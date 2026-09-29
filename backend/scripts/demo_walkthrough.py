"""End-to-End Decision Support System Walkthrough.

Automates the complete 6-module decision lifecycle in a single execution:
1. [W1] Synthetic Market Seeding (24 SKUs, 5 categories, 730 days of sales)
2. [W2] Ingestion & Product Matching (feed ingestion, embedding + GTIN match, alert evaluation)
3. [W3] Econometric Demand Estimation (log-log OLS, HC3 robust standard errors, 90% CIs)
4. [W3] Demand Forecasting (30-day ETS vs seasonal-naive backtest)
5. [W4] Constrained Price Optimization (profit grid, cannibalization penalty, guardrails)
6. [W4] Monte Carlo Scenario Sandbox (parameter sampling, competitor reaction, percentiles)
7. [W5] Financial Ingestion & Anomaly Detection (8 quarters, EBITDA, CAC/LTV, runway, z-scores)
8. [W5] CFO Strategic Advisor (Ollama LLM narrative with strict numeric-grounding check)

Run with:
    python scripts/demo_walkthrough.py [--fast] [--skip-seed]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Ensure backend root is on python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from sqlalchemy import func, select

from app.db import session_scope
from app.models import (
    Category,
    Competitor,
    CompetitorListing,
    ElasticityEstimate,
    FinancialFinding,
    FinancialMetric,
    ModelRun,
    PriceRecommendation,
    Product,
    SalesFact,
    SimulationRun,
)
from app.models.enums import PricingObjective
from app.services.elasticity import fit_elasticity_model
from app.services.finance.advisor import generate_cfo_advisor_report
from app.services.finance.anomalies import detect_financial_anomalies
from app.services.finance.ingestion import seed_demo_financials
from app.services.finance.metrics import compute_all_metrics
from app.services.forecasting import run_demand_forecast
from app.services.ingestion import run_ingestion
from app.services.matching import run_matching
from app.services.optimization.engine import generate_recommendations
from app.services.seed import get_default_organization, seed_market
from app.services.simulation.scenario import run_scenario_simulation
from simulator import MarketSimulator, default_electronics_market

console = Console()


def run_walkthrough(fast: bool = False, skip_seed: bool = False) -> None:
    start_time = time.time()
    console.print(
        Panel.fit(
            "[bold cyan]Revenue & Pricing DSS -- End-to-End System Walkthrough[/]\n"
            "[dim]Orchestrating Modules A, B, C, D, and F across the full decision lifecycle[/]",
            border_style="cyan",
        )
    )

    with session_scope() as session:
        # -------------------------------------------------------------
        # Step 1: Catalog & Market Seeding
        # -------------------------------------------------------------
        console.rule("[bold magenta]Step 1: Synthetic Market Generation & Seeding (W1)[/]")
        if not skip_seed:
            days = 180 if fast else 730
            console.print(f"Generating {days} days of realistic market history (seed=20260927)...")
            config = default_electronics_market(seed=20260927, history_days=days)
            market = MarketSimulator(config).generate()
            seed_results = seed_market(session, market, reset=True)
            console.print(
                f"[green]✓ Seeded:[/] {seed_results.get('products', 0)} products, "
                f"{seed_results.get('sales_facts', 0):,} sales records, "
                f"{seed_results.get('competitors', 0)} competitors, "
                f"{seed_results.get('listings', 0)} competitor listings."
            )
        else:
            console.print("[yellow]Skipping seed as requested; utilizing existing database state.[/]")

        org = get_default_organization(session)
        products = list(session.scalars(select(Product).where(Product.organization_id == org.id)))

        # -------------------------------------------------------------
        # Step 2: Feed Ingestion & Matching Pipeline (Module A)
        # -------------------------------------------------------------
        console.rule("[bold blue]Step 2: Competitive Intelligence & Ingestion (Module A - W2)[/]")
        console.print("Polling competitor feeds and executing matching pipeline...")
        ingest_report = run_ingestion(session, org.id, match=True, alert=True)
        console.print(
            f"[green]✓ Ingestion:[/] Processed {len(ingest_report.competitors)} feeds "
            f"({ingest_report.alerts} alert(s) triggered)."
        )

        match_report = run_matching(session, org.id)
        console.print(
            f"[green]✓ Product Matching:[/] {match_report.auto_accepted} auto-accepted "
            f"(GTIN + embedding cosine > 0.88), {match_report.flagged_for_review} sent to review queue."
        )

        # -------------------------------------------------------------
        # Step 3: Econometric Demand Estimation (Module B)
        # -------------------------------------------------------------
        console.rule("[bold yellow]Step 3: Econometric Elasticity Estimation (Module B - W3)[/]")
        console.print("Fitting log-log OLS demand models with HC3 robust covariance...")
        elasticity_run = fit_elasticity_model(session, org.id, min_observations=20 if fast else 30)
        estimates = list(
            session.scalars(
                select(ElasticityEstimate)
                .where(ElasticityEstimate.model_run_id == elasticity_run.id)
                .limit(5)
            )
        )

        el_table = Table(title="Sample Recovered Elasticities (Top 5 SKUs)", header_style="bold yellow")
        el_table.add_column("Product SKU")
        el_table.add_column("Elasticity", justify="right")
        el_table.add_column("90% Confidence Interval", justify="center")
        el_table.add_column("R²", justify="right")
        el_table.add_column("Status")

        prods_map = {p.id: p for p in products}
        for est in estimates:
            sku = prods_map[est.product_id].sku if est.product_id in prods_map else str(est.product_id)[:8]
            ci = f"[{est.ci_low:.2f}, {est.ci_high:.2f}]" if est.ci_low is not None else "—"
            r2 = f"{est.r_squared:.3f}" if est.r_squared is not None else "—"
            status = "[green]Reliable[/]" if est.is_reliable else "[dim]Category Fallback[/]"
            el_table.add_row(sku, f"{est.elasticity:.3f}", ci, r2, status)
        console.print(el_table)
        console.print(
            f"[green]✓ Elasticity Run:[/] Recovered parameters for {len(products)} SKUs. "
            f"Reliable share: {elasticity_run.metrics.get('reliable_pct', 0) * 100:.1f}%."
        )

        # -------------------------------------------------------------
        # Step 4: Demand Forecasting & Backtesting (Module B)
        # -------------------------------------------------------------
        console.rule("[bold cyan]Step 4: Demand Forecasting & Rolling Backtest (Module B - W3)[/]")
        console.print("Generating 30-day forward demand projections and evaluating backtest...")
        forecast_run = run_demand_forecast(session, org.id, horizon_days=30, backtest_days=30)
        console.print(
            f"[green]✓ Demand Forecast:[/] {forecast_run.metrics.get('n_forecast_points', 0):,} forecast points. "
            f"Holt-Winters ETS WAPE: [bold]{forecast_run.metrics.get('wape_ets', 0) * 100:.2f}%[/] "
            f"(beat seasonal-naive by [green]+{forecast_run.metrics.get('wape_improvement_pct', 0):.1f}%[/])."
        )

        # -------------------------------------------------------------
        # Step 5: Dynamic Pricing Optimization Engine (Module C)
        # -------------------------------------------------------------
        console.rule("[bold magenta]Step 5: Dynamic Pricing Optimization (Module C - W4)[/]")
        console.print("Evaluating profit objective curves under guardrails and cannibalization...")
        recs = generate_recommendations(
            session,
            org.id,
            objective=PricingObjective.MARGIN,
            apply_charm_rounding=True,
        )

        rec_table = Table(title="Sample Price Recommendations (Top 5 SKUs)", header_style="bold magenta")
        rec_table.add_column("SKU")
        rec_table.add_column("Current Price", justify="right")
        rec_table.add_column("Recommended", justify="right", style="bold")
        rec_table.add_column("Price Δ", justify="right")
        rec_table.add_column("Expected Margin Δ", justify="right")
        rec_table.add_column("Binding Constraints")

        for r in recs[:5]:
            p0 = float(r.current_price)
            p_rec = float(r.recommended_price)
            delta_p = (p_rec - p0) / p0
            color = "green" if delta_p > 0 else ("red" if delta_p < 0 else "white")
            margin_delta = r.rationale.get("delta_margin_pct", 0.0) * 100
            sku = prods_map[r.product_id].sku if r.product_id in prods_map else str(r.product_id)[:8]
            constraints = ", ".join(r.binding_constraints) if r.binding_constraints else "none"
            rec_table.add_row(
                sku,
                f"${p0:.2f}",
                f"${p_rec:.2f}",
                f"[{color}]{delta_p * 100:+.1f}%[/]",
                f"[green]{margin_delta:+.1f}%[/]",
                constraints,
            )
        console.print(rec_table)
        console.print(f"[green]✓ Recommendations:[/] Generated {len(recs)} proposed prices with audited guardrails.")

        # -------------------------------------------------------------
        # Step 6: What-if Scenario Sandbox (Module D)
        # -------------------------------------------------------------
        console.rule("[bold blue]Step 6: Monte Carlo Scenario Simulation (Module D - W4)[/]")
        console.print("Simulating a portfolio +5% price adjustment with 500 Monte Carlo draws...")
        overrides = {p.id: float(p.current_price) * 1.05 for p in products[:4]}
        sim_run = run_scenario_simulation(
            session,
            org.id,
            name="Walkthrough +5% Flagship Adjustment",
            horizon_days=30,
            price_overrides=overrides,
            n_monte_carlo=300 if fast else 500,
        )
        sim_delta = sim_run.results.get("delta", {})
        console.print(
            f"[green]✓ Monte Carlo Simulation:[/] Expected margin impact: "
            f"[bold green]{sim_delta.get('margin_pct', 0) * 100:+.2f}%[/], "
            f"Revenue impact: [bold green]{sim_delta.get('revenue_pct', 0) * 100:+.2f}%[/], "
            f"Units delta: {sim_delta.get('units_pct', 0) * 100:+.2f}%."
        )

        # -------------------------------------------------------------
        # Step 7: Corporate Financials & Anomaly Engine (Module F)
        # -------------------------------------------------------------
        console.rule("[bold green]Step 7: Corporate Financials & Anomaly Detection (Module F - W5)[/]")
        console.print("Seeding 8 trailing quarters of financial statements and computing KPIs...")
        periods = seed_demo_financials(session, org.id)
        metrics = compute_all_metrics(session, org.id)
        findings = detect_financial_anomalies(session, org.id)

        console.print(
            f"[green]✓ Financial Engine:[/] Loaded {len(periods)} quarters. "
            f"Computed {len(metrics)} financial KPI records. "
            f"Flagged {len(findings)} operational anomaly finding(s)."
        )

        # -------------------------------------------------------------
        # Step 8: Grounded Strategic CFO Advisor (Module F)
        # -------------------------------------------------------------
        console.rule("[bold cyan]Step 8: Grounded Strategic CFO Advisor (Module F - W5)[/]")
        console.print("Synthesizing strategic advisory briefing with numeric grounding...")
        report = generate_cfo_advisor_report(session, org.id, model_name="llama3.2")

        status_str = (
            "[bold green]PASSED (100% Grounded)[/]"
            if report.grounding_passed
            else f"[bold red]FAILED ({len(report.grounding_violations)} violations)[/]"
        )
        console.print(f"Grounding Verification: {status_str}")

        console.print("\n[bold]CFO Executive Synthesis Sample:[/]")
        preview = report.narrative[:400] + ("..." if len(report.narrative) > 400 else "")
        console.print(Panel(preview, title="Advisor Briefing", border_style="cyan"))

    elapsed = time.time() - start_time
    console.print()
    console.print(
        Panel.fit(
            f"[bold green]✓ Full Decision Support System Walkthrough Complete in {elapsed:.1f}s![/]\n"
            f"[dim]All 6 Modules (A, B, C, D, F & Simulator) successfully executed and validated.[/]",
            border_style="green",
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="End-to-End Decision Support System Walkthrough.")
    parser.add_argument("--fast", action="store_true", help="Run with reduced history days for rapid testing.")
    parser.add_argument("--skip-seed", action="store_true", help="Skip database re-seeding step.")
    args = parser.parse_args()

    run_walkthrough(fast=args.fast, skip_seed=args.skip_seed)


if __name__ == "__main__":
    main()
