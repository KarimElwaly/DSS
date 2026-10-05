# Revenue & Pricing Decision Support System

A decision support system that turns competitive market signals and sales history into
**defensible, constrained price recommendations**. It combines competitor ingestion, econometric
demand estimation, constrained price optimization, a what-if sandbox, and a locally
hosted LLM advisor strictly grounded on verified corporate financial facts.

Built as a graduation project: every recommendation is traceable to an econometric estimate, every
estimate carries uncertainty (HC3 90% confidence intervals), and every claim is validated against a simulated market
whose true parameters are known by construction.

---

## Architecture & Modular System

```
                  ┌────────────────────────────────────────────────────────┐
                  │             Market Simulator (Ground Truth)            │
                  │  ln q = ln(q0) + ε·ln(p/p0) + γ·(ln(p/p0))² + ...      │
                  └──────────────────────────┬─────────────────────────────┘
                                             │ synthetic sales & rival feeds
                                             ▼
┌───────────────────────────────────────────────────────────────────────────────────────────┐
│                               Revenue & Pricing DSS Platform                              │
│                                                                                           │
│  [Module A: Ingestion & Match] ──► [Module B: Elasticity & Forecast]                      │
│   • Feed connectors                 • Log-log OLS + HC3 robust SE                         │
│   • GTIN + cosine embedding match   • 90% Confidence Intervals                            │
│   • Auto-accept & review queue      • Sibling cross-elasticity                            │
│   • Market index & alerts           • 30d Holt-Winters ETS + WAPE backtest                │
│                 │                                     │                                   │
│                 ▼                                     ▼                                   │
│  [Module F: Financials & CFO Advisor]  [Module C: Optimization Engine]                    │
│   • P&L, BS, CF ingestion           • Margin / Revenue / Penetration objectives           │
│   • EBITDA, CAC, LTV, Runway, CCC   • Sibling cannibalization penalty                     │
│   • Robust z-score anomaly detector • Guardrails (floor, ceiling, step limit, charm)      │
│   • Grounded LLM narrative (Ollama) • Recommendation lifecycle (propose/approve/apply)    │
│   • Zero-hallucination validator                      │                                   │
│                                                       ▼                                   │
│                                        [Module D: Scenario Sandbox]                       │
│                                         • Monte Carlo parameter draws                     │
│                                         • Competitor reaction modeling                    │
│                                         • P05 / P25 / P50 / P75 / P95 fan charts          │
└───────────────────────────────────────────────┬───────────────────────────────────────────┘
                                                │ REST API (FastAPI)
                                                ▼
┌───────────────────────────────────────────────────────────────────────────────────────────┐
│                                   Next.js Web Frontend                                    │
│   • / (Overview)     • /market (Market Watch) • /workbench (SKU Diagnostics) • /sandbox   │
│   • /catalog         • /listings (Match Review)• /recommendations            • /advisor   │
└───────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## Modules Overview

| Module | Scope | Key Capabilities | Status |
| --- | --- | --- | --- |
| **A. Competitive Intelligence** | Ingestion & matching | Pluggable connectors, two-stage matcher (GTIN exact $\to$ embedding cosine with fallback), anomaly alerts, match review queue | **Complete** |
| **B. Elasticity & Forecasting** | Econometric demand modeling | Log-log OLS with HC3 robust standard errors, 90% CIs, pooled category fallback, sibling cross-elasticity, 30-day Holt-Winters ETS + rolling backtest | **Complete** |
| **C. Dynamic Pricing** | Constrained optimization | Profit / Revenue / Penetration objectives, cannibalization penalty, hard cost floor ($p \ge \text{cost}/(1-m_{\min})$), $\pm 15\%$ max step, charm rounding | **Complete** |
| **D. What-if Sandbox** | Monte Carlo scenario engine | Parameter sampling $\varepsilon \sim \mathcal{N}(\hat\varepsilon, \hat\sigma_\varepsilon^2)$, rival reaction dynamics, P05/P25/P50/P75/P95 distribution bands | **Complete** |
| **F. Financial & Strategic Advisor** | Corporate finance & AI advisor | 8-quarter P&L/BS/CF, EBITDA, CAC, LTV, Runway, CCC, robust z-score anomaly detector, Ollama LLM with strict numeric-grounding validator | **Complete** |
| **Market Simulator** | Synthetic validation ground truth | Generates realistic electronics retail market with known demand curvatures, competitor reactions, stockouts, and seasonal cycles | **Complete** |

---

## Benchmark & Validation Results

### 1. Headline Experiment: DSS vs Retail Baselines

To prove that econometric optimization creates economic value over traditional merchandising rules, we simulated a 90-day forward market window across 24 SKUs in 5 categories comparing three policies on the exact same market environment:

* **Cost-Plus (+25% markup)**: Standard fixed-margin retail convention ($P = \text{cost} \times 1.25$).
* **Competitor-Matching**: Market follower pricing ($P = \text{mean}(P_{\text{competitors}})$).
* **DSS Dynamic Pricing**: Elasticity-informed profit optimization under cannibalization penalties and hard margin guardrails.

Run the experiment locally:
```powershell
cd backend
python scripts/benchmark_dss_vs_baselines.py --days 90
```

#### Results Summary:

| Pricing Strategy | Units Sold | Total Revenue | Gross Profit | Gross Margin % | Avg Price | Profit Lift vs Cost-Plus | Profit Lift vs Comp Match |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Cost-Plus (+25%)** | 71,940 | $3,812,450.20 | $762,490.04 | 20.0% | $52.99 | *baseline* | — |
| **Competitor-Matching** | 68,120 | $4,015,310.80 | $798,240.15 | 19.9% | $58.94 | +4.7% | *baseline* |
| **DSS Optimization** | **74,830** | **$4,380,920.40** | **$884,610.90** | **20.2%** | **$58.54** | **+16.0%** | **+10.8%** |

#### Why DSS Outperforms:
1. **Asymmetric Elasticity Exploitation**: On inelastic products ($|\varepsilon| < 1$), Cost-Plus underprices and leaves consumer surplus on the table; DSS safely expands margin without sacrificing volume.
2. **Avoidance of Destructive Price Wars**: Competitor-Matching blindly follows rivals into ungrounded price cuts; DSS enforces hard margin guardrails ($p \ge \text{cost} / (1 - m_{\min})$).
3. **Cannibalization Awareness**: DSS incorporates substitute cross-elasticity ($\kappa$), preventing discounts on low-margin SKUs that would destroy sales of higher-margin catalog siblings.

---

### 2. Elasticity Recovery Experiment

Competitor prices act as an omitted variable: rivals *react* to our price changes, which biases naive elasticity estimates toward zero. Incorporating competitive intelligence from Module A eliminates this bias:

| Specification | Mean Abs. Error | **Systematic Bias** | 90% CI Coverage | Mean R² |
| :--- | :---: | :---: | :---: | :---: |
| Naive OLS (all 24 SKUs) | 21.5% | **+17.0%** | 66.7% | 0.498 |
| Naive OLS (19 SKUs with competitor coverage) | 18.5% | **+12.8%** | 78.9% | 0.493 |
| **Enriched OLS (+ competitor price index & OOS share)** | **17.8%** | **+0.4%** | **68.4%** | **0.504** |

Competitive intelligence removes **~97% of systematic estimation bias**. Reproduce with:
```powershell
python scripts/check_elasticity_recovery.py
```

---

## Technology Stack

| Layer | Technologies |
| :--- | :--- |
| **Backend API** | Python 3.11+, FastAPI, SQLAlchemy 2.0, Pydantic v2, Alembic |
| **Database** | PostgreSQL 16 + `pgvector` (vector embeddings) + `pg_trgm` (trigram search) |
| **Analytics & Econometrics** | NumPy, pandas (<3.0), statsmodels (HC3 robust standard errors), SciPy, scikit-learn |
| **Frontend Web App** | Next.js 15 (App Router), React 19, TypeScript, Vanilla CSS + Tailwind v4, Recharts |
| **Local LLM & Grounding** | Ollama (`llama3.2`), custom regex-based numeric-grounding verification engine |
| **Operator CLI** | Typer, Rich |

---

## Quickstart

### Prerequisites
* Docker Desktop (running)
* Python 3.11+
* Node.js 20+

### Step-by-Step Setup

```powershell
# 1. Environment configuration
Copy-Item .env.example .env

# 2. Start PostgreSQL container with pgvector
docker compose -f infra/docker-compose.yml up -d postgres

# 3. Setup & Activate Backend Virtual Environment
# From project root (D:\projects\DSS):
.\.venv\Scripts\Activate.ps1

# (Or if already inside the backend directory: ..\.venv\Scripts\Activate.ps1)
cd backend
pip install -e ".[dev]"
alembic upgrade head

# 4. Seed database with synthetic market history
python -m app.cli seed --days 730

# 5. Start Backend API server (runs on port 8010)
uvicorn app.main:app --reload --port 8010
```

In a new terminal:
```powershell
# 6. Start Frontend Web Application (runs on port 3000)
cd frontend
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) and sign in with the default admin credentials:
* **Email**: `admin@dss-demo.com`
* **Password**: `admin12345`

---

## Operator CLI (`dss`)

The backend includes a comprehensive operator CLI for orchestrating pipelines and models:

```powershell
# Catalog & Seed Management
python -m app.cli seed --days 730 --seed 20260927   # Seed market data
python -m app.cli reset                              # Purge data, keep schema
python -m app.cli info                               # Table row counts

# Module A: Ingestion & Matching
python -m app.cli ingest                             # Poll feeds and check alerts
python -m app.cli match                              # Run GTIN + embedding matching
python -m app.cli alerts                             # List open pricing alerts

# Module B: Econometrics & Forecasting
python -m app.cli fit-elasticity                     # Fit log-log OLS models with HC3 CIs
python -m app.cli forecast --horizon 30              # 30-day ETS demand forecast + WAPE

# Module C: Pricing Optimization
python -m app.cli recommend --objective margin       # Generate constrained recommendations
python -m app.cli recommend --objective revenue      # Optimize for revenue volume

# Module D: Scenario Sandbox
python -m app.cli simulate --name "Promo Test"       # Run Monte Carlo scenario simulation

# Module F: Financials & CFO Advisor
python -m app.cli seed-finance                       # Seed 8 quarters P&L/BS/CF & detect anomalies
python -m app.cli cfo-report                         # Generate verified grounded CFO report
```

---

## End-to-End Walkthrough Script

To execute the entire 6-module decision lifecycle in a single automated command:

```powershell
cd backend
python scripts/demo_walkthrough.py
```

This runs:
1. `seed` $\to$ 2. `ingest` & `match` $\to$ 3. `fit-elasticity` $\to$ 4. `forecast` $\to$ 5. `recommend` $\to$ 6. `simulate` $\to$ 7. `seed-finance` $\to$ 8. `cfo-report`.

---

## Frontend Web Application Pages

The frontend provides dedicated decision-support screens for every operational persona:

* **[Executive Overview](file:///d:/projects/DSS/frontend/app/page.tsx)** (`/`): High-level KPIs, revenue & margin trajectories, catalog health, active alerts, and top/all SKU market deviation toggle.
* **[Product Catalog](file:///d:/projects/DSS/frontend/app/catalog/page.tsx)** (`/catalog`): Searchable catalog, unit costs, pricing margins, stock states.
* **[Market Watch](file:///d:/projects/DSS/frontend/app/market/page.tsx)** (`/market`): Competitor price index tracker, competitor landed prices, out-of-stock monitor.
* **[Match Review Queue](file:///d:/projects/DSS/frontend/app/listings/page.tsx)** (`/listings`): Human-in-the-loop review of competitor listings (auto-accepted, pending, needs review, confirmed, rejected).
* **[SKU Workbench](file:///d:/projects/DSS/frontend/app/workbench/page.tsx)** (`/workbench`): Econometric demand curves, 90% confidence intervals, price variation diagnostics, 30-day Holt-Winters ETS forward forecast.
* **[Price Recommendations](file:///d:/projects/DSS/frontend/app/recommendations/page.tsx)** (`/recommendations`): Constrained price recommendations under Margin/Revenue/Penetration objectives, binding guardrails, recommendation lifecycle (approve/reject/apply).
* **[Scenario Sandbox](file:///d:/projects/DSS/frontend/app/sandbox/page.tsx)** (`/sandbox`): Interactive what-if pricing experiments with Monte Carlo fan charts and competitor reaction modeling.
* **[CFO Strategic Advisor](file:///d:/projects/DSS/frontend/app/advisor/page.tsx)** (`/advisor`): Financial KPI cockpit (EBITDA, CAC, LTV, Runway, CCC), anomaly detection feed, and grounded AI strategic synthesis with 100% verified numeric facts.

---

## API Sitemap (`/api`)

```
System & Health:
  GET  /api/health                      - Service health status and version

Authentication:
  POST /api/auth/login                  - Issue JWT access token
  GET  /api/auth/me                     - Current authenticated user context

Catalog & Competitive Intelligence (Module A):
  GET  /api/catalog/products            - List products with margins and stock state
  GET  /api/catalog/products/{id}       - Product details with competitor summary
  GET  /api/catalog/categories          - Category hierarchy
  GET  /api/market/competitors          - List competitors and listing counts
  GET  /api/market/snapshot             - Real-time catalog price gap vs competitor indices
  GET  /api/market/listings             - Competitor listings with case-insensitive status filters
  GET  /api/market/listings/{id}/history - Historical price observation timeseries
  GET  /api/market/alerts               - Open competitive intelligence alerts
  POST /api/market/alerts/{id}/status   - Acknowledge or resolve an alert

Matching & Human Review (Module A):
  GET  /api/matching/review             - Review queue for uncertain matches
  POST /api/matching/{id}/confirm       - Human confirm listing match to SKU
  POST /api/matching/{id}/reject        - Reject proposed match
  POST /api/matching/{id}/rematch       - Trigger on-demand re-embedding match
  POST /api/matching/trigger            - Run batch matching pipeline

Econometrics & Demand Forecasting (Module B):
  POST /api/analytics/elasticity/fit    - Fit log-log OLS models with HC3 robust CIs
  GET  /api/analytics/elasticity        - Champion elasticity estimates across catalog
  GET  /api/analytics/elasticity/{id}   - Product-level elasticity and cross-terms
  GET  /api/analytics/workbench/{id}    - Complete SKU workbench (demand curve + forecast)
  POST /api/analytics/forecast/fit      - Execute forward ETS demand forecast
  GET  /api/analytics/forecast/{id}     - Retrieve 30-day forecast points

Pricing Optimization & Sandbox (Modules C & D):
  GET  /api/pricing/recommendations     - Current generated price recommendations
  POST /api/pricing/recommendations/generate - Run optimization under objective
  POST /api/pricing/recommendations/{id}/action - Lifecycle decision (approve/reject/apply)
  GET  /api/pricing/sandbox/runs        - List historical what-if simulation runs
  POST /api/pricing/sandbox/simulate    - Launch Monte Carlo what-if simulation

Financial Performance & Grounded CFO Advisor (Module F):
  POST /api/finance/seed                - Seed 8 quarters of financial statements
  POST /api/finance/upload              - Ingest custom quarterly statement lines
  GET  /api/finance/periods             - Statement periods with summary ratios
  GET  /api/finance/periods/{id}        - Detailed period line items and KPIs
  GET  /api/finance/findings            - Financial anomaly and variance findings
  POST /api/finance/advisor/generate    - Synthesize 100% grounded CFO narrative
  GET  /api/finance/advisor/latest      - Retrieve most recent verified executive briefing
```

---

## Commercial Roadmap & Enterprise Transition

> For the comprehensive strategic gap analysis, target ICP, and packaging tiers, see the full artifact:  
> [commercial_product_roadmap_and_gap_analysis.md](file:///C:/Users/Karim%20ElWaly/.gemini/antigravity-ide/brain/b74a23ae-a1e4-440f-b924-6cc135bfec49/commercial_product_roadmap_and_gap_analysis.md)

### The 6 Pillars to Commercial SaaS Viability

1. **Closed-Loop Execution (Write-Back API)**: Direct push to Shopify Variant API, Amazon SP-API repricers, and ERP price books (SAP S/4HANA, NetSuite) with an emergency 60-second kill switch.
2. **Industrial Data Ingestion**: Webhook sync with eCommerce storefronts, Snowflake/BigQuery data warehouse historical order pipelines, and managed proxy scrapers (Bright Data / Oxylabs) to bypass Cloudflare.
3. **Advanced Retail Econometrics**: Tobit regression to eliminate zero-demand bias during stockouts, Weeks-of-Supply (WOS) inventory yield constraints, and promotional/holiday indicator variables.
4. **Enterprise Multi-Tenancy & Governance**: PostgreSQL Row-Level Security (RLS) enforcement, SAML 2.0 / Okta SSO, SCIM provisioning, and tiered approval matrices (Merchandiser $\to$ Director $\to$ CFO).
5. **Distributed Scalability**: Celery + Redis worker cluster replacing in-memory scheduling, partitioned HNSW vector indexing for 500k+ competitor listings, and HiGHS/OR-Tools MILP solvers.
6. **Commercial Packaging**: Growth ($1,500/mo), Scale ($4,500/mo), and Enterprise ($10,000+/mo) subscription tiers with 15-minute self-service store onboarding.

---

## Quality Gates & Verification

```powershell
# Backend verification
cd backend
pytest tests/ -q                             # Test suite (all modules)
python scripts/smoke_api.py                  # API route smoke test
python scripts/benchmark_dss_vs_baselines.py # Pricing policy benchmark

# Frontend verification
cd ../frontend
npm run typecheck                            # TypeScript strict type check
npm run build                                # Production build verification
```

---

## Key Design Principles

1. **Defensible by Construction**: Every price recommendation is derived from a formal objective function, bounded by explicit economic guardrails, and carries traceable audit records.
2. **Ground Truth Separation**: Ground-truth market generator parameters are strictly isolated in `simulator/` and never accessed by operational DSS services.
3. **No Unchecked Hallucinations**: The strategic CFO advisor passes every AI-generated token through a strict numeric-grounding validator. Any figure not present in the verified facts JSON triggers immediate rejection.
4. **Resilient Offline Fallback**: All analytical, optimization, and advisory endpoints function completely offline without external cloud dependencies.
