"""SQLModel table definitions.

Every model module must be imported here so that Alembic autogenerate sees its
tables on SQLModel.metadata. An unimported model is invisible to migrations.
"""

from app.models.post import Post

__all__ = ["Post"]
