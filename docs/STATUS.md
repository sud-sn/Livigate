# Build status

Milestone 1 — **demo build on the real pilot stack, with synthetic data** (requirements: *Client demo plan*).
Legend: ✅ built and tested · 🟡 partly built · ⏳ not started.

## Demo script coverage

| Step | What we show | Status |
| --- | --- | --- |
| 1 | My queue opens on dollars at risk | ✅ |
| 2 | "Refill too soon" (Rx 4471-02): rule-based fix, AI explanation, one-click approve with audit line | ✅ |
| 3 | Unknown reject code goes to review instead of a guess | ✅ |
| 4 | Call list: patients excluded for no consent / lapse over 90 days, with the reason | ✅ |
| 5 | Declining prescriber with PA friction; AI visit brief; call logged | ✅ |
| 6 | Plain-English question answered with a chart (approved named queries only) | ✅ |
| 7 | Compliance Center: tiles, approval in the audit log, amber BAA renewal, audit pack | ✅ |
| 8 | Results dashboard sized with the client's aggregate numbers | 🟡 runs on synthetic aggregates; needs the client's counts |

## Functional requirements

| Area | Built | Not yet |
| --- | --- | --- |
| Claims tracker | CT-01 … CT-05 (status counts match the source — tested) | Load test of the 2 s target at 50k items |
| Insurance follow-up | IF-01 … IF-05 | IF-06 prescriber fax/message drafts |
| Patient outreach | PO-01 … PO-05 (telephony is simulated: the provider dials server-side) | PO-06 SMS; real telephony provider |
| Prescriber CRM | PR-01 … PR-04 | PR-05 pipeline view (owners are stored), PR-06 campaign tracking |
| Admin | AD-01 users/roles, AD-03 catalog with two-person approval, AD-04 audit search, AD-05 results | AD-01 SSO group mapping; AD-02 add-connection form (list, test, enable/disable are built) |
| Compliance Center | CC-01 … CC-04, CC-06 … CC-08 | CC-05 alert delivery to the compliance owner (alerts are raised and shown); CC-09 … CC-11 |
| HubSpot replacement | — | HS-01 … HS-08 |

## Controls and security

| Item | Status |
| --- | --- |
| C-01 BAA chain | ✅ register, 60-day amber, AI falls back to offline if the LLM vendor's BAA lapses |
| C-02, C-03, C-14 | 🟡 attested in the Compliance Center; infrastructure-as-code not written yet |
| C-04 Identity | 🟡 server-side sessions, 15-min idle timeout, CSRF; **OIDC SSO with the client IdP not built yet** |
| C-05 RBAC / minimum necessary | ✅ six roles, object-level checks, role-specific response models (tested) |
| C-06 Audit trail | ✅ append-only (database trigger on PostgreSQL) and hash-chained; tamper detection tested |
| C-07 AI data controls | ✅ field allow-lists, governance log, hashes only, token budgets |
| C-08 Human approval | ✅ idempotent, never optimistic, coverage computed live |
| C-09 Consent | ✅ checked at call time; opt-outs revoke every channel immediately |
| C-10 Refill-reminder scope | ✅ 90-day lapse rule, current drug only |
| C-11 Offshore | ✅ masking by location unless approved |
| C-12 Exports | ✅ role-restricted, confirmed, watermarked, logged |
| C-13 Incident alerts | 🟡 bulk views, off-hours exports, offshore PHI access raise alerts; no paging/notification yet |
| OWASP API Top 10 | ✅ API1, API3, API5, API7, API8, API9, API10 · 🟡 API2 (OIDC pending), API4/API6 (in-memory limits; Redis for scale-out) |
| OWASP LLM Top 10 | ✅ delimited data, no tools, escaped output, allow-lists, budgets |

## UI and UX

✅ design system (tokens, light + dark, WCAG-minded focus ring), 16 pharmacy icons + sprite, motion tokens and the
approve sequence, keyboard shortcuts, PHI masked with audited reveal, skeletons/empty states/readable errors,
validated chart colours with table views.
⏳ guided tour + sandbox, notification digest, undo on reversible actions (the toast supports it), tablet pass.

## Engineering decisions worth knowing

* **A work item follows a fill**, not a claim row; at most one open item per fill. A new response re-routes it.
* **Two dates per item:** the *due date* is when a person must act (source event + SLA, so a backlog shows as
  overdue); the fix's own date (e.g. "resubmit on 12 Dec") becomes the follow-up date after approval.
* **Audit writes share the business transaction** and hold a PostgreSQL advisory lock, so the hash chain cannot fork
  even with several API processes (tested with four concurrent processes; without the lock it forks).
* **AI defaults to an offline template engine** so the demo sends nothing anywhere; Azure OpenAI is one setting away
  and is used only while the vendor register shows an active BAA.
* **Focus ring:** bright mint is 1.4:1 on white, so light surfaces use the brand's mint stroke `#0FA38A` (3.3:1).

## Next up (proposed order)

1. OIDC SSO with the client IdP (C-04/API2) and SSO group → role mapping (AD-01).
2. Alembic migrations (today the schema is created at startup) and infrastructure as code for the Azure tenant.
3. Snowflake connector against the client's read-only role; replace synthetic queries' column names if they differ.
4. Telephony provider integration (click-to-call, call status, opt-out sync) once its BAA is signed.
5. IF-06, PO-06, PR-05/06, CC-05 notifications, guided tour + sandbox.
6. HubSpot phase 1 (HS-01, HS-02).
7. Upgrade React Router 6 → 7. Two moderate advisories remain on 6.30.6 (open redirect via backslash in
   `<Link>`/`useNavigate`; SSR hydration injection). Neither applies today — the app only navigates to paths it
   builds itself and does no server-side rendering — but the major upgrade clears them.
