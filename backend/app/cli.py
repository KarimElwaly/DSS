"""Operator CLI: ``dss <command>``."""

from __future__ import annotations

import datetime as dt

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db import session_scope
from app.models import (
    Alert,
    Category,
    Competitor,
    CompetitorListing,
    ElasticityEstimate,
    ModelRun,
    Organization,
    PriceObservation,
    Product,
    SalesFact,
)
from app.models.enums import AlertStatus
from app.services.elasticity import fit_elasticity_model
from app.services.forecasting import run_demand_forecast
from app.services.ingestion import run_ingestion
from app.services.matching import run_matching
from app.services.seed import get_default_organization, reset_database, seed_market
from simulator import MarketSimulator, default_electronics_market


app = typer.Typer(help="Revenue & Pricing DSS operator CLI.", no_args_is_help=True)
console = Console()


@app.callback()
def _main() -> None:
    configure_logging()


@app.command()
def seed(
    days: int = typer.Option(730, help="Days of synthetic sales history to generate."),
    seed_value: int = typer.Option(20260927, "--seed", help="RNG seed (reproducibility)."),
    end_date: str | None = typer.Option(None, help="ISO end date; defaults to today."),
    reset: bool = typer.Option(True, help="Purge existing rows before seeding."),
) -> None:
    """Generate the synthetic market and load it into the database."""
    parsed_end = dt.date.fromisoformat(end_date) if end_date else None
    config = default_electronics_market(seed=seed_value, history_days=days, end_date=parsed_end)

    console.print(f"[bold]Generating[/] {days} days of history (seed={seed_value})...")
    market = MarketSimulator(config).generate()

    with session_scope() as session:
        result = seed_market(session, market, reset=reset)

    table = Table(title="Seed complete", show_header=False)
    for key, value in result.items():
        table.add_row(str(key), str(value))
    console.print(table)
    console.print(
        f"[green]Login[/] with {get_settings().admin_email} / "
        f"{'*' * len(get_settings().admin_password)}"
    )


@app.command()
def reset() -> None:
    """Delete every row (schema untouched)."""
    if not typer.confirm("This deletes ALL data in the DSS database. Continue?"):
        raise typer.Abort
    with session_scope() as session:
        reset_database(session)
    console.print("[yellow]Database cleared.[/]")


@app.command()
def info() -> None:
    """Show row counts for the main tables."""
    with session_scope() as session:
        try:
            org = get_default_organization(session)
        except LookupError as exc:
            console.print(f"[red]{exc}[/]")
            raise typer.Exit(code=1) from exc

        counts = {
            "organizations": session.scalar(select(func.count()).select_from(Organization)),
            "categories": session.scalar(select(func.count()).select_from(Category)),
            "products": session.scalar(select(func.count()).select_from(Product)),
            "sales_facts": session.scalar(select(func.count()).select_from(SalesFact)),
            "competitors": session.scalar(select(func.count()).select_from(Competitor)),
            "listings": session.scalar(select(func.count()).select_from(CompetitorListing)),
            "price_observations": session.scalar(
                select(func.count()).select_from(PriceObservation)
            ),
        }

    table = Table(title=f"DSS status - {org.name}")
    table.add_column("Table")
    table.add_column("Rows", justify="right")
    for key, value in counts.items():
        table.add_row(key, f"{value:,}")
    console.print(table)


@app.command()
def match(
    only_unmatched: bool = typer.Option(
        False, help="Re-score only listings with no product linked."
    ),
) -> None:
    """Embed the catalog and match competitor listings to our SKUs."""
    with session_scope() as session:
        org = get_default_organization(session)
        report = run_matching(session, org.id, only_unmatched=only_unmatched)

    table = Table(title="Matching complete")
    table.add_column("Outcome")
    table.add_column("Listings", justify="right")
    for key, value in report.as_dict().items():
        table.add_row(key.replace("_", " "), f"{value:,}")
    console.print(table)
    console.print("[dim]Run scripts/check_matching.py to score these against ground truth.[/]")


@app.command()
def ingest(
    match_new: bool = typer.Option(True, "--match/--no-match", help="Match new listings."),
    raise_alerts: bool = typer.Option(True, "--alerts/--no-alerts", help="Evaluate alert rules."),
) -> None:
    """Poll every active competitor feed once."""
    with session_scope() as session:
        org = get_default_organization(session)
        report = run_ingestion(session, org.id, match=match_new, alert=raise_alerts)

        feeds = Table(title="Competitor feeds")
        feeds.add_column("Competitor")
        feeds.add_column("New listings", justify="right")
        feeds.add_column("New prices", justify="right")
        feeds.add_column("Status")
        for row in report.competitors:
            feeds.add_row(
                row.competitor_slug,
                f"{row.new_listings:,}",
                f"{row.new_observations:,}",
                f"[red]{row.error}[/]" if row.error else "[green]ok[/]",
            )
        console.print(feeds)

        if report.matched:
            console.print(f"Matching: {report.matched}")
        if report.alerts:
            console.print(f"Alerts: {report.alerts}")


@app.command()
def alerts(limit: int = typer.Option(20, help="How many open alerts to show.")) -> None:
    """List the open alerts raised by the ingestion pipeline."""
    with session_scope() as session:
        org = get_default_organization(session)
        rows = list(
            session.scalars(
                select(Alert)
                .where(Alert.organization_id == org.id, Alert.status == AlertStatus.OPEN)
                .order_by(Alert.created_at.desc())
                .limit(limit)
            )
        )
        if not rows:
            console.print("[green]No open alerts.[/]")
            return

        table = Table(title=f"{len(rows)} open alert(s)")
        table.add_column("Severity")
        table.add_column("Type")
        table.add_column("Title")
        colours = {"critical": "red", "warning": "yellow", "info": "cyan"}
        for alert in rows:
            colour = colours.get(alert.severity.value, "white")
            table.add_row(
                f"[{colour}]{alert.severity.value}[/]",
                alert.alert_type.value,
                alert.title,
            )
        console.print(table)


@app.command(name="fit-elasticity")
def fit_elasticity_cmd(
    min_obs: int = typer.Option(30, "--min-obs", help="Minimum observations required for SKU OLS."),
) -> None:
    """Fit econometric demand models and estimate elasticity with HC3 CIs."""
    with session_scope() as session:
        org = get_default_organization(session)
        console.print("[bold]Fitting log-log OLS elasticity models...[/]")
        model_run = fit_elasticity_model(session, org.id, min_observations=min_obs)

        # Show top estimates
        estimates = list(
            session.scalars(
                select(ElasticityEstimate)
                .where(ElasticityEstimate.model_run_id == model_run.id)
                .order_by(ElasticityEstimate.elasticity)
            )
        )
        prods = {p.id: p for p in session.scalars(select(Product))}

        table = Table(title=f"Elasticity Estimates ({model_run.label})")
        table.add_column("SKU")
        table.add_column("Method")
        table.add_column("Elasticity", justify="right")
        table.add_column("90% CI", justify="center")
        table.add_column("R²", justify="right")
        table.add_column("Reliable?")

        for est in estimates:
            ci_str = f"[{est.ci_low:.2f}, {est.ci_high:.2f}]" if est.ci_low is not None else "—"
            r2_str = f"{est.r_squared:.3f}" if est.r_squared is not None else "—"
            rel_str = "[green]Yes[/]" if est.is_reliable else "[yellow]No[/]"
            sku = prods[est.product_id].sku if est.product_id in prods else str(est.product_id)[:8]
            table.add_row(sku, est.method.value, f"{est.elasticity:.3f}", ci_str, r2_str, rel_str)
        console.print(table)
        console.print(
            f"Fitted [bold]{len(estimates)}[/] products. "
            f"Reliable: [green]{model_run.metrics.get('n_reliable', 0)}[/] "
            f"({model_run.metrics.get('reliable_pct', 0) * 100:.1f}%), "
            f"Mean R²: {model_run.metrics.get('mean_r_squared', 0):.3f}"
        )


@app.command(name="forecast")
def forecast_cmd(
    horizon: int = typer.Option(30, "--horizon", help="Forecast horizon in days."),
    backtest: int = typer.Option(30, "--backtest", help="Backtest evaluation period in days."),
) -> None:
    """Generate forward demand forecasts and report rolling backtest WAPE."""
    with session_scope() as session:
        org = get_default_organization(session)
        console.print(f"[bold]Running demand forecast (horizon={horizon}d, backtest={backtest}d)...[/]")
        model_run = run_demand_forecast(session, org.id, horizon_days=horizon, backtest_days=backtest)

        table = Table(title="Demand Forecast & Backtest Summary")
        table.add_column("Metric")
        table.add_column("Value", justify="right")

        table.add_row("Forecast points generated", f"{model_run.metrics.get('n_forecast_points', 0):,}")
        table.add_row("Backtest evaluation days", f"{model_run.metrics.get('backtest_days', 0)} days")
        table.add_row("ETS WAPE", f"{model_run.metrics.get('wape_ets', 0) * 100:.2f}%")
        table.add_row("Seasonal-Naive WAPE", f"{model_run.metrics.get('wape_seasonal_naive', 0) * 100:.2f}%")
        table.add_row("WAPE Improvement", f"[green]+{model_run.metrics.get('wape_improvement_pct', 0):.2f}%[/]")
        table.add_row("ETS Mean Absolute Error (MAE)", f"{model_run.metrics.get('mae_ets', 0):.2f} units")
        console.print(table)


if __name__ == "__main__":
    app()

