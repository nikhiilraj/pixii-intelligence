"""SQLModel table definitions.

Every model module must be imported here so that Alembic autogenerate sees its
tables on SQLModel.metadata. An unimported model is invisible to migrations.
"""

from app.models.post import Post
from app.models.template import Template, TemplateKind, TemplateStatus

__all__ = ["Post", "Template", "TemplateKind", "TemplateStatus"]
