"""SQLModel table definitions.

Every model module must be imported here so that Alembic autogenerate sees its
tables on SQLModel.metadata. An unimported model is invisible to migrations.
"""

from app.models.asset import Asset, AssetKind
from app.models.daily_run import DAILY_SLOT, DailyRun
from app.models.draft import Draft
from app.models.metric import MetricSnapshot
from app.models.post import Post
from app.models.publication import Publication
from app.models.template import Template, TemplateKind, TemplateStatus

__all__ = [
    "DAILY_SLOT",
    "Asset",
    "AssetKind",
    "DailyRun",
    "Draft",
    "MetricSnapshot",
    "Post",
    "Publication",
    "Template",
    "TemplateKind",
    "TemplateStatus",
]
