# Levigate — Architecture and Build Requirements

Nov 27, 2026 · @Sutharsan

## Overview

We will build one platform that closes the compounding pharmacy's revenue loop: every filled Rx gets paid, every eligible patient gets a timely refill call, and every prescriber is tracked and nurtured. It reads the pharmacy's existing data, keeps its own workflow database, and uses AI only where rules can't do the job.

**Product name:** Levigate, by GainInsights. **Tagline:** Every script, smoothed to paid. Brand mark and palette: [Levigate brand sheet](brand/README.md).

**Problem.** Today the data to act on exists (fills, claims, payment cycle, call logs) but the follow-up is manual. Rejected and unpaid claims sit in reports, refill calls depend on staff memory, and the marketing team has no ranked list of which prescribers to call.

**Product: five modules on one data layer**

1. **Claims tracker:** follows every filled Rx from fill to paid; flags rejected, unpaid and underpaid claims.
2. **Insurance follow-up agent:** reads the reject code, proposes the fix, routes it for human approval, and tracks the follow-up with the plan or prescriber.
3. **Patient outreach:** prioritized call lists for refills, adherence, ready-for-pickup and balances, with AI-drafted scripts and logged outcomes.
4. **CRM (replaces HubSpot in phases):** prescriber and patient-relationship records, ranked lists, calls, visits, tasks and campaigns, scored from Rx data instead of copied in by Talend.
5. **Compliance Center:** an admin space that proves the controls are working: access, consent, approvals, audit evidence and an exportable audit pack.

**Goals for the pilot**

- Recover revenue from rejected and unpaid claims, and shorten days-to-payment.
- Raise the refill capture rate for eligible patients.
- Give marketing a working prescriber pipeline with every call logged.
- Be HIPAA- and TCPA-ready from day one, with a clean, easy UI.

**In scope (pilot):** one pharmacy client, read-only access to its PMS data through the existing Snowflake warehouse, the four modules above, human-in-the-loop for every claim change.

**Out of scope (pilot):** writing back to the PMS, prescription intake and data entry, our own PMS (phase 3), automated voice calling to patients, email campaigns (HubSpot keeps these until phase 2), multi-tenant SaaS packaging.

## Market landscape: one platform for what others sell separately

A compounding pharmacy today buys five or six separate tools that don't share data; our platform brings them into one space on the pharmacy's own warehouse, with one login, one audit trail and one set of metrics.

| Capability | Who sells it today (examples) | What it does | Our module | Phase |
| --- | --- | --- | --- | --- |
| Claims and rejection follow-up | [CoverMyMeds](https://covermymeds.health/our-solutions/pharmacy-operations/pharmacy-operational-efficiencies), PMS add-ons | Starts prior auths when a claim rejects; rejection queues | Claims tracker + insurance follow-up agent | 1 |
| Prescriber CRM | [Dotti (Atrium24)](https://www.ipcrx.com/wp-content/uploads/2025/08/A24-DOTTI-Sell_Sheet-IPC_Members-072925.pdf), HubSpot | Prescriber records, outreach, profitability by niche | CRM (replaces HubSpot) | 1–2 |
| Patient outreach | PMS refill modules, call centres | Refill reminders, adherence, pickup calls | Patient outreach | 1 |
| AI phone receptionist | [Frontdesk](https://www.myaifrontdesk.com/industries/compounding-pharmacy), [Novoflow](https://ai.novoflow.io/tool-compounding-pharmacy-orders-auto) | Answers calls, refill requests, order intake by voice | Voice agent (consent-gated) | 2 |
| Rx intake automation | [TJM Labs](https://www.tjmlabs.com/solutions/compounding) | Reads faxes and e-Rx, types them into the PMS | Intake assistant | 2 |
| Compounding compliance | [Pharmacy Compounding AI](https://online.flippingbook.com/view/583364154/2), [StackAI](https://stackai-git-toni-test2.stack-ai.com/solutions/compounding-pharmacies) | Compliance documentation, batch record and QA checks | Compliance Center + batch-record checks | 2 |
| Pharmacy management system | PK, PrimeRx, [SiCompounding](https://www.capterra.com/p/10025218/SiCompounding/) | Rx entry, billing, inventory, dispensing | Our own PMS | 3 |

**Can we do it?** Yes, in sequence. Phase 1 modules use data and skills we already have. Phase 2 adds AI workflows the specialists have proven work. Phase 3 (a full PMS) is the biggest build and needs pharmacy-system certification work, so it comes only after the platform is in daily use.

**How we win:** not by beating each specialist on depth (TJM Labs alone [raised $100M in July 2026](https://simplify.jobs/c/TJM-Labs)), but by being the one place where claims, patients, prescribers and compliance share the same data and the same audit trail. No point tool can show "this prescriber's patients have the most rejected claims" or "this outreach call was consented and approved."

## Architecture

The platform sits beside the pharmacy's existing systems: it reads client data through one connector layer, the rules engine decides what work to create, the AI explains and drafts, and every action passes through an audited API.

&#91;embedded content: platform architecture · 5 layers\]

Prescriptions are still typed into the PMS by pharmacy staff, and claims still go to payers from the PMS. We never retype or resubmit from our side in the pilot.

**Layers and stack**

| Layer | What it does | Proposed tech |
| --- | --- | --- |
| Client sources | PMS data, already copied to Snowflake by Talend | Existing (PK, Talend, Snowflake) |
| Connector layer | Read-only scheduled pulls and on-demand reads | Python, SQLAlchemy, Azure Key Vault |
| Rules engine | Runs the business catalog; creates and routes work items | Python service, rules stored in Postgres |
| AI service | Explanations, scripts, email drafts, summaries | Azure OpenAI under BAA, or a local model on a private VM |
| App database | Workflow state, outcomes, consent, catalog, audit log | Azure Database for PostgreSQL |
| API | Auth, role checks, approvals, audit logging | FastAPI (async), SSO via the client's identity provider |
| Web UI | Queues, call lists, CRM, admin | React + TypeScript, component library (see UI requirements) |
| Telephony | Click-to-call and call status | A BAA-covered provider chosen with the client |
| Reporting | Results and trends | Qlik on the app DB and warehouse |

**Data flow by module**

- **Claims tracker:** warehouse → connector → rules engine flags unpaid/underpaid → work items in app DB → Claims queue.
- **Insurance follow-up:** rejection → playbook rule → work item with suggested fix → AI explanation → user approves → outcome logged.
- **Patient outreach:** eligibility rules + consent → daily call list → click-to-call → outcome and opt-outs written back to app DB.
- **Prescriber CRM:** aggregated Rx volume and revenue → scoring rule → ranked lists → calls, visits and notes logged.

## Data connectivity

The platform connects to any client database through a connector layer: it **reads** from client sources with read-only accounts and **writes** only to its own app database in the pilot. Write-back to client systems is a phase 2 feature, behind approval.

| Connection | Mode | What it is used for |
| --- | --- | --- |
| Snowflake (client warehouse) | Read | Fills, claims, reject codes, patients, prescribers, payment cycle |
| PMS replica or reporting DB (e.g. PK) | Read | Fresher claim status when the warehouse lags |
| SQL Server / Azure SQL / Postgres / MySQL / Oracle | Read | Other client sources, configured per client |
| App database (Postgres) | Read + write | Work queues, follow-up status, call outcomes, notes, consent, audit log |
| Telephony provider | Read + write | Click-to-call, call status and duration, opt-outs |
| Client PMS / CRM write-back | Write (phase 2) | Push notes or status back, only after human approval |

**Connector requirements**

- One connector interface (SQLAlchemy-based) so adding a database type is configuration, not a rebuild.
- Admins add a connection in the UI: type, host, database, schema, auth method. Secrets live in a vault (e.g. Azure Key Vault), never in code or the app DB.
- Every client connection uses a **read-only service account** scoped to the tables we need.
- Queries are parameterized and validated; no free-form SQL from users or the LLM reaches a client database.

**Sync model**

- **Scheduled pull** (every 15 minutes, configurable): new fills, new rejections, claim status changes → creates or updates work items.
- **On-demand read**: when a user opens a record, details are fetched live from the source, so the app DB stores IDs and workflow state, not a second copy of patient data.
- **Daily reconciliation**: work items are re-checked against source status; anything paid or reversed upstream closes automatically.

## How the AI knows our business logic

The LLM does not learn our logic from the database by itself. We write the logic down once in a **business catalog** (a semantic layer stored in the app DB), the rules engine executes it, and the LLM only reads the relevant entries to explain and draft. Logic stays deterministic, versioned and auditable.

**What the catalog holds**

| Catalog part | Example entry | Who maintains it |
| --- | --- | --- |
| Data dictionary | `FACT_RXFILL.RX_NUMBER` = prescription number; joins to claims on Rx number + fill number | Data team |
| Metric definitions | Unpaid claim = no paid response N days after fill (N set per payer) | Billing lead, approved by client |
| Reject-code playbook | 79 Refill too soon → compute next eligible date from last fill and days supply → schedule resubmission task | Billing lead + pharmacist |
| Outreach eligibility rules | Refill call if: due within 7 days, consent on file, not opted out, Rx not lapsed over 90 days | Compliance + operations |
| Prescriber scoring | Rank by 90-day Rx volume, revenue and trend vs prior 90 days | Marketing lead |
| Business glossary | "Paid" = adjudicated and remitted; "Captured" = accepted, not yet remitted | Client SMEs |

**How a request flows**

1. The scheduled pull finds a rejected claim with code 79.
2. The rules engine looks up code 79 in the playbook and computes the next eligible date in code, not in the LLM.
3. It creates a work item: "Resubmit on 12 Dec", assigned to billing.
4. When a user opens it, the LLM receives the rule text, the computed result and the minimum claim fields, and writes a plain-English explanation and the next step.
5. The user approves or edits; the outcome is logged against the rule version that produced it.

**What the LLM may and may not do**

| The LLM may | The LLM may not |
| --- | --- |
| Explain a rule's result in plain English | Decide a claim fix or approve a resubmission |
| Draft call scripts, prescriber emails, claim-history summaries | Write free-form SQL against client databases |
| Answer questions by calling approved, named queries (tools) from the catalog | Invent a metric or rule that is not in the catalog |
| Suggest a catalog entry for a reject code it hasn't seen, flagged for human review | Receive full patient records when a few fields will do |

**Keeping it accurate**

- Billing lead and a pharmacist sign off the first catalog in weeks 1–2; every later change goes through an admin screen, versioned and audited.
- Unknown reject codes land in a review queue instead of guessing.
- A test set of real (de-identified) claims checks every rule change before release; target 95%+ correct routing on known codes.
- Phase 2: natural-language questions over the data ("which payers rejected most this month?") use the same catalog plus a SQL validator, the pattern already proven in QueryBot.

## Compliance controls (day one)

Every control below is a build requirement for the pilot, not a later hardening task. The client's compliance or legal team signs off this list before any PHI is connected.

| ID | Control | Requirement |
| --- | --- | --- |
| C-01 | BAA chain | Signed BAAs: client ↔ GainInsights ↔ every vendor touching PHI (cloud, LLM endpoint, telephony, email). No BAA, no PHI. |
| C-02 | Hosting | US-region, HIPAA-eligible services (e.g. Azure); PHI never leaves the client-approved environment. |
| C-03 | Encryption | TLS 1.2+ in transit; encryption at rest for app DB, backups, logs and file storage. |
| C-04 | Identity | SSO with MFA (client's identity provider or Entra ID); no shared accounts; session timeout after 15 minutes idle. |
| C-05 | Minimum necessary (RBAC) | Roles: Billing, Outreach, Marketing, Pharmacist, Admin, Auditor. Marketing sees prescriber data and aggregate counts, not patient clinical details. |
| C-06 | Audit trail | Log every view, edit, export, call and AI action: who, what record, when, from where. Append-only, retained per client policy. |
| C-07 | AI data controls | LLM via a BAA-covered endpoint or local model; send only the fields a task needs; no training on client data; prompts and responses treated and stored as PHI. |
| C-08 | Human approval | No claim change, resubmission or patient message goes out without a named user's approval, recorded in the audit log. |
| C-09 | Patient consent (TCPA) | Consent flag, channel and date per patient; automated or prerecorded calls only with prior express consent; opt-outs honoured within one business day and synced back. |
| C-10 | Refill-reminder scope (HIPAA) | Patient lists only include drugs currently prescribed; Rx lapsed over 90 days drops out automatically; no messages promoting a switch to another therapy. |
| C-11 | Offshore access | Configurable: PHI fields masked for users outside the US unless the client approves. Decide before build starts. |
| C-12 | Exports | Exports restricted by role, watermarked with user and time, and logged. |
| C-13 | Incident response | Documented breach-response process with client contacts; alerting on unusual access (bulk views, off-hours exports). |
| C-14 | Policies and testing | HIPAA risk assessment before go-live; annual penetration test; builds on GainInsights' ISO 27001 controls. |

Rule sources: [HHS refill reminder exception](https://www.hhs.gov/hipaa/for-professionals/faq/what-types-of-communications-fall-within-the-refill-reminder-exception/index.html), [HHS on lapsed prescriptions (90 days)](https://www.hhs.gov/hipaa/for-professionals/faq/do-communications-about-recently-lapsed-prescriptions-fall-within-the-refill-reminder-exception/index.html), [HHS on alternative therapy](https://www.hhs.gov/hipaa/for-professionals/faq/do-communications-encouraging-individuals-to-switch-to-alternative-therapy-fall-within-the-refill-reminder-exception/index.html), [TCPA and refill reminders (DWT)](https://www.dwt.com/insights/2014/12/refill-reminders-and-the-tcpa). These are design inputs, not legal advice.

## Security: API and product work

Every API endpoint and background job is built against the [OWASP API Security Top 10 (2023)](https://owasp.org/www-project-api-security/) and every AI feature against the [OWASP Top 10 for LLM Applications (2025)](https://invicti.com/blog/web-security/owasp-top-10-risks-llm-security-2025); each row below is a build requirement with an automated test.

**API security (OWASP API Top 10, 2023)**

| Risk | Levigate control |
| --- | --- |
| API1 Broken object level authorization | Every request for a claim, patient or prescriber checks the caller may see THAT record (tenant + role + assignment), in one shared policy layer; tests try other users' IDs |
| API2 Broken authentication | SSO via OIDC with MFA; short-lived access tokens (15 min) and rotating refresh tokens; no API keys for users |
| API3 Broken object property level authorization | Response models per role (Pydantic): marketing never receives patient fields; no mass assignment on updates |
| API4 Unrestricted resource consumption | Rate limits per user and per tenant; page-size caps; query timeouts; token budgets on AI calls |
| API5 Broken function level authorization | Admin, approval and export endpoints check role server-side, never by hiding buttons |
| API6 Unrestricted access to sensitive business flows | Approvals, exports and bulk call lists need step-up confirmation, idempotency keys and anomaly alerts |
| API7 Server-side request forgery | Connectors only reach allow-listed hosts; no user-supplied URLs fetched by the server |
| API8 Security misconfiguration | Hardened defaults: TLS only, security headers and CSP, CORS locked to our domain, debug off, infra as code reviewed |
| API9 Improper inventory management | OpenAPI spec generated from code; every endpoint versioned and listed; old versions retired on a date |
| API10 Unsafe consumption of APIs | Responses from PMS, telephony and LLM providers validated against schemas and treated as untrusted |

**AI security (OWASP LLM Top 10, 2025)**

| Risk | Levigate control |
| --- | --- |
| Prompt injection | Fax text, notes and emails are passed as data with clear delimiters; the AI has no tools that change anything |
| Sensitive information disclosure | Field allow-list per AI task; PHI redacted from logs; BAA-covered endpoint; no training on client data |
| Improper output handling | AI text is escaped in the UI and never executed as SQL, code or HTML |
| Excessive agency | AI drafts and explains only; every action needs a named human approval (C-08) |
| System prompt leakage | No secrets, rules or IDs that grant access are placed in prompts |
| Unbounded consumption | Per-user and per-tenant limits on AI calls and tokens, with alerts on spikes |

**Product work and delivery**

- Threat model for every new feature before build; security review in the definition of done.
- CI pipeline: static analysis, dependency and secret scanning, container image scanning; builds fail on high-severity findings.
- Background jobs run under their own least-privilege service identities and write to the audit log like users do.
- Audit log is append-only and hash-chained, so any tampering is detectable.
- Sensitive identifiers (e.g. member IDs) encrypted at field level in the app DB.
- Penetration test before go-live and yearly; findings tracked in the Compliance Center.

## Functional requirements

Requirements are grouped by module; each has an ID the build team and testers use. "Must" items are in the pilot; "Should" items are in if time allows.

### 1. Claims tracker

| ID | Priority | Requirement | Acceptance criteria |
| --- | --- | --- | --- |
| CT-01 | Must | Show every filled Rx with its claim status: paid, rejected, pending, reversed, unpaid past due | Counts match the source warehouse for the same date range |
| CT-02 | Must | Flag unpaid claims past the payer's expected payment window | Window configurable per payer in the catalog |
| CT-03 | Must | Flag underpaid claims (paid below expected amount by a set threshold) | Threshold configurable; flagged items open a work item |
| CT-04 | Must | Filter by payer, reject code, date, pharmacy location, amount | Filters combine; results load in under 2 seconds |
| CT-05 | Should | Show claim history timeline per Rx (submissions, rejections, resubmissions, payment) | Every event shows its source and time |

### 2. Insurance follow-up agent

| ID | Priority | Requirement | Acceptance criteria |
| --- | --- | --- | --- |
| IF-01 | Must | Auto-create a work item for every new rejection, routed by reject-code playbook | New rejections appear within one sync cycle |
| IF-02 | Must | Show the rule-based fix and an AI explanation of why | Explanation cites the playbook entry and rule version |
| IF-03 | Must | Require user approval before marking a fix applied or resubmitted | No status change without an approving user in the audit log |
| IF-04 | Must | Track follow-ups with plan or prescriber: due date, owner, attempts, outcome | Overdue items surface at the top of the queue |
| IF-05 | Must | Route unknown reject codes to a review queue | No unknown code gets an automated suggestion |
| IF-06 | Should | Draft prescriber fax or message for missing info (PA, quantity, days supply) | Draft editable; sent only after approval |

### 3. Patient outreach

| ID | Priority | Requirement | Acceptance criteria |
| --- | --- | --- | --- |
| PO-01 | Must | Generate daily call lists: refill due, ready for pickup, balance due, adherence gap | Lists apply consent and eligibility rules (C-09, C-10) automatically |
| PO-02 | Must | Prioritize lists by due date, revenue and days since last contact | Sort order explained on screen |
| PO-03 | Must | Click-to-call from the list; log outcome (reached, voicemail, refill confirmed, opted out, wrong number) | Outcome required before the next call |
| PO-04 | Must | Show an AI-drafted call script per patient using only allowed fields | Script contains no data outside the role's permissions |
| PO-05 | Must | Capture and enforce opt-outs immediately | Opted-out patients never reappear on a list |
| PO-06 | Should | SMS reminders for patients with written consent | Message templates approved by compliance |

### 4. CRM (prescribers; replaces HubSpot, see 7)

| ID | Priority | Requirement | Acceptance criteria |
| --- | --- | --- | --- |
| PR-01 | Must | Prescriber profile: practice, contact, specialty, Rx volume, revenue, trend, last contact | Figures match the warehouse for the same period |
| PR-02 | Must | Ranked lists: top prescribers, declining (volume down vs prior period), new, dormant | Ranking formula visible and editable by admin |
| PR-03 | Must | Log calls, visits and emails with outcome and next action | Next actions appear on the owner's task list |
| PR-04 | Must | AI-drafted outreach emails and visit briefs from prescriber stats | Drafts use aggregate data only, no patient details |
| PR-05 | Should | Territory and rep assignment with a pipeline view | Each prescriber has exactly one owner |
| PR-06 | Should | Campaign tracking: did prescribers contacted send more Rx afterwards? | Before/after volume shown per campaign |

### 5. Admin and platform

| ID | Priority | Requirement | Acceptance criteria |
| --- | --- | --- | --- |
| AD-01 | Must | Manage users, roles and SSO mapping | Role changes take effect on next request and are audited |
| AD-02 | Must | Manage data connections (add, test, disable) | Secrets stored in vault; connection test shows pass/fail |
| AD-03 | Must | Edit the business catalog with versioning and approval | Every change shows who, when, old vs new value |
| AD-04 | Must | Audit log viewer with search and export for the Auditor role | Filter by user, patient ID, action, date |
| AD-05 | Must | Results dashboard: dollars recovered, days to payment, refill capture, prescriber reactivation | Numbers trace back to logged work items |

### 6. Compliance Center (admin)

The Compliance Center lets an admin show any auditor, at any time, that the controls in this doc are working. Wherever possible a control is checked automatically from the audit log, not ticked by hand.

| ID | Priority | Requirement | Acceptance criteria |
| --- | --- | --- | --- |
| CC-01 | Must | Controls dashboard: every control (C-01 to C-14) with status, last check, owner and a link to its evidence | Status computed from live data where automatable; red items list the failing records |
| CC-02 | Must | Approval coverage: share of claim changes and patient messages with a named approver | Shows 100% or lists every exception |
| CC-03 | Must | Consent coverage: patients with consent by channel; calls or texts made without consent; opt-out turnaround | "Contacts without consent" must read zero; any breach opens an incident |
| CC-04 | Must | Access review: users, roles, last login, PHI records viewed; quarterly review with manager sign-off | Sign-off recorded with name and date; removed users lose access the same day |
| CC-05 | Must | Audit log explorer with alerts on bulk views, off-hours exports and offshore PHI access | Alerts reach the compliance owner within 15 minutes |
| CC-06 | Must | AI governance log: every AI call's purpose, model, fields sent, user, and whether the draft was accepted or edited | No AI call without a logged purpose; fields sent never exceed the task's allow-list |
| CC-07 | Must | Vendor and BAA register: each vendor touching PHI, BAA status, expiry date | Reminder 60 days before expiry; vendor with no BAA blocks its connection |
| CC-08 | Must | One-click audit pack: PDF + CSV of control status, approvals, consent, access reviews, incidents for a date range | Generated in under 2 minutes; the export itself is logged |
| CC-09 | Should | Incident register: log, triage, notification deadline tracking, closure notes | Every incident has an owner and a due date |
| CC-10 | Should | Policy and training tracker: staff acknowledgment of HIPAA policies and training completion | Users with overdue training are flagged to their manager |
| CC-11 | Should | Retention and deletion log: rules per data type and a record of each deletion job | Deletions are logged with counts, never with PHI |

### 7. CRM: HubSpot replacement (phased)

The CRM replaces HubSpot in three steps so the marketing team never loses a working tool.

| ID | Phase | Requirement | Acceptance criteria |
| --- | --- | --- | --- |
| HS-01 | 1 | Replace the Talend → HubSpot push with platform sync; write our scores and ranked lists into HubSpot via its API | HubSpot shows prescriber score and trend fields, refreshed daily |
| HS-02 | 1 | Audit what HubSpot holds today; remove any patient data not covered by a HubSpot BAA | Written data inventory signed by client compliance |
| HS-03 | 2 | One-time migration of contacts, companies, notes, tasks and pipeline history from HubSpot | Mapping report; record counts match; spot-check of 50 records passes |
| HS-04 | 2 | Activity timeline per contact: calls, visits, emails, notes, tasks, owner | Every activity visible in one place, newest first |
| HS-05 | 2 | Configurable pipelines (e.g. prescriber onboarding: identified → contacted → first Rx → regular) | Admin edits stages without code |
| HS-06 | 2 | One-to-one email from the CRM through a BAA-covered email service, logged to the timeline | Sent emails and replies appear on the contact |
| HS-07 | 2 | Parallel run with HubSpot for 4 weeks, then cut-over checklist | Marketing lead signs off cut-over |
| HS-08 | 3 | Email campaigns with templates, unsubscribe handling and tracking; web form leads | Unsubscribes honoured automatically; HubSpot retired or kept email-only |

## UI requirements

The UI is built on a design system from the first sprint, and a clickable prototype of the five core screens is signed off by client users before backend build starts. "Neat" means every user sees their queue first, does the next action in one or two clicks, and never hunts for status.

**Design system**

- React + TypeScript with one component library (e.g. shadcn/ui on Tailwind, or Fluent UI if the client is Microsoft-first). No hand-styled one-off components.
- One set of tokens: colours, type scale, spacing, radius. Status colours fixed across the app: paid = green, pending = amber, rejected = red, info = blue.
- Light theme by default; dark theme supported by tokens.
- Accessibility: WCAG 2.2 AA, full keyboard use, visible focus, 4.5:1 text contrast.

**Core screens**

| Screen | Primary user | What it must do at a glance |
| --- | --- | --- |
| My queue (home) | Everyone | Today's work items for my role, overdue first, counts by type |
| Claims work item | Billing | Claim summary, reject code, rule-based fix, AI explanation, approve/edit buttons, history timeline |
| Patient call list | Outreach | Ranked list, click-to-call, script panel, one-click outcome logging |
| Prescriber profile and lists | Marketing | Stats and trend sparkline, contact history, next action, AI email draft |
| Results dashboard | Managers | Dollars recovered, days to payment, refill capture, prescriber reactivation, by week |
| Compliance Center | Admin, Auditor | Control status tiles (green/amber/red), exceptions to fix, audit pack button, BAA register |
| Admin | Admin | Users and roles, data connections, business catalog |

**Interaction standards**

- Left navigation by module, top search across Rx number, patient ID and prescriber name (results respect role permissions).
- Tables: sticky headers, saved filters, column chooser, CSV export only where the role allows.
- AI output always labelled as AI-drafted, editable, and shown next to the rule or data it came from.
- Every screen has loading skeletons, a clear empty state ("No rejected claims today") and readable error messages; no raw stack traces.
- PHI masked by default in lists (e.g. patient initials + last 4 of phone); full details on click, which is audited.
- Desktop-first at 1280 px and wider; prescriber screens usable on a tablet for field reps.

## Brand identity

The product is **Levigate**, by GainInsights, with the tagline **Every script, smoothed to paid.** The full brand sheet, motion examples and icon set live on the [Levigate brand guide](brand/README.md).

**The mark.** The prescription sign, ℞, drawn as one smooth stroke: the R's leg sweeps out into a level base, the way levigation works a powder into an even preparation. The mint cross-stroke makes it read Rx at a glance and is reused as the accent for progress and success.

&#91;image: Levigate app icon: the Rx sign in white on deep teal with a mint cross-stroke\]

| Element | Specification |
| --- | --- |
| Colours | Deep Teal #0B4F57 (primary), Ink #0B2E33 (text), Mint #5EEAD4 (accent, on dark or as a stroke only), Mist #F4F9F8 (background) |
| Status colours | Success #0F7A5A, Attention #B54708, Rejected #B42318, each always paired with an icon and a word |
| Type | Space Grotesk 600 for the wordmark and headings; Inter 400–600 for interface text |
| Icon sizes | 512 and 192 px app icons, 32 and 16 px favicons with heavier strokes |
| Clear space | At least a quarter of the icon's width on every side |
| Alternate taglines | Rough edges in. Revenue out. · Smooth operations. Proven compliance. |

Before public use: trademark search for "Levigate" (software and healthcare classes) and domain check.

## UX principles

Every screen is judged by one question: can a billing tech, outreach caller or marketer finish their next task in two clicks without wondering what happened? These rules apply from the first sprint.

**Work flows, not pages**

- **Queue first.** Home is my work, sorted by urgency and dollars; finishing an item opens the next one automatically.
- **One primary action per screen,** visually dominant; secondary actions grouped and quieter.
- **Keyboard-first for power users:** J/K next and previous, A approve, E edit, / search, ? shows all shortcuts.
- **Explain every number:** each figure and AI suggestion shows where it came from (rule version, data source, date) on hover or click.

**Trust and safety in the interface**

- **Confirm what can't be undone** (approvals, exports, bulk lists); **undo for 10 seconds** on everything else (notes, assignments, filters).
- **Errors say what happened and what to do next,** and never lose what the user typed.
- **Privacy by default:** PHI masked in lists, revealed on click, and that reveal is audited.

**Learnability**

- First login opens a 4-step guided tour and a sandbox with demo data, so nobody practises on real patients.
- Every empty state names the next step ("No rejections today. Review unpaid claims →").

**Healthy engagement**

- **Daily wins:** an end-of-day summary such as "12 claims recovered, $3,420" per user and per team.
- **Weekly goals** set by the manager with a progress ring on the home screen.
- **Digest, not pings:** notifications batched to twice a day, except compliance alerts.
- **No leaderboards that reward speed over accuracy:** approvals and patient calls stay careful work.

**Proof it works:** usability tests with 5 users per role before each phase; target a System Usability Scale score of 80+ and task times measured against the current manual process.

## Motion and feedback

Every click answers within 100 ms and every wait shows real progress; motion explains what the system is doing and never decorates. A working example, including the approve-a-claim sequence, is in the [motion and feedback section](brand/README.md#motion-and-feedback) of the brand guide.

| Moment | Behaviour | Timing |
| --- | --- | --- |
| App start | The Rx mark draws itself and the mint stroke lands; first load only | Loop of 2.4 s |
| Lists and tables | Skeleton rows in the final layout, so nothing jumps when data arrives | Until loaded |
| Short waits | Spinner inside the button that was pressed, with a verb ("Saving and logging…") | Shown only after 300 ms |
| Long jobs (over 2 s) | Named steps with checks ("Connected → Rules applied → Building work items") | Live |
| Button press | Scale to 97%, colour shift | 120 ms |
| Hover and focus | Colour change; mint focus ring for keyboard users | 150 ms |
| Panels and toasts | Rise 8 px and fade in, ease-out | 250 ms |
| Success | Check mark draws, then the audit line appears beneath | 350 ms |
| Approvals | Never optimistic: success shows only after the server confirms and logs it | Server-driven |
| Reduced motion | All animation off when the operating system asks | Always |

Implementation: CSS transitions and keyframes only (no heavy animation libraries); one shared set of motion tokens (durations and easing curves) in the design system.

## Iconography

One icon language across the product: [Lucide](https://lucide.dev) (open source, ISC licence) as the base set, plus custom pharmacy icons drawn to the same rules. The set is in the [iconography section](brand/README.md#iconography) of the brand guide.

| Rule | Specification |
| --- | --- |
| Grid and stroke | 24 px grid, 1.75 px stroke, round caps and joins, no fills except status dots |
| Sizes | 16 px in tables, 20 px in buttons and navigation, 24–28 px in empty states |
| Custom icons | Prescription (℞), medication, compounding (mortar and pestle), claim, rejected claim, payer, prescriber, patient, call, refill, consent, audit log, compliance, AI draft, approve, alert |
| Labels | Action icons always sit beside a word; icon-only buttons carry an accessible name and a tooltip |
| Status | Never colour alone: paid, pending and rejected each pair an icon, a colour and a word |
| Accent strokes | Mint for success and progress, red for rejection, amber for attention; everything else teal |
| AI marker | Every AI-drafted text carries the AI draft icon and the label "AI draft" until a person edits or approves it |
| Delivery | Icons shipped as an SVG sprite and React components from the design system; no icon fonts, no emoji |

## Non-functional requirements

These are the pilot targets; production SLAs are agreed with the client after the pilot.

| Area | Requirement |
| --- | --- |
| Performance | Queue and list screens load in under 2 s for up to 50,000 open work items; AI drafts return in under 8 s |
| Availability | 99.5% during pharmacy business hours (US time zones); scheduled maintenance outside them |
| Scale | 25 concurrent users in the pilot; design for 10 pharmacies without re-architecture |
| Backup and recovery | Daily encrypted backups, 30-day retention; restore tested once before go-live; RPO 24 h, RTO 8 h |
| Security | OWASP Top 10 covered; dependency and secret scanning in CI; CSRF protection, secure cookies, rate limiting on APIs |
| Code quality | Async-safe backend (no blocking DB or LLM calls on the request thread); typed models; code review on every merge |
| Testing | Unit tests on rules engine and connectors; end-to-end tests on the five core screens; a regression set of de-identified claims run before each release |
| Observability | Structured logs (PHI-redacted), metrics and alerts for sync failures, LLM errors, slow queries; a health page for admins |
| AI quality | Track AI draft acceptance rate and edits; reject-code routing accuracy 95%+ on known codes |
| Environments | Separate dev, test and prod; dev and test use synthetic or de-identified data only |

## Pilot plan and success metrics

The pilot runs 10 weeks at the current compounding pharmacy client, and nothing touches PHI until the week-2 compliance and UI sign-off.

&#91;embedded content: pilot roadmap · 4 phases, 4 gates\]

Each phase ends with a demo to client users; a failed gate extends that phase rather than starting the next.

**Team (proposed)**

| Role | Count | Responsibility |
| --- | --- | --- |
| Product lead / solution architect | 1 | Requirements, catalog with client SMEs, architecture, demos |
| Backend engineer (Python/FastAPI) | 1–2 | Connectors, rules engine, API, AI service |
| Frontend engineer (React) | 1 | Design system and the five core screens |
| Data engineer | 0.5 | Snowflake queries, reject-code data, reconciliation |
| QA / test | 0.5 | Regression set, end-to-end tests, UAT support |
| Client SMEs | part-time | Billing lead, pharmacist, compliance, marketing lead |

**Success metrics (measured against a 4-week baseline before go-live)**

| Metric | How it is measured | Pilot target |
| --- | --- | --- |
| Revenue recovered | Dollars paid on claims that went through a work item | Set with client after baseline |
| Days to payment | Median days from fill to paid | Down vs baseline |
| Rejection backlog | Open rejected claims older than 7 days | Down vs baseline |
| Refill capture rate | Eligible refills filled within 7 days of due date | Up vs baseline |
| Prescriber reactivation | Declining prescribers contacted who increase Rx in 60 days | Tracked from week 8 |
| AI usefulness | Share of AI drafts accepted with light or no edits | 70%+ |
| Compliance | Audit findings on access, consent and approvals | Zero critical |

## Product roadmap

The pilot proves the revenue loop; phase 2 brings the separate point tools into one platform; phase 3 adds our own PMS so prescriptions are entered where everything else already lives.

&#91;embedded content: product roadmap · 3 phases, 2 gates\]

Phase 3 is the largest build: a PMS must submit claims to payers in the industry-standard formats, handle e-prescriptions and controlled substances, and pass the related certifications, so its scope is set only after phase 2 is in daily use.

## Client demo plan

We win the pilot with a 15-minute working demo, not slides: one "Monday morning at the pharmacy" story that moves through every module and ends in the Compliance Center, so the client sees revenue impact and proof of compliance in the same sitting.

**Demo build (estimate: about 3 weeks, 2 engineers)**

- Working screens on the real pilot stack: My queue, Claims work item, Patient call list, Prescriber profile, Compliance Center.
- 100% synthetic data, no real PHI: a fictional pharmacy with about 2,000 patients, 150 prescribers, 6 months of fills, realistic payers and reject codes.
- Isolated demo environment with a one-command reset, plus a recorded backup video in case of network issues.

**The script**

| Step | What we show | Why it lands |
| --- | --- | --- |
| 1 | My queue opens on the dollars at risk in rejected and unpaid claims | The problem in their own language: money |
| 2 | A "refill too soon" rejection: rule-based fix, AI explanation, one-click approve | Minutes instead of a manual lookup; a human stays in control |
| 3 | An unknown reject code goes to a review queue instead of a guess | The AI doesn't make things up |
| 4 | Patient call list: one patient excluded for no consent, one for a lapse over 90 days, each with the reason shown | HIPAA and TCPA rules enforced by the system, not by memory |
| 5 | Prescriber profile: volume down sharply and many rejected claims among their patients; AI visit brief; call logged | Cross-module insight no point tool gives |
| 6 | A plain-English question: "Which payers rejected most this month?" answered with a chart | Their data, answered instantly |
| 7 | Compliance Center: green tiles, the approval from step 2 already in the audit log, one amber BAA renewal, audit pack downloaded | Proof that every action is tracked and audit-ready |
| 8 | Results dashboard sized with the client's own aggregate numbers (no PHI) | Their likely ROI, not a generic one |

**Before the demo:** get aggregate counts from the client (monthly fills, rejection rate, average claim value) so step 8 uses their scale.

## Open questions for the client

These decide the build; the first three block the start.

- [ ] Does the PMS (PK) store the full claim response, including reject codes and messages, and does Talend already land it in Snowflake?
- [ ] Will the client sign a BAA with GainInsights, and may our India-based team see PHI, or must it be masked offshore (C-11)?
- [ ] Which hosting is approved: client's Azure tenant or a GainInsights-managed HIPAA environment?
- [ ] Where is patient consent for calls and texts recorded today, and how complete is it?
- [ ] Which telephony system does the outreach team use, and does that vendor sign a BAA?
- [ ] Who are the named owners for the catalog: billing lead, pharmacist, compliance, marketing?
- [ ] Which HubSpot features does the marketing team use every week, and does the Talend push send any patient data into HubSpot today?
- [ ] Expected payment window per major payer, and the underpayment threshold to flag.
- [ ] Should prescriber outreach include fax, or calls and email only?
