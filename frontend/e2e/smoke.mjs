// End-to-end smoke test against a running demo stack (API on :8000, web on :5173, synthetic data).
//   npm run e2e            (LEVIGATE_WEB=http://127.0.0.1:5173 SHOTS=./e2e/shots by default)
// Walks the demo script role by role and fails on console errors, 5xx responses or missing UI.
import { mkdirSync } from "node:fs";
import { createRequire } from "node:module";

const { chromium } = createRequire(import.meta.url)("playwright");
const WEB = process.env.LEVIGATE_WEB ?? "http://127.0.0.1:5173";
const SHOTS = process.env.SHOTS ?? "./e2e/shots";
const SCALE = Number(process.env.SCALE ?? 1);
mkdirSync(SHOTS, { recursive: true });

const problems = [];
const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: SCALE });
const page = await context.newPage();
page.on("console", (m) => m.type() === "error" && problems.push(`console: ${m.text()}`));
page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
page.on("response", (r) => r.status() >= 500 && problems.push(`HTTP ${r.status()} ${r.url()}`));

const shot = (name) => page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: false });
const step = async (name, fn) => {
  try {
    await fn();
    console.log(`ok   ${name}`);
  } catch (err) {
    problems.push(`${name}: ${err.message.split("\n")[0]}`);
    console.log(`FAIL ${name}`);
    await shot(`fail-${name.replace(/\W+/g, "-")}`);
  }
};
async function signInAs(name) {
  await context.clearCookies();
  await page.goto(WEB);
  await page.getByRole("button", { name: new RegExp(name) }).click();
  await page.getByRole("navigation", { name: "Modules" }).first().waitFor();
}

await step("sign-in page", async () => {
  await page.goto(WEB);
  await page.getByText("Choose a demo user").waitFor();
  await shot("01-sign-in");
});

await step("billing queue", async () => {
  await signInAs("Jamie Patel");
  await page.getByText("Dollars at risk").waitFor();
  await page.locator("tbody tr").first().waitFor();
  await shot("02-billing-queue");
});

await step("claims work item + AI explanation", async () => {
  await page.keyboard.press("/");
  await page.keyboard.type("4471");
  await page.getByRole("button", { name: /Rx 4471-02.*Open work item/ }).click();
  await page.getByText("Rule-based fix").waitFor();
  await page.getByText("Resubmit on 12 Dec", { exact: true }).waitFor();
  await page.getByText(/Rule 79 v3 recommends/).waitFor({ timeout: 10_000 });
  await shot("03-work-item");
});

await step("approve sequence (never optimistic)", async () => {
  await page.getByRole("button", { name: "Approve resubmission" }).click();
  await page.getByRole("dialog").getByText("can’t be undone").waitFor();
  await shot("04-confirm");
  await page.getByRole("button", { name: "Approve and log" }).click();
  await page.getByText(/Approved · resubmission set for 12 Dec/).waitFor();
  await page.getByText(/Audit trail: approved by J\. Patel, rule 79 v3/).waitFor();
  await shot("05-approved");
  await page.getByRole("button", { name: "Stay here" }).click();
});

await step("claims tracker", async () => {
  await page.goto(`${WEB}/claims`);
  await page.getByRole("button", { name: /^Rejected/ }).waitFor();
  await page.locator("tbody tr").first().waitFor();
  await shot("06-claims");
});

await step("outreach call list + call outcome", async () => {
  await signInAs("Ana Ruiz");
  await page.goto(`${WEB}/outreach`);
  await page.getByRole("heading", { name: /Not on today/ }).waitFor();
  await page.getByText(/No call consent on file|opted out/).first().waitFor();
  await page.getByText("Call script").waitFor();
  await page.getByText(/may I speak with/).waitFor({ timeout: 10_000 });
  await shot("07-call-list");
  await page.getByRole("button", { name: "Call", exact: true }).click();
  await page.getByText("Call in progress").waitFor();
  await page.getByRole("button", { name: "Refill confirmed" }).click();
  await page.getByText("Outcome logged.").waitFor();
});

await step("prescriber CRM: declining story", async () => {
  await signInAs("Marcus Lee");
  await page.goto(`${WEB}/prescribers?segment=declining`);
  await page.getByRole("link", { name: /Elena Marsh/ }).click();
  await page.getByText("Claim friction (90 days, aggregate)").waitFor();
  await page.getByRole("button", { name: "Visit brief" }).click();
  await page.getByLabel("Edit draft").waitFor({ timeout: 10_000 });
  await shot("08-prescriber");
});

await step("results dashboard + ask", async () => {
  await signInAs("Sam Okafor");
  await page.goto(`${WEB}/results`);
  await page.getByText("Dollars recovered per week").waitFor();
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await page.getByText(/rejected the most claims/).waitFor();
  await shot("09-results");
});

await step("admin: business catalog", async () => {
  await page.goto(`${WEB}/admin?tab=catalog`);
  await page.getByText("Refill too soon").waitFor();
  await shot("10-admin-catalog");
});

await step("compliance center (auditor)", async () => {
  await signInAs("Dana Whitfield");
  await page.getByText("Compliance Center").first().waitFor();
  await page.getByText("C-06").waitFor();
  await page.getByText(/Hash chain verified/).waitFor();
  await shot("11-compliance");
  await page.getByRole("tab", { name: "Audit log" }).click();
  await page.getByRole("button", { name: "Verify hash chain" }).click();
  await page.getByText(/Chain intact/).waitFor();
  await shot("12-audit-log");
});

await step("dark theme", async () => {
  await page.getByRole("button", { name: "Use dark theme" }).click();
  await page.getByRole("tab", { name: "Controls" }).click();
  await page.getByText("C-06").waitFor();
  await shot("13-dark");
});

await browser.close();
if (problems.length) {
  console.error(`\n${problems.length} problem(s):\n- ${problems.join("\n- ")}`);
  process.exit(1);
}
console.log("\nSmoke test passed.");
