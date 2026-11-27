// Role journeys: how each real user works through Levigate, against a running demo stack (synthetic data).
//   npm run e2e:journeys      (LEVIGATE_WEB=http://127.0.0.1:5173 by default; reset the demo first)
//
// Every role signs in, sees only its own modules, does its everyday work end to end, and is refused where its role
// stops. Every screen visited is scanned with axe-core (WCAG 2.1 A and AA) in light and dark themes. Fails on any
// accessibility violation, console error, 5xx response, missing UI or PHI shown to a role that must not see it.
import { mkdirSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const AXE = readFileSync(require.resolve("axe-core/axe.min.js"), "utf8");
const WEB = process.env.LEVIGATE_WEB ?? "http://127.0.0.1:5173";
const SHOTS = process.env.SHOTS ?? "./e2e/shots/journeys";
mkdirSync(SHOTS, { recursive: true });

const problems = [];
const a11y = new Map(); // screen -> violations
const browser = await chromium.launch();
// Reduced motion: colour transitions finish instantly, so the contrast scan measures settled colours.
const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, acceptDownloads: true, reducedMotion: "reduce" });
const page = await context.newPage();
page.on("console", (m) => m.type() === "error" && problems.push(`console: ${m.text()}`));
page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
page.on("response", (r) => r.status() >= 500 && problems.push(`HTTP ${r.status()} ${r.url()}`));
// Every API response a role receives, for the "never patient data" checks.
let seen = [];
page.on("response", async (r) => {
  if (!r.url().includes("/api/") || !(r.headers()["content-type"] ?? "").includes("json")) return;
  try {
    seen.push({ url: r.url(), body: await r.text() });
  } catch {
    /* body unavailable after navigation */
  }
});

const NAV = {
  "Jamie Patel": ["My queue", "Claims", "Prescribers", "Results"],
  "Ravi Kumar": ["My queue", "Claims", "Prescribers", "Results"],
  "Priya Shah": ["My queue", "Claims", "Call lists", "Prescribers", "Results"],
  "Ana Ruiz": ["My queue", "Call lists", "Results"],
  "Marcus Lee": ["My queue", "Prescribers", "Results"],
  "Sam Okafor": ["My queue", "Claims", "Prescribers", "Results", "Compliance", "Admin"],
  "Alex Chen": ["My queue", "Claims", "Prescribers", "Results", "Compliance", "Admin"],
  "Dana Whitfield": ["Results", "Compliance"],
};
const PHONE = /\(\d{3}\) \d{3}-\d{4}/;
let current = "";

const shot = (name) => page.screenshot({ path: `${SHOTS}/${name}.png` });
async function step(name, fn) {
  try {
    await fn();
    console.log(`ok   ${current} · ${name}`);
  } catch (err) {
    problems.push(`${current} · ${name}: ${err.message.split("\n")[0]}`);
    console.log(`FAIL ${current} · ${name}`);
    await shot(`fail-${current}-${name}`.replace(/\W+/g, "-"));
  }
}

async function scan(screen) {
  await page.waitForLoadState("networkidle");
  for (const theme of ["light", "dark"]) {
    await page.evaluate((t) => document.documentElement.setAttribute("data-theme", t), theme);
    await page.waitForTimeout(150);
    if (!(await page.evaluate(() => "axe" in window))) await page.addScriptTag({ content: AXE });
    const result = await page.evaluate(async () => {
      const r = await window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] } });
      return r.violations.map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length, sample: v.nodes[0]?.target?.join(" ") }));
    });
    for (const v of result) {
      const key = `${screen} (${theme})`;
      a11y.set(key, [...(a11y.get(key) ?? []), v]);
    }
  }
  await page.evaluate(() => document.documentElement.removeAttribute("data-theme"));
}

async function signInAs(name) {
  current = name.split(" ")[0];
  seen = [];
  await page.goto("about:blank"); // leave the previous user's page first, so no request of theirs lands signed out
  await context.clearCookies();
  await page.goto(WEB);
  await page.getByRole("button", { name: new RegExp(name) }).click();
  await page.getByRole("navigation", { name: "Modules" }).first().waitFor();
  const nav = page.getByRole("navigation", { name: "Modules" }).first();
  const items = (await nav.getByRole("link").allInnerTexts()).map((t) => t.trim()).filter(Boolean);
  const expected = NAV[name];
  if (JSON.stringify(items) !== JSON.stringify(expected)) {
    throw new Error(`${name} sees modules ${JSON.stringify(items)}, expected ${JSON.stringify(expected)}`);
  }
}

async function refused(path) {
  await page.goto(`${WEB}${path}`);
  await page.getByText("Your role doesn't include this area.").waitFor();
}

// The screen shows no patient identifiers, and no API response the role received carried any (names, phones, DOB).
async function noPatientData(where, { screenPhones = true } = {}) {
  await page.waitForLoadState("networkidle");
  const text = await page.locator("main").innerText();
  if (/\bPT\d{5}\b/.test(text) || /\bDOB\b/.test(text) || (screenPhones && PHONE.test(text))) throw new Error(`patient data shown on ${where}`);
  for (const r of seen) {
    if (/"(first_name|last_name|dob|DOB|FIRST_NAME|PHONE)"\s*:/.test(r.body) && /\/api\/(patients|outreach|queue|work-items)/.test(r.url)) {
      throw new Error(`patient fields in ${r.url} on ${where}`);
    }
    if (/\/api\/prescribers/.test(r.url) && /"(patient|patient_id|PATIENT_ID)"\s*:|\bPT\d{5}\b/.test(r.body)) {
      throw new Error(`patient data in ${r.url} on ${where}`);
    }
  }
}

// ---------------------------------------------------------------------------------------------------------------
await step("sign-in page", async () => {
  current = "anonymous";
  await page.goto(WEB);
  await page.getByText("Choose a demo user").waitFor();
  await scan("Sign in");
});

// Billing specialist (US): works rejections and unpaid claims, exports for the payer call.
await step("billing: modules", () => signInAs("Jamie Patel"));
await step("billing: queue by type", async () => {
  await page.getByText("Dollars at risk").waitFor();
  await scan("My queue (billing)");
  const filter = page.getByRole("group", { name: "Filter by type" });
  await filter.getByRole("button", { name: /Unpaid claim/ }).click();
  if ((await filter.getByRole("button", { name: /Unpaid claim/ }).getAttribute("aria-pressed")) !== "true") throw new Error("filter not applied");
  await page.locator("tbody tr").first().waitFor();
  await shot("billing-unpaid");
});
await step("billing: work an unpaid claim", async () => {
  await page.locator("tbody tr").first().click();
  await page.getByRole("heading", { name: /Claim/ }).first().waitFor();
  await scan("Work item");
  await page.getByRole("button", { name: "Reveal" }).click();
  await page.getByText(PHONE).first().waitFor();
  await page.getByText(/DOB \d{4}-\d{2}-\d{2}/).waitFor();
});
await step("billing: claims tracker and CSV export", async () => {
  await page.goto(`${WEB}/claims`);
  await page.locator("tbody tr").first().waitFor();
  await scan("Claims");
  await page.getByRole("button", { name: /^Rejected/ }).click();
  await page.locator("tbody tr").first().waitFor();
  await page.getByRole("button", { name: "Export CSV" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.waitFor();
  const [download] = await Promise.all([page.waitForEvent("download"), dialog.getByRole("button", { name: /Export/ }).click()]);
  if (!download.suggestedFilename().endsWith(".csv")) throw new Error(`unexpected export ${download.suggestedFilename()}`);
});
await step("billing: refused outside the role", async () => {
  await refused("/compliance");
  await refused("/admin");
  await refused("/outreach");
});

// Offshore billing (India, no PHI approval): same queue, masked patients, no reveal.
await step("offshore: modules", () => signInAs("Ravi Kumar"));
await step("offshore: patients stay masked", async () => {
  await page.locator("tbody tr").first().waitFor();
  await noPatientData("offshore queue");
  await page.locator("tbody tr").first().click();
  await page.getByRole("heading", { name: /Claim/ }).first().waitFor();
  if (await page.getByRole("button", { name: "Reveal" }).count()) throw new Error("offshore user is offered Reveal");
  await noPatientData("offshore work item");
});

// Pharmacist: clinical rejections routed to them, plus the call lists.
await step("pharmacist: modules", () => signInAs("Priya Shah"));
await step("pharmacist: queue and call lists", async () => {
  await page.getByText("Dollars at risk").waitFor();
  await page.locator("tbody tr").first().waitFor();
  await page.goto(`${WEB}/outreach`);
  await page.getByText("Call script").waitFor();
});

// Outreach coordinator: four call lists, consent-aware, outcome logging.
await step("outreach: modules", () => signInAs("Ana Ruiz"));
await step("outreach: every call list", async () => {
  await page.goto(`${WEB}/outreach`);
  const lists = page.getByRole("tablist", { name: "Call lists" });
  for (const name of [/Refill due/, /Adherence gap/, /Ready for pickup/, /Balance due/]) {
    await lists.getByRole("tab", { name }).click();
    await page.getByText("Call script").waitFor();
    await page.locator("tbody tr").first().waitFor();
  }
  await scan("Call lists");
  await lists.getByRole("tab", { name: /Refill due/ }).click();
  await page.getByRole("heading", { name: /Not on today/ }).waitFor();
});
await step("outreach: reveal, call, log the outcome", async () => {
  await page.getByText("Call script").waitFor();
  await page.getByRole("button", { name: "Reveal" }).first().click();
  await page.getByText(PHONE).first().waitFor();
  await page.getByRole("button", { name: "Call", exact: true }).click();
  await page.getByText("Call in progress").waitFor();
  await page.getByRole("group", { name: "Call outcome" }).getByRole("button", { name: "Voicemail" }).click();
  await page.getByText("Outcome logged.").waitFor();
});
await step("outreach: refused outside the role", async () => {
  await refused("/claims");
  await refused("/prescribers");
});

// Marketing: prescribers only, never patient data.
await step("marketing: modules", () => signInAs("Marcus Lee"));
await step("marketing: ranked prescribers, no patient data", async () => {
  await page.goto(`${WEB}/prescribers`);
  await page.locator("tbody tr").first().waitFor();
  await scan("Prescribers");
  await noPatientData("prescriber list");
  await page.getByLabel("Filter by name or practice").fill("Marsh");
  await page.getByRole("link", { name: /Elena Marsh/ }).click();
  await page.getByText("Claim friction (90 days, aggregate)").waitFor();
  await scan("Prescriber profile");
  // The prescriber's own office phone is shown; patient data must not be
  await noPatientData("prescriber profile", { screenPhones: false });
});
await step("marketing: log a visit", async () => {
  await page.getByLabel("Outcome").fill("Dropped off compounding formulary");
  await page.getByRole("button", { name: "Log contact" }).click();
  await page.getByText("Dropped off compounding formulary").first().waitFor();
});
await step("marketing: refused outside the role", async () => {
  await refused("/claims");
  await refused("/outreach");
  await page.goto(`${WEB}/results`);
  await page.getByText("Dollars recovered per week").waitFor();
  await scan("Results");
});

// Admin: the client's data mapping, data checks, BI comparison, sync.
await step("admin: modules", () => signInAs("Sam Okafor"));
let source = null; // the active mapping, as the admin screen's API reports it
await step("admin: data source mapping", async () => {
  await page.goto(`${WEB}/admin?tab=data`);
  await page.getByText("7 of 7").waitFor();
  source = await (await page.request.get(`${WEB}/api/admin/data-source`)).json();
  await scan("Admin · Data source");
  if (source.helpers.length) {
    await page.getByRole("row", { name: /FACT_CLAIM/ }).getByRole("button", { name: "View mapping" }).click();
    await page.getByRole("dialog").getByText(/SELECT/).first().waitFor();
    await page.keyboard.press("Escape");
  } else {
    // Reference layout: the warehouse already holds the relations, read directly
    if ((await page.getByText("Read directly").count()) !== 7) throw new Error("expected 7 relations read directly");
  }
});
await step("admin: run data checks", async () => {
  await page.getByRole("button", { name: "Run checks now" }).click();
  const results = page.getByRole("list", { name: "Data check results" });
  await results.waitFor();
  await results.getByText("Fills that appear more than once after mapping").waitFor();
  if (source?.helpers.length) await results.getByText(/Duplicate fill rows from reloads/).waitFor();
  if (await results.getByText(/Failing|Could not run/).count()) throw new Error("a data check fails on the demo data");
});
await step("admin: compare with the client's BI", async () => {
  if (source?.bi_views.length) {
    await page.getByRole("button", { name: "Compare" }).click();
    await page.getByText("Every difference is explained.").waitFor({ timeout: 20_000 });
  } else {
    if (!(await page.getByRole("button", { name: "Compare" }).isDisabled())) throw new Error("Compare should be off without BI views");
    await page.getByText(/This mapping names no BI views/).waitFor();
  }
  await shot("admin-data-source");
});
await step("admin: sync and full re-read", async () => {
  await page.goto(`${WEB}/admin?tab=sync`);
  await page.getByRole("button", { name: "Run sync" }).click();
  await page.getByText(/Created \d+, re-routed/).waitFor({ timeout: 30_000 });
  await page.getByRole("button", { name: "Full re-read" }).click();
  await page.getByRole("cell", { name: "Full" }).first().waitFor({ timeout: 30_000 });
  await scan("Admin · Sync");
});
await step("admin: propose a catalog change", async () => {
  await page.goto(`${WEB}/admin?tab=catalog`);
  await page.getByRole("row", { name: /Refill too soon/ }).filter({ hasText: "Current" }).first().getByRole("button", { name: "Open" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Why are you changing it?").fill("Journey test: confirm two-person approval");
  await dialog.getByRole("button", { name: "Propose change" }).click();
  await page.getByText(/Proposed|waiting for a second admin|pending/i).first().waitFor();
  await page.keyboard.press("Escape");
});
await step("admin 2: approves the change (two people)", async () => {
  await signInAs("Alex Chen");
  await page.goto(`${WEB}/admin?tab=catalog`);
  await page.getByRole("row", { name: /Refill too soon/ }).filter({ hasText: "Waiting for approval" }).getByRole("button", { name: "Open" }).click();
  await page.getByRole("dialog").getByRole("button", { name: /Approve v\d+/ }).click();
  await page.getByText("Approved. The new version is current and audited.").waitFor();
});

// Auditor: compliance evidence only.
await step("auditor: modules", () => signInAs("Dana Whitfield"));
await step("auditor: every compliance tab", async () => {
  await page.getByText("Compliance Center").first().waitFor();
  for (const tab of ["Controls", "Approvals", "Consent", "Access review", "AI governance", "Vendors & BAAs", "Alerts", "Audit log"]) {
    await page.getByRole("tab", { name: tab }).click();
    await page.waitForLoadState("networkidle");
    await scan(`Compliance · ${tab}`);
  }
  await page.getByRole("button", { name: "Verify hash chain" }).click();
  await page.getByText(/Chain intact/).waitFor();
});
await step("auditor: audit pack download", async () => {
  await page.getByRole("button", { name: "Audit pack" }).click();
  const dialog = page.getByRole("dialog");
  const [download] = await Promise.all([page.waitForEvent("download"), dialog.getByRole("button", { name: "Download" }).click()]);
  if (!/^levigate-audit-pack-.*\.zip$/.test(download.suggestedFilename())) throw new Error(`unexpected audit pack ${download.suggestedFilename()}`);
});
await step("auditor: refused outside the role", async () => {
  await refused("/claims");
  await refused("/admin");
  await refused("/prescribers");
});

await browser.close();
for (const [screen, violations] of a11y) {
  for (const v of violations) problems.push(`a11y ${screen}: ${v.id} (${v.impact}, ${v.nodes} node${v.nodes === 1 ? "" : "s"}) e.g. ${v.sample}`);
}
if (problems.length) {
  console.error(`\n${problems.length} problem(s):\n- ${problems.join("\n- ")}`);
  process.exit(1);
}
console.log(`\nAll role journeys passed; no WCAG 2.1 A/AA violations on any screen scanned (light and dark).`);
