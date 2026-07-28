from collections.abc import Iterator

import pytest
from sqlmodel import Session

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
        yield s
    transaction.rollback()
    connection.close()
