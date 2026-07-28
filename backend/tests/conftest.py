from collections.abc import Iterator

import pytest
from sqlalchemy import delete
from sqlmodel import Session, SQLModel

import app.models  # noqa: F401  — registers every table on SQLModel.metadata
from app.db import engine


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
