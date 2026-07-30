from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlmodel import col, select

from app.assets import AssetInUse, UnreadableUpload, delete_asset, sha256_of, store_image
from app.config import settings
from app.deps import SessionDep
from app.models.asset import Asset, AssetKind
from app.models.post import Post

router = APIRouter(prefix="/assets", tags=["assets"])


@router.post("")
def upload_asset(
    session: SessionDep,
    file: Annotated[UploadFile, File()],
    kind: Annotated[AssetKind, Form()],
    label: Annotated[str, Form()] = "",
    tags: Annotated[list[str] | None, Form()] = None,
) -> Asset:
    """Add an image to the library, or hand back the row it already has.

    Returns 200 either way rather than 201-on-create: a caller re-adding the same logo
    wants the asset, and making it branch on the status code to find out whether it got
    one buys nothing.
    """
    raw = file.file.read()
    digest = sha256_of(raw)

    existing = session.exec(select(Asset).where(Asset.sha256 == digest)).first()
    if existing is not None:
        # The same bytes again. Returning the existing row is what keeps a library of
        # reusable images from growing a second copy of the wordmark every time someone
        # picks it off their desktop.
        return existing

    try:
        filename, width, height = store_image(raw, digest, file.filename, file.content_type)
    except UnreadableUpload as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    asset = Asset(
        filename=filename,
        label=label or Path(file.filename or filename).stem,
        kind=kind,
        tags=tags or [],
        width=width,
        height=height,
        sha256=digest,
    )
    session.add(asset)
    session.commit()
    session.refresh(asset)
    return asset


class PromoteIn(BaseModel):
    """Which corpus posts to lift media from, and what shelf the results land on.

    `kind` is the operator's call and applies to every post named — nothing here infers one
    from the post. That includes the inspiration-cohort posts: a creator's image promotes
    exactly like ours, because an asset is an image and the cohort line is about voice.
    """

    post_ids: list[int]
    kind: AssetKind
    tags: list[str] = []


@router.post("/promote")
def promote_post_media(session: SessionDep, payload: PromoteIn) -> list[Asset]:
    """Turn media a corpus post already carries into reusable library assets.

    The 107 posts arrived with their media downloaded to `media/<zernio_id><suffix>`; the
    library starts empty, so without this a picker has nothing real to offer. Promotion takes
    the same path an upload does — `sha256_of` then `store_image` — so dedupe and the
    long-edge ceiling behave identically and a promoted row is indistinguishable from an
    uploaded one apart from `source_post_id`.

    - `404` — a named post is not in the corpus.
    - `422` — a named post has no downloaded media, records a file that cannot be read, or
      records something this library cannot serve. That last case is real, not theoretical:
      3 of the 62 files in `media/` are `.mp4`, and `local_media_path` points at them.
    - `200 [asset, …]` — one row per post named, in the order given. A post whose media is
      already in the library yields the row it already has, never a second copy.

    ponytail: all-or-nothing, no per-post skip report. A 422 naming the offending post is
    something an operator can act on by dropping it from the selection, where a partial
    success needs a second response shape and a caller that reads it. Build that when
    something promotes more posts than a human picked by hand.
    """
    # Everything is read and checked before anything is written, so a request naming one
    # post with no media fails having left no half-promotion behind. `dict.fromkeys` rather
    # than `set`: naming the same post twice is one promotion, and the order given is kept.
    pending: list[tuple[Post, str, bytes]] = []
    for post_id in dict.fromkeys(payload.post_ids):
        post = session.get(Post, post_id)
        if post is None:
            raise HTTPException(status_code=404, detail=f"post {post_id} is not in the corpus")

        name = post.local_media_path
        if not name:
            # Normal, not exceptional: media download was opportunistic and many rows carry
            # nothing. Refused rather than skipped so the operator learns which post it was.
            raise HTTPException(
                status_code=422, detail=f"post {post_id} has no downloaded media to promote"
            )

        try:
            pending.append((post, name, (settings.media_dir / name).read_bytes()))
        except OSError as exc:
            # `media.download_post_media` swallows every failure it meets, because losing a
            # thumbnail must not cost us the post. This is the opposite case: the operator
            # named this exact file, so a silent success would hand them a library missing
            # the thing they asked for.
            raise HTTPException(
                status_code=422,
                detail=f"post {post_id} records media {name!r}, which cannot be read: {exc}",
            ) from exc

    promoted: list[Asset] = []
    for post, name, raw in pending:
        digest = sha256_of(raw)
        existing = session.exec(select(Asset).where(Asset.sha256 == digest)).first()
        if existing is not None:
            # These bytes are already in the library — the same post promoted twice, or two
            # posts carrying an identical image. Either way one file gets one row, and the
            # row keeps the `source_post_id` of whichever post got there first.
            promoted.append(existing)
            continue

        try:
            filename, width, height = store_image(raw, digest, name, None)
        except UnreadableUpload as exc:
            # Reached by the `.mp4` rows. Nothing is committed, so the rows added above this
            # one are discarded when the session closes; their files stay on disk as orphans
            # nobody reads, which re-promoting the same bytes simply overwrites — the same
            # trade `delete_asset` makes in the other direction.
            raise HTTPException(status_code=422, detail=f"post {post.id}: {exc}") from exc

        asset = Asset(
            filename=filename,
            # The stem is the post's `zernio_id`, matching what an upload's label falls back
            # to. Opaque, but `source_post_id` is on the row, so a UI can name the post.
            label=Path(name).stem,
            kind=payload.kind,
            tags=payload.tags,
            width=width,
            height=height,
            sha256=digest,
            source_post_id=post.id,
        )
        session.add(asset)
        # Flushed inside the loop, because the *next* post's dedupe query has to be able to
        # see this row — two posts carrying the identical image would otherwise both insert
        # and collide on the unique `sha256`. Autoflush already does this before the SELECT,
        # verified by removing the line and watching the tests still pass; it stays because
        # the correctness of the loop should not rest on a session default staying on.
        session.flush()
        promoted.append(asset)

    session.commit()
    for asset in promoted:
        session.refresh(asset)
    return promoted


@router.get("")
def list_assets(
    session: SessionDep,
    kind: AssetKind | None = None,
    tag: str | None = None,
    limit: int = 500,
) -> list[Asset]:
    """The library, newest first. An unknown `kind` is rejected with the allowed values."""
    statement = select(Asset)
    if kind:
        statement = statement.where(Asset.kind == kind)
    if tag:
        # JSONB containment: the row carries this tag, whatever else it also carries.
        statement = statement.where(col(Asset.tags).contains([tag]))
    statement = statement.order_by(col(Asset.id).desc()).limit(limit)
    return list(session.exec(statement).all())


@router.delete("/{asset_id}")
def remove_asset(session: SessionDep, asset_id: int) -> dict[str, int]:
    """Delete an asset and **remove its file from disk**. The library holds the only copy.

    - `404` — no such asset.
    - `409` — a draft or template still references it, and the message names which. Returned
      rather than cascading: the alternative is deleting the bytes a render depends on and
      letting it discover that as an empty box, which is the failure mode this codebase has
      spent three slices closing.
    - `200 {"deleted": id}` — gone. A body rather than a `204` because the frontend's one
      request helper reads every success as JSON, and a 204 would surface as a malformed
      response.
    """
    asset = session.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail=f"asset {asset_id} is not in the library")

    try:
        delete_asset(session, asset)
    except AssetInUse as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return {"deleted": asset_id}
