import uuid
from datetime import UTC, datetime

from sqlmodel import Session

from app.config import settings
from app.models.draft import Draft
from app.zernio import ZernioClient, ZernioRefused, ZernioResponseError

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
    metadata: dict[str, str | int | dict[str, str]] = {
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
    if draft.asset_values:
        # Which asset filled which image slot, for the same reason the template versions are
        # here: the picture Zernio holds is a flattened PNG, and without this nothing outside
        # our database records that it was built from asset 7 rather than asset 9. A nested
        # object because `metadata` is an arbitrary JSON object Zernio stores verbatim, so
        # flattening it into `asset_left_image_url` keys would invent a naming scheme to
        # answer a question the slot names already answer.
        #
        # Only when non-empty: a text-only visual would otherwise carry an empty object into
        # every post, which reads as "assets were considered" rather than "there are none".
        metadata["asset_values"] = dict(draft.asset_values)
    return metadata


def media_items_for(
    session: Session, draft: Draft, client: ZernioClient, *, reuse: bool
) -> list[dict[str, str]]:
    """The draft's rendered visual, uploaded, in the shape a post attaches media in.

    Empty when there is no image — a draft whose visual failed, or a template that produces
    text alone. The caller omits the key entirely in that case rather than sending `[]`, for
    the same reason `lineage_metadata` omits an empty `asset_values`: an empty list is a
    claim that the post has no picture, and this app cannot make that claim about a draft
    whose render merely failed.

    `reuse` is what keeps a retry from creating a second post. See `Draft.zernio_media_url`:
    the API's duplicate hash covers media URLs, so an ordinary retry has to send back the
    URL it sent last time or the hash will not match and the retry becomes a new post. It is
    off for a forced re-push, which exists precisely to carry a *redrawn* picture — reusing
    the stored URL there would re-send the old image, quietly, which is the one outcome worse
    than a duplicate.
    """
    if not draft.visual_image:
        return []

    if reuse and draft.zernio_media_url:
        return [{"url": draft.zernio_media_url, "type": "image"}]

    # PNG is a constant here rather than something sniffed from the bytes, because both
    # renderers produce PNG and nothing else writes this column: the Cloudflare path asks for
    # `screenshotOptions: {"type": "png"}`, and the image-model path ends in `_scale_to`,
    # which re-encodes whatever the provider returned with `format="PNG"`. Worth stating
    # because presign takes a fixed enum of content types and rejects a wrong one, and
    # because the value is signed into the upload URL — a mismatch fails at the store, in the
    # store's vocabulary, a long way from here.
    #
    # The filename is only a label — the store prefixes its own timestamp and random token,
    # so this does not make the resulting URL predictable. It names the draft so a human
    # looking at Zernio's storage can tell where the file came from.
    url = client.upload_media(draft.visual_image, f"pixii-draft-{draft.id}.png", "image/png")

    # Committed, not flushed. A flush would be rolled back with the rest of the request when
    # the create that follows dies mid-flight — which is the exact case this column exists
    # for, so a flush here would look correct and guard nothing. The commit is safe to make
    # early because the row it writes is true the moment the upload returns: this draft's
    # picture is at this URL, whether or not a post ever references it.
    draft.zernio_media_url = url
    session.add(draft)
    session.commit()
    return [{"url": url, "type": "image"}]


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
    """Create this draft in Zernio as a draft, picture and all. Never schedules, publishes.

    Sending no `scheduledFor`, `publishNow` or `queuedFromProfile` is what makes Zernio
    treat the post as a draft; `isDraft` is sent as well so the intent is explicit rather
    than implied by omission. Attaching media does not touch that: `mediaItems` is another
    field on the same create call, not a second request and not a publish trigger.

    **The image is uploaded before the post is created, deliberately.** The other order —
    create the post, then attach — is what produces the state nobody can reason about: a
    post sitting in Zernio carrying our text and no picture, with our database recording a
    successful push. This way a failed upload means nothing was created at all: no post,
    `zernio_post_id` still NULL, the draft as retryable as it was a second earlier. The debris
    is the other case — an upload that succeeded under a create that then failed — and it is
    an object in temporary storage that no post references, which expires on its own.

    That covers the failure this app can control. The one it cannot, and this is **measured,
    not inferred**: a presign against the live account returns a URL under `media.zernio.com`
    **/temp/**, and that storage is documented to expire after seven days, with the file
    copied to permanent storage only when a post using it **publishes**. Publishing here is a
    human act performed in Zernio at an unbounded later date, so a draft that sits in the
    queue longer than a week can lose its picture while keeping its text. Zernio's own advice
    for this — "upload near publish time" — is not available to us by design, because the
    publish is the event this tool exists not to perform.

    (The account's existing drafts all carry `/media/` URLs rather than `/temp/` ones, which
    looks like counter-evidence and is not: those were uploaded through the dashboard, by a
    route the public API does not expose. Presign is what we have, and presign returns
    `/temp/`.)

    What a human sees in that state is the broken image in Zernio's own editor, where they
    already are; `pushed_at` says how old the upload was. Our records are not wrong —
    `zernio_post_id` still names a real post whose text is intact.

    ponytail: there is no repair path for that, only a re-push, and `force=True` re-pushes by
    creating a *second* post — it re-uploads deliberately, so the URL is new and neither the
    5-minute request-id window nor the 24-hour content hash suppresses it. That is confined to
    `force`: an ordinary retry re-sends the stored `zernio_media_url`, reproducing the hash so
    the API's own dedup catches it. Repairing the picture in place would need
    `PUT /posts/{id}`, which this client does not speak.
    """
    if draft.zernio_post_id and not force:
        return draft

    try:
        # Inside the try, and before the payload: an upload that fails must fail the push
        # here, where nothing has been created yet.
        #
        # `reuse=not force`: an ordinary push — including the retry of one whose response was
        # lost — must send the URL it sent before, or the duplicate hash misses and the retry
        # creates a second post. A forced re-push must not, because the picture may have been
        # redrawn since.
        media_items = media_items_for(session, draft, client, reuse=not force)

        payload: dict[str, object] = {
            "content": draft.full_text,
            "isDraft": True,
            "platforms": [
                {"platform": "linkedin", "accountId": account_id or settings.getlate_linkedin_id}
            ],
            "tags": [SOURCE_TAG],
            "metadata": lineage_metadata(draft),
        }
        if media_items:
            payload["mediaItems"] = media_items

        body = client.create_post(payload, request_id=_request_id(draft))
    except (ZernioRefused, ZernioResponseError) as exc:
        # `ZernioResponseError` as well as a refusal: a presign that answers 200 without an
        # upload target is a failed push, not a crash. Both reach the route as a 502.
        raise PushFailed(str(exc)) from exc
    # `existingPost` as well as `post`. Within about five minutes, a repeat of the same
    # `x-request-id` is answered with HTTP 200 and the original post under `existingPost` —
    # the idempotency guarantee working, not a failure. Reading only `post` turned exactly
    # the retry this app is built to make into "accepted the post but returned no id", which
    # is untrue and points away from the real state: the post exists, we had merely never
    # recorded its id. Adopting it here is also what stops the draft looking permanently
    # unpushed while a real post sits in the account.
    candidate = body.get("post") or body.get("existingPost")
    post = candidate if isinstance(candidate, dict) else body
    post_id = post.get("_id") if isinstance(post, dict) else None
    if not post_id:
        raise PushFailed(f"Zernio accepted the post but returned no id: {str(body)[:200]}")

    draft.zernio_post_id = str(post_id)
    draft.pushed_at = datetime.now(UTC)
    # What Zernio is now holding, so `distribution.submit` can tell a draft that has since
    # been redrawn or rewritten from one that has not. Written here and nowhere else, and
    # only on the path where a create actually succeeded — the early return above sends
    # nothing, so the remote post still holds whatever it held.
    #
    # **`edited()` is deliberately not called**, here or anywhere in this function. See the
    # comment on `Draft.revision`: this writes `zernio_post_id`, `pushed_at` and now this
    # column, none of which change what a person would publish, and bumping the revision here
    # would make every push invalidate the command it exists to enable — including this one,
    # which would then record a revision the draft had already left.
    draft.pushed_revision = draft.revision
    session.add(draft)
    session.flush()
    return draft
