# Revenue & Pricing DSS — Backend Service

FastAPI service and econometric analytics engine powering the Revenue & Pricing Decision Support System.

## Architecture

| Module | Core Services & Endpoints | Econometric & Mathematical Engine |
| --- | --- | --- |
| **A. Ingestion & Matching** | `app/services/ingestion.py`, `app/services/matching.py`, `app/api/routes/market.py` | Two-stage matcher: GTIN exact $\to$ pgvector cosine similarity ($\ge 0.86$ auto-accept) $\to$ review queue |
| **B. Elasticity & Forecasting** | `app/services/elasticity.py`, `app/services/forecasting.py`, `app/api/routes/analytics.py` | Log-log OLS with HC3 robust covariance, 90% CIs, pooled category fallback, 30d Holt-Winters ETS + WAPE backtest |
| **C. Dynamic Pricing** | `app/services/optimization/`, `app/api/routes/pricing.py` | Profit/Revenue/Penetration grid evaluation, substitute cross-elasticity cannibalization penalty, hard guardrails |
| **D. Scenario Sandbox** | `app/services/simulation/scenario.py`, `app/api/routes/pricing.py` | Monte Carlo parameter draws $\varepsilon \sim \mathcal{N}$, competitor reaction dynamics, P05/P25/P50/P75/P95 bands |
| **F. Corporate Finance & CFO Advisor** | `app/services/finance/`, `app/llm/`, `app/api/routes/finance.py` | Financial KPI engine (EBITDA, CAC, LTV, Runway, CCC), robust z-score anomaly detector, Ollama LLM + numeric grounding |
| **Ground-Truth Simulator** | `simulator/` | Ground-truth demand generator with known curvatures, competitor reaction loops, and Poisson arrivals |

## Operator CLI

The CLI provides commands for running each module:

```powershell
python -m app.cli seed --days 730
python -m app.cli ingest
python -m app.cli match
python -m app.cli fit-elasticity
python -m app.cli forecast
python -m app.cli recommend --objective margin
python -m app.cli simulate
python -m app.cli seed-finance
python -m app.cli cfo-report
```

## Validation & Benchmark Scripts

* `scripts/benchmark_dss_vs_baselines.py`: Benchmark DSS vs Cost-Plus (+25%) vs Competitor-Matching over 90 days.
* `scripts/demo_walkthrough.py`: Automated end-to-end execution of all 6 modules.
* `scripts/check_elasticity_recovery.py`: Validates that Module A competitive intelligence eliminates elasticity bias.
* `scripts/check_matching.py`: Evaluates product matching precision/recall against distractor listings.
* `scripts/smoke_api.py`: Automated FastAPI route smoke test.

## Running Tests

```powershell
pytest tests/ -v
```
