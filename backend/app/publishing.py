import uuid
from datetime import UTC, datetime

from sqlmodel import Session

from app.config import settings
from app.models.draft import Draft
from app.zernio import ZernioClient, ZernioRefused

# Stamped on every post this app creates, so its output is trivially distinguishable from
# the drafts already in the account and can be found again from either side.
SOURCE_TAG = "pixii-intelligence"


class PushFailed(RuntimeError):
    """Zernio would not accept the post, or accepted it without returning an id."""


def lineage_metadata(draft: Draft) -> dict:
    """The lineage, flat and JSON-safe, as Zernio will store it verbatim.

    Written into Zernio as well as our own database so attribution survives the loss of
    our records and is visible to anyone inspecting the post in Zernio itself.
    """
    metadata: dict[str, str | int] = {
        "source": SOURCE_TAG,
        "draft_id": draft.id or 0,
        "mode": draft.mode,
        "hook_family": draft.hook_family,
        "hook_version": draft.hook_version,
        "structure_family": draft.structure_family,
        "structure_version": draft.structure_version,
    }
    if draft.visual_family:
        metadata["visual_family"] = draft.visual_family
        metadata["visual_version"] = draft.visual_version or 0
    return metadata


def _request_id(draft: Draft) -> str:
    """A stable idempotency key for this draft.

    Derived from the draft's identity rather than generated per call, so a retry after a
    timeout cannot produce a second post.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"pixii-intelligence/draft/{draft.id}"))


def push_draft(
    session: Session,
    draft: Draft,
    client: ZernioClient,
    *,
    account_id: str | None = None,
    force: bool = False,
) -> Draft:
    """Create this draft in Zernio as a draft. Never schedules, never publishes.

    Sending no `scheduledFor`, `publishNow` or `queuedFromProfile` is what makes Zernio
    treat the post as a draft; `isDraft` is sent as well so the intent is explicit rather
    than implied by omission.
    """
    if draft.zernio_post_id and not force:
        return draft

    payload = {
        "content": draft.full_text,
        "isDraft": True,
        "platforms": [
            {"platform": "linkedin", "accountId": account_id or settings.getlate_linkedin_id}
        ],
        "tags": [SOURCE_TAG],
        "metadata": lineage_metadata(draft),
    }

    try:
        body = client.create_post(payload, request_id=_request_id(draft))
    except ZernioRefused as exc:
        raise PushFailed(str(exc)) from exc
    post = body.get("post") if isinstance(body.get("post"), dict) else body
    post_id = post.get("_id") if isinstance(post, dict) else None
    if not post_id:
        raise PushFailed(f"Zernio accepted the post but returned no id: {str(body)[:200]}")

    draft.zernio_post_id = str(post_id)
    draft.pushed_at = datetime.now(UTC)
    session.add(draft)
    session.flush()
    return draft
