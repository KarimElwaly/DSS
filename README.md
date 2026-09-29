# Revenue & Pricing Decision Support System

A decision support system that turns competitive market signals and sales history into
**defensible price recommendations**. It combines competitor ingestion, econometric
demand estimation, constrained price optimization, a what-if sandbox, and a locally
hosted LLM advisor grounded on computed financial facts.

Built as a graduation project: every recommendation is traceable to an estimate, every
estimate carries uncertainty, and every claim is validated against a simulated market
whose true parameters are known.

---

## Why a simulator sits at the centre

Real pricing data never reveals the true elasticity, so a model's accuracy cannot be
measured on it. This project therefore ships a **market simulator** that generates sales
from a declared demand curve:

```
ln q = ln(base) + ε·x + γ·x² + η·ln(cost) + Σ κ_j·ln(competitor price_j)
       + season + weekday + trend + promo lift + stockout boost + noise
units ~ Poisson(exp(ln q))
```

Because `ε` (elasticity), `κ` (cross-effects) and the revenue-optimal price are known by
construction, the estimators and the optimizer can be scored rather than merely
demonstrated. Ground truth lives in an isolated `ground_truth` table that analytics,
optimization and simulation code must never read.

### Headline validation result

Competitor prices are an omitted variable: rivals *react* to our price, which attenuates
a naive elasticity estimate toward zero. Feeding Module A's output into the estimator
removes almost all of that bias.

| Specification | Mean abs. error | **Bias** | 90% CI coverage | R² |
| --- | --- | --- | --- | --- |
| Naive, all 24 SKUs | 21.5% | **+17.0%** | 66.7% | 0.498 |
| Naive, 19 SKUs with competitor coverage | 18.5% | **+12.8%** | 78.9% | 0.493 |
| **+ competitor price index & out-of-stock share** | **17.8%** | **+0.4%** | 68.4% | 0.504 |

Competitive intelligence removes roughly **97% of the systematic bias**. Reproduce with:

```powershell
python scripts/check_elasticity_recovery.py
```

> Known gap: CI coverage is below nominal because residual serial correlation is not yet
> handled. Scheduled fix is HAC/Newey-West standard errors.

---

## Modules

| Module | Scope | Status |
| --- | --- | --- |
| **A** Competitive intelligence | Pluggable connectors, product matching (GTIN → embedding → fuzzy rerank), stockout tracking, alerts | Ingestion + GTIN matching shipped |
| **B** Elasticity & forecasting | Log-log demand estimation with confidence intervals, scenario forecasts | Validation harness shipped |
| **C** Dynamic pricing | Revenue / margin / penetration objectives under floor & ceiling guardrails | Planned |
| **D** What-if sandbox | Hypothetical scenarios over the same engine, persisted for comparison | Planned |
| **F** Financial advisor | CSV/Excel ERP upload, anomaly diagnostics, locally hosted LLM recommendations grounded on a facts JSON | Planned |

---

## Stack

| Layer | Choice |
| --- | --- |
| API | Python 3.11+, FastAPI, SQLAlchemy 2.0, Pydantic v2, Alembic |
| Database | PostgreSQL 16 + `pgvector` + `pg_trgm` |
| Analytics | NumPy, pandas, SciPy, statsmodels, scikit-learn |
| Frontend | Next.js (App Router), React 19, TypeScript, Tailwind CSS v4, Recharts |
| LLM | Ollama, run locally — no data leaves the machine |

---

## Ports

Non-default ports are used deliberately so the stack never collides with an existing
local PostgreSQL or another API server.

| Service | Host port | Notes |
| --- | --- | --- |
| PostgreSQL | **55432** | container port 5432; avoids a pre-existing local Postgres |
| API | **8010** | container port 8000 |
| Web | 3000 | |
| Ollama | 11434 | optional, `--profile llm` |

CI uses the standard 5432 because the runner has nothing else bound.

---

## Quickstart

Prerequisites: Docker Desktop (running), Python 3.11+, Node 20+.

```powershell
# 1. configuration
Copy-Item .env.example .env

# 2. database
docker compose -f infra/docker-compose.yml up -d postgres

# 3. backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e "backend[dev]"
cd backend
alembic upgrade head
dss seed --days 730          # synthetic market + admin user
uvicorn app.main:app --reload --port 8010

# 4. frontend (new terminal)
npm --prefix frontend install
npm --prefix frontend run dev
```

Open <http://localhost:3000> and sign in with the credentials from `.env`
(`admin@dss-demo.com` / `admin12345` by default).

Alternatively run everything in containers:

```powershell
docker compose -f infra/docker-compose.yml up --build
docker compose -f infra/docker-compose.yml exec api dss seed
```

---

## CLI

```powershell
dss seed --days 730 --seed 42 --reset   # regenerate the synthetic market
dss reset                               # wipe all rows, keep the schema
dss info                                # row counts per table
```

---

## Repository layout

```
backend/
  app/
    api/          FastAPI routers and dependencies
    core/         settings, security, logging
    db/           declarative base, session management
    models/       25 SQLAlchemy tables
    schemas/      Pydantic request/response models
    services/     seeding, market views, audit
    cli.py        Typer command line
  simulator/      synthetic market generator (ground truth lives here)
  alembic/        migrations
  scripts/        validation experiments and smoke tests
  tests/
frontend/
  app/            App Router pages (overview, catalog, market, listings, login)
  components/     shared UI
  lib/            API client, formatting, auth guard
infra/
  docker-compose.yml
```

---

## Design decisions worth knowing

- **Enums are `VARCHAR` + `CHECK`, not PostgreSQL enum types.** Adding a value stays a
  one-line migration instead of an `ALTER TYPE` dance.
- **Every business table carries `organization_id`.** Multi-tenancy becomes a filter
  change rather than a rewrite.
- **`audit_event` is append-only** and deliberately has no `updated_at`. Events are added
  to the caller's session so they commit in the same transaction as the action.
- **Money is `NUMERIC(14,4)`, rates are `NUMERIC(8,6)`**, cast to float only at the
  analytics boundary.
- **Login accepts any string as the email.** It is a lookup key at that point; rejecting
  the format would leak a 422 where a uniform 401 belongs.
- **pandas is pinned below 3.0.** pandas 3 coerces `str`-mixin enum columns into its new
  string dtype, which silently breaks equality against enum members.

---

## Quality gates

```powershell
cd backend
ruff check . ; ruff format --check .
mypy app simulator
pytest -q

npx --prefix ../frontend tsc --noEmit
npm --prefix ../frontend run build
```

Database-backed tests skip automatically when PostgreSQL is unreachable, so the
simulator and pure-Python suites always run. The same gates run in GitHub Actions.
