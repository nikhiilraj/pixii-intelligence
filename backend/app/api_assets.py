from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from sqlmodel import col, select

from app.assets import AssetInUse, UnreadableUpload, delete_asset, sha256_of, store_image
from app.deps import SessionDep
from app.models.asset import Asset, AssetKind

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
