from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends
from sqlmodel import Session

from app.db import get_session
from app.llm import LLM, AzureChat

# Every route takes the session this way. Declaring it as an annotated alias rather than
# a `Depends()` default keeps FastAPI's idiom without tripping ruff's B008.
SessionDep = Annotated[Session, Depends(get_session)]


def get_llm() -> Iterator[LLM]:
    """The language model, as a dependency so tests can substitute a fake."""
    client = AzureChat()
    try:
        yield client
    finally:
        client.close()


LLMDep = Annotated[LLM, Depends(get_llm)]
