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
    Organization,
    PriceObservation,
    Product,
    SalesFact,
)
from app.models.enums import AlertStatus
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


if __name__ == "__main__":
    app()
