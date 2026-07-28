from typing import Annotated

from fastapi import Depends
from sqlmodel import Session

from app.db import get_session

# Every route takes the session this way. Declaring it as an annotated alias rather than
# a `Depends()` default keeps FastAPI's idiom without tripping ruff's B008.
SessionDep = Annotated[Session, Depends(get_session)]
