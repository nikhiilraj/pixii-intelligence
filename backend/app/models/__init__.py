"""SQLModel table definitions.

Every model module must be imported here so that Alembic autogenerate sees its
tables on SQLModel.metadata. An unimported model is invisible to migrations.
"""

from app.models.asset import Asset, AssetKind
from app.models.daily_run import DAILY_SLOT, DailyRun
from app.models.draft import Draft
from app.models.editorial import CHANNEL, AnglePlan, EditorialBrief, PlannedClaim
from app.models.generation_trace import GenerationTrace
from app.models.metric import MetricSnapshot
from app.models.post import Post
from app.models.publication import Publication
from app.models.research import Citation, Claim, ResearchJob, ResearchSource
from app.models.template import Template, TemplateKind, TemplateStatus

__all__ = [
    "CHANNEL",
    "DAILY_SLOT",
    "AnglePlan",
    "Asset",
    "AssetKind",
    "Citation",
    "Claim",
    "DailyRun",
    "Draft",
    "EditorialBrief",
    "GenerationTrace",
    "MetricSnapshot",
    "PlannedClaim",
    "Post",
    "Publication",
    "ResearchJob",
    "ResearchSource",
    "Template",
    "TemplateKind",
    "TemplateStatus",
]
