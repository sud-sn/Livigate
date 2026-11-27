# Readiness report — realistic client data phase

**Question this phase answers:** does Levigate work on a pharmacy's data as it really sits in Snowflake (normalised
PMS tables landed by Talend, with the mess real pipelines carry, and business logic already living in BI views), for
every kind of user, with no difference from the clean reference layout?

**Answer:** yes, on synthetic data that reproduces that shape. Every role sees identical results on the reference
layout and on two client layouts; incremental sync misses nothing; the client's BI formula agrees to the cent;
50,000 open work items stay fast; every screen passes WCAG 2.1 A/AA in light and dark. Not yet proven against a
real Snowflake account — see *Limits*.

## The simulated client

One simulated pharmacy (2,000 patients, 150 prescribers, 7 payers, 12 compound formulas, 10,227 fills over six
months, 6,848 of them with claims in the 120-day working window) is written in two layouts:

* **Reference** — Levigate's seven canonical relations.
* **Client** — 18 normalised PMS tables + 2 BI views, as Talend would land them, with the mess found in real
  pipelines (default demo size): 70 duplicate fill rows from reloads, 31 voided fills, 22 claim transactions whose
  fill has not landed yet, 86 secondary-payer (COB) claims, 94 rejections with extra reject codes, 926 fills paid
  across two 835s, 7 expired contract rows, 265 e-mail preferences, 43 patients without a phone, names in capitals,
  phone numbers in five formats, plan types as codes. Some formulas sit on a cash-price list so the U&C half of the
  expected-reimbursement formula is exercised.

Three source profiles read them: `canonical`, `pms_snowflake` (BI formula ported into the mapping) and
`pms_snowflake_bi` (expected amount taken from the client's BI view). See [data-integration.md](data-integration.md).

## Evidence

| Area | How it was tested | Result |
| --- | --- | --- |
| Whole backend suite on every layout | 113 tests; CI runs the suite once per profile (the cross-layout and load tests run once and cover all layouts) | All pass on all three layouts, with PostgreSQL |
| Same answer from every layout | 25 shared named queries compared row by row; the 2 BI queries compared between client variants | Identical |
| Every role sees the same | Full environment per layout (warehouse → mapping → sync → API); 31 API views across 7 demo users compared | Identical work items and API answers |
| Incremental sync | Yesterday's data synced, today's load lands, 15-minute sync runs, then a forced full re-read | 32 changed rows read, 3 items created; full re-read: 0 created, 0 updated, 0 resolved |
| Data checks | 29 checks; each injected problem counted | Exact counts; 0 failing on correct mappings |
| Mutation tests | Keep reload duplicates · ignore contract dates · drop the U&C rule · drop a column · leave codes untranslated | Each caught (data check, contract or cross-layout comparison) |
| BI parity (default demo) | Levigate vs the client's BI views, fill by fill | Expected reimbursement 6,848/6,848 to the cent · days to payment 5,780/5,780 · 131 status and 174 volume differences, **all explained** (flat 30-day unpaid vs payer windows, no "underpaid" in BI, BI counts sold fills only) |
| Snowflake SQL | Every query, check and mapping composed as sent, parsed with sqlglot's Snowflake dialect | Valid; standard functions only; no writes |
| Permissions | Every API route (55) × 7 demo users covering all 6 roles + signed out, discovered from the app | Matches the permission table; a route without a permission fails the test |
| Load | 50,000 open work items | Queue page 0.15 s (page 400: 0.46 s) · filtered 0.18 s · claims tracker 0.16 s · dashboard 0.20 s · item 0.08 s · call list 0.14 s · sync 1.9 s |
| Role journeys (browser) | 29 steps for 8 demo users on the client layout: billing, offshore billing, pharmacist, outreach, marketing, two admins, auditor | All pass: each sees only its modules, completes its daily work, and is refused elsewhere |
| PHI boundaries | Offshore user and marketing: screen text and every API response checked | No patient data |
| Accessibility | axe-core on 18 screens × light and dark | 0 WCAG 2.1 A/AA violations |
| Demo script | Existing smoke test on React Router 7 | Passes on both layouts |
| Dependencies | pip-audit (backend), npm audit (runtime) | 0 known vulnerabilities |

## Problems this phase found and fixed

1. **Past-due claims could surface up to a day late.** A claim with no payment becomes past due when the payer's
   window ends — nothing changes in the source, so the 15-minute incremental pull never saw it. The first sync of
   every business day now re-reads everything.
2. **Order depended on how the warehouse returned rows.** Equal dollar amounts in the queue, equal scores on call
   lists and in prescriber rankings (including the top-25 cut) fell back to load order, so two layouts showed
   different orders. Ties now break on business keys.
3. **Patient names and phones differed by source.** Capitals and five phone formats reached reveal screens and call
   scripts. Normalised once in the service layer; phones display as (708) 555-0124.
4. **Payer plan-type codes were passed through untranslated.** Now translated in the mapping, and a vocabulary check
   fails on any unknown value.
5. **The payer's response time was lost** in the PMS mapping (submission time used). Mapped from the response
   timestamp.
6. **Patients with no phone sat at the top of call lists.** Now listed under "Not on today's list" with the reason.
7. **Queue sorted in Python** (all items loaded per request). Sorted and paged in the database.
8. **Data checks took 30 s on the demo database** (mapped relations re-expanded per row). Composed relations are
   materialised on SQLite only; Snowflake receives plain `WITH` clauses.
9. **Accessibility:** scrolling panels were not reachable by keyboard; dark-mode red text measured 4.49:1 on the
   selected queue row. Both fixed.
10. **Simulation realism:** surrogate keys and injected mess changed when the warehouse was rebuilt for a later day,
    and the U&C rule was never exercised. Both fixed, so day-by-day loads behave like a real source.

Also delivered: React Router 7 (7.18.5) upgrade; Admin → Data source (mapping, contract, data checks, BI comparison);
Admin → Sync → Full re-read.

## Limits — what is not proven yet

* **Not run against a real Snowflake account.** SQL is validated as Snowflake syntax and executed on SQLite; the app
  database runs on PostgreSQL. First step with the client: run the column contract, data checks and BI comparison
  against their read-only role.
* **The client's real BI definitions will differ** from the simulated ones. The comparison is the tool to find out;
  expect a round of mapping fixes with their data team.
* **The change marker** assumes a landing timestamp per row (Talend `LOAD_TS`). Without one, only full scans are safe.
* **Load test on SQLite**; not repeated on PostgreSQL (expected to be the same or better).
* Development tooling advisories (vite/esbuild dev server, vitest, eslint, typescript-eslint, postcss) are fixed:
  vite 6.4.6, vitest 4.1.12, eslint 9.39.5, postcss 8.5.33, minimatch 11.0.7 for the TypeScript lint parser.
  `npm audit` reports 0 vulnerabilities, development tools included; container images scan clean.
* Still open from milestone 1: OIDC SSO, migrations and infrastructure as code, real telephony, HubSpot phases
  (see [STATUS.md](STATUS.md)).

## How to reproduce

```bash
make test-all                                   # backend suite on all three layouts
cd backend && uv run pytest -q tests/test_layouts.py tests/test_performance.py
LEVIGATE_SOURCE_PROFILE=pms_snowflake make demo # then start `make api` and `make web`
make e2e && make journeys                       # demo script, then (after a fresh demo + API restart) role journeys
```
