# Levigate API

FastAPI service: connector layer, business catalog, rules engine, sync, AI service, hash-chained audit log, API.
See the repository [README](../README.md) for how to run it and [docs/STATUS.md](../docs/STATUS.md) for what is built.

| Module | Responsibility |
| --- | --- |
| `levigate/connectors.py` | Read-only, named and typed queries against client sources |
| `levigate/catalog.py` | Versioned business catalog with two-person approval |
| `levigate/rules.py` | Claim status, reject-code playbook, outreach eligibility, prescriber scoring |
| `levigate/sync.py` | Scheduled pull, work items, reconciliation |
| `levigate/ai.py` | AI tasks with field allow-lists, BAA-gated provider, governance log |
| `levigate/audit.py` | Append-only, hash-chained audit log and unusual-access alerts |
| `levigate/policy.py` | Role and object-level access, PHI masking |
| `levigate/api/` | HTTP routes |
| `levigate/demo/` | Synthetic warehouse and demo reset |
