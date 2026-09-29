"""Test configuration.

Database-backed tests are skipped automatically when no PostgreSQL instance is
reachable, so ``pytest`` always runs the pure-Python suites (simulator,
optimization, guardrails) on a laptop with nothing else started.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings


def _database_available() -> bool:
    try:
        from app.db import get_engine

        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(
    not _database_available(),
    reason="PostgreSQL is not reachable; start `infra/docker-compose.yml`.",
)


@pytest.fixture(scope="session")
def settings():
    return get_settings()


@pytest.fixture(autouse=True, scope="session")
def _enforce_real_auth():
    """Run the suite with authentication switched on.

    A developer's `.env` may set ``DSS_AUTH_DISABLED=true`` to skip the login
    screen. If that leaked into the tests, every assertion about 401s would
    pass vacuously and the suite would stop protecting the thing it is there to
    protect. Tests that want the bypass opt in via the ``auth_disabled``
    fixture.
    """
    settings = get_settings()
    original = settings.auth_disabled
    settings.auth_disabled = False
    yield
    settings.auth_disabled = original


@pytest.fixture
def auth_disabled():
    """Temporarily run with the login bypass active."""
    settings = get_settings()
    original = settings.auth_disabled
    settings.auth_disabled = True
    yield settings
    settings.auth_disabled = original


@pytest.fixture
def db_session() -> Iterator[Session]:
    """A session wrapped in a transaction that is always rolled back."""
    from app.db import get_engine, get_session_factory

    connection = get_engine().connect()
    transaction = connection.begin()
    session = get_session_factory()(bind=connection)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture(scope="session")
def small_market():
    from simulator import MarketSimulator, default_electronics_market

    config = default_electronics_market(seed=7, history_days=200, end_date=dt.date(2026, 6, 30))
    return MarketSimulator(config).generate()
