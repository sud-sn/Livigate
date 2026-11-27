<p align="center"><img src="frontend/public/brand/levigate-lockup.svg" alt="Levigate" width="300"></p>

<p align="center"><b>Every script, smoothed to paid.</b><br>Pharmacy revenue and compliance platform, by GainInsights.</p>

Levigate closes a compounding pharmacy's revenue loop on one data layer: every filled Rx is followed to paid, every
eligible patient gets a timely refill call, every prescriber is tracked and nurtured — with an audit trail that proves
the HIPAA and TCPA controls are working. Rules decide; the AI only explains and drafts; a named person approves.

The full requirements live in [`docs/requirements.md`](docs/requirements.md), the brand in
[`docs/brand/README.md`](docs/brand/README.md), and what is built so far in [`docs/STATUS.md`](docs/STATUS.md).

## What's here

| Path | What it is |
| --- | --- |
| `backend/` | FastAPI service (Python 3.13, async SQLAlchemy). Connector layer, business catalog, rules engine, sync, AI service, audit log, API. |
| `backend/levigate/demo/` | Synthetic client warehouse (≈2,000 patients, 150 prescribers, 6 months of fills) and the one-command demo reset. |
| `frontend/` | React + TypeScript + Tailwind app on the Levigate design system (tokens, icons, motion, charts). |
| `docs/` | Requirements, brand guide, build status. |
| `.github/workflows/ci.yml` | Lint, types, tests (SQLite + PostgreSQL), dependency audits, secret scanning, image scanning, e2e smoke. |

## Run the demo

Prerequisites: Python 3.12+ with [uv](https://docs.astral.sh/uv/), Node 22.

```bash
make setup
```

```bash
make demo
```

Then, in two terminals:

```bash
make api
```

```bash
make web
```

Open http://localhost:5173 and choose a demo user. Each role sees only what it is allowed to:

| User | Role | Start here |
| --- | --- | --- |
| Jamie Patel | Billing | My queue → search `4471` → the refill-too-soon fix, AI explanation, approve |
| Ana Ruiz | Outreach | Call lists: consent and the 90-day lapse rule applied, scripts, outcomes |
| Marcus Lee | Marketing | Prescribers → Declining → Dr. Elena Marsh |
| Priya Shah | Pharmacist | DUR rejects routed to the pharmacist queue |
| Sam Okafor / Alex Chen | Admin | Business catalog (changes need a second admin), users, connections, sync |
| Dana Whitfield | Auditor | Compliance Center, audit log, one-click audit pack |
| Ravi Kumar | Billing, offshore | Same queue with PHI masked (C-11) |

Or run the whole stack — PostgreSQL, API and web — in Docker on http://localhost:8080:

```bash
make up
```

All data in the demo is synthetic. Nothing connects to a real pharmacy system until the client's warehouse
connection, BAA chain and compliance sign-off are in place (see `docs/requirements.md`, compliance controls).

## Develop

```bash
make test
```

```bash
make lint
```

```bash
make e2e
```

`make e2e` walks the client demo script in a real browser against a freshly reset demo (`make demo`, then
`make api` and `make web`). Set `LEVIGATE_TEST_PG_URL=postgresql+asyncpg://…` to also run the PostgreSQL checks
(append-only audit table, hash chain under concurrent writers from several processes).

Configuration is environment-only (`LEVIGATE_*`); see [`backend/.env.example`](backend/.env.example).

## How it fits together

```
Client sources (PMS → Talend → Snowflake)
        │  read-only service account, named + typed queries only
        ▼
Connector layer ──► Rules engine ◄── Business catalog (versioned, two-person approval)
        │                │
        │                ▼
        │          Work items, outreach lists, prescriber scores ──► App database (workflow state only)
        │                │                                              │
        ▼                ▼                                              ▼
   On-demand reads   AI service (explain / draft; field allow-lists; BAA-gated)     Hash-chained audit log
        │                │                                              │
        └────────────────┴──────────► API (RBAC + object-level policy) ◄┘
                                          │
                                          ▼
                                 Web app (design system)
```

* **Rules decide, AI explains.** Reject-code fixes, eligibility and scores are computed in code from catalog
  parameters. The model sees only allow-listed fields, has no tools, and every draft is labelled and logged.
* **Humans approve.** No claim change goes out without a named approval; the UI shows success only after the
  server has written the approval and its audit event.
* **The app stores IDs, not a second copy of PHI.** Patient details are read live, masked by default, and every
  reveal is audited.
