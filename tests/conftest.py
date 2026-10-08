"""Pytest bootstrap with a process-local SQLite database by default."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile

import pytest


_RUN_INTEGRATION = os.getenv("RUN_INTEGRATION_TESTS") == "1"
_TEST_DATABASE_PATH = Path(tempfile.gettempdir()) / f"lifelog-pytest-{os.getpid()}.sqlite3"

if _RUN_INTEGRATION:
    integration_url = os.getenv("INTEGRATION_DATABASE_URL")
    if not integration_url:
        raise RuntimeError(
            "RUN_INTEGRATION_TESTS=1 requires INTEGRATION_DATABASE_URL; "
            "tests never fall back to .env or a production database"
        )
    os.environ["DATABASE_URL"] = integration_url
else:
    os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{_TEST_DATABASE_PATH}"

os.environ.setdefault("DEFAULT_USER_TELEGRAM_ID", "6944966420")
os.environ.setdefault("AUTH_ENABLED", "false")
os.environ.setdefault("BACKUP_ENABLED", "false")
os.environ["BACKUP_ENCRYPTION_KEY"] = ""


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Keep explicit PostgreSQL checks out of the ordinary test command."""
    if _RUN_INTEGRATION:
        return
    skip = pytest.mark.skip(reason="integration tests require explicit RUN_INTEGRATION_TESTS=1")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session", autouse=True)
def isolated_database():
    """Own the lifecycle of the process-local test database."""
    if _RUN_INTEGRATION:
        yield
        return

    yield

    from app.db.database import Base, engine

    Base.metadata.drop_all(engine)
    engine.dispose()
    _TEST_DATABASE_PATH.unlink(missing_ok=True)


@pytest.fixture(autouse=True)
def reset_isolated_database(isolated_database):
    """Give every test a fresh schema and bootstrap identity."""
    if _RUN_INTEGRATION:
        yield
        return

    from app.db.database import Base, SessionLocal, engine
    import app.db.models  # noqa: F401 - register every ORM model
    from app.db.models.user import User, UserIdentity

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as session:
        user = User()
        session.add(user)
        session.flush()
        session.add(
            UserIdentity(
                user_id=user.id,
                provider="telegram",
                provider_subject=os.environ["DEFAULT_USER_TELEGRAM_ID"],
            )
        )
        session.commit()
    yield
