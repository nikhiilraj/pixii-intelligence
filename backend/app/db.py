from collections.abc import Iterator
from datetime import UTC, datetime

from sqlmodel import Session, create_engine

from app.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)


def utc(value: datetime) -> datetime:
    """The same instant, always aware.

    Every datetime column in this schema is `timestamp without time zone`, so a value the app
    wrote as `datetime.now(UTC)` reads back naive once the row has round-tripped — and whether
    that has happened yet depends on when the ORM expired the object. One query can therefore
    yield both kinds, and comparing them raises. Normalised once, here, rather than at each
    use. Same hazard `test_publish_detection.utc_naive` documents, resolved the other way.

    Lives beside the engine because that is what it is about: how *this* database stores a
    datetime. It was in `main.py` until `daily.py` needed it too and could not import `main`
    without a cycle.
    """
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
