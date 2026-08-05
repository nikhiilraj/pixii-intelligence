from collections.abc import Iterator

import pytest
from sqlalchemy import delete
from sqlmodel import Session, SQLModel

import app.models  # noqa: F401  — registers every table on SQLModel.metadata
from app import workflow
from app.db import engine


@pytest.fixture(autouse=True)
def spawned(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, str | None]]:
    """Every background run a test asked for, and not one of them actually started.

    **Autouse, so no test can start a real thread by forgetting to.** `POST /drafts/workflow`
    hands its run to a daemon thread that builds a real `AzureChat`, a real search provider
    and two real renderers — against the shared database, outside the transaction this
    module's `session` fixture rolls back. One unpatched route call would talk to a live
    endpoint and leave rows behind.

    The list is the assertion surface: a route that spawned once appended once, and duplicate
    protection means the second press appends nothing. `tests/test_workflow.py` is where the
    run itself is exercised, synchronously, against a database of its own.
    """
    calls: list[tuple[int, str | None]] = []

    def record(draft_id: int, requested_mode: str | None) -> None:
        calls.append((draft_id, requested_mode))

    monkeypatch.setattr(workflow, "_spawn", record)
    return calls


@pytest.fixture
def session() -> Iterator[Session]:
    """A session whose work is rolled back, so tests share one migrated database.

    ponytail: transaction rollback instead of a per-test database. If tests ever need
    to exercise commit behaviour itself, give them their own database.
    """
    connection = engine.connect()
    transaction = connection.begin()
    with Session(bind=connection) as s:
        # Start from an empty corpus regardless of what a live ingest left behind. The
        # outer transaction is rolled back, so real rows are never actually removed.
        for model in reversed(SQLModel.metadata.sorted_tables):
            s.exec(delete(model))
        yield s
    transaction.rollback()
    connection.close()
