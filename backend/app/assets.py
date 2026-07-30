import base64
import hashlib
import io
import mimetypes
from pathlib import Path

from PIL import Image
from sqlmodel import Session

from app.config import settings
from app.media import _suffix
from app.models.asset import Asset
from app.models.template import Template

# Long-edge ceiling for a stored asset. Purely about disk and upload time: the composite
# these assets land in is 1080x1350, so pixels beyond this are paid for and never seen.
# ponytail: one number, no per-kind policy. Raise it if a full-bleed background ever
# needs more than this.
#
# This is NOT about a render payload limit. There is no Cloudflare Browser Rendering
# payload ceiling in the practical range — a 13.17MB body carrying a 9.88MB embedded PNG
# was accepted live (2026-07-30, `.scratch/v2/seams.md`). The real Cloudflare constraint
# is requests per minute, which resizing does nothing about.
MAX_LONG_EDGE = 1600

# The formats Pillow reads that the /media mount can actually serve, mapped to the
# suffixes each may be stored under. Every suffix here is one of
# `media._KNOWN_SUFFIXES`. A format outside this map is refused rather than stored under
# a suffix that misdescribes it — StaticFiles would serve a `.tiff` as
# `application/octet-stream` and no browser would render it.
_SERVABLE_FORMATS = {
    "PNG": (".png",),
    "JPEG": (".jpg", ".jpeg"),
    "GIF": (".gif",),
    "WEBP": (".webp",),
}


class UnresolvableAsset(RuntimeError):
    """An image slot holds something the renderer cannot be handed.

    Loud on purpose. Every value this rejects would otherwise reach the browser as an
    `<img src>` it cannot load, which renders an empty box and reports success — the
    failure that retired `stat-hero` v1.
    """


class UnreadableUpload(ValueError):
    """The uploaded bytes are not an image this library can store.

    Deliberately an exception and not a `None` return. `media.download_post_media`
    swallows every failure because losing a post's thumbnail must not cost us the post;
    an upload is the opposite case — the caller is a human who just chose a file, and a
    silent success would leave them staring at an asset library missing the thing they
    added.
    """


def assets_dir() -> Path:
    """Where uploaded assets live, under the directory the /media mount already serves.

    Resolved per call rather than captured at import: tests point `settings.media_dir` at
    a temporary directory, and a module-level constant would have frozen the real one.
    """
    return settings.media_dir / "assets"


def asset_path(asset: Asset) -> Path:
    """The stored file for an asset. Served at `/media/assets/{asset.filename}`."""
    return assets_dir() / asset.filename


def sha256_of(raw: bytes) -> str:
    """The digest an asset is deduped on: the bytes as uploaded, before any downscaling.

    Hashing the upload rather than the stored result keeps "is this the same file again?"
    a question about what was handed to us, which is what the person re-picking a logo
    means. Hashing the re-encoded bytes would make dedupe depend on Pillow's encoder
    staying byte-identical across versions.
    """
    return hashlib.sha256(raw).hexdigest()


def store_image(
    raw: bytes, digest: str, name: str | None, content_type: str | None
) -> tuple[str, int, int]:
    """Store an uploaded image and return its filename and stored dimensions.

    The file is named for its own digest, so the name is stable and two identical uploads
    can never land on two paths. Decoding happens before anything is written: a file that
    turns out not to be an image leaves nothing behind.

    Raises `UnreadableUpload` if the bytes are not a servable image.
    """
    try:
        opened = Image.open(io.BytesIO(raw))
        # `open` is lazy — it reads the header only. Forcing the decode here is what turns
        # a truncated or corrupt file into a 4xx instead of a surprise 500 further on.
        opened.load()
    except (OSError, ValueError) as exc:
        # `UnidentifiedImageError` is an `OSError`; a decode failure raises one too.
        raise UnreadableUpload(f"not a readable image: {exc}") from exc

    # `format` is a property of the opened file and is lost by `resize`, so read it first.
    suffix = _stored_suffix(opened.format or "", name or "", content_type)
    scaled = _downscaled(opened)

    if scaled is opened:
        # Nothing to change, so store the bytes exactly as they arrived rather than
        # round-tripping them through the encoder and losing quality for no reason.
        stored = raw
    else:
        buffer = io.BytesIO()
        # Saved back in its original format so the suffix keeps describing the bytes.
        # ponytail: no format normalisation and no quality parameter. Add one if
        # re-encoded JPEGs ever look soft.
        scaled.save(buffer, format=opened.format)
        stored = buffer.getvalue()

    filename = f"{digest}{suffix}"
    directory = assets_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / filename).write_bytes(stored)
    return filename, scaled.width, scaled.height


def _downscaled(image: Image.Image) -> Image.Image:
    """The image at or under the long-edge ceiling, or the image itself if it already is."""
    long_edge = max(image.size)
    if long_edge <= MAX_LONG_EDGE:
        return image
    ratio = MAX_LONG_EDGE / long_edge
    size = (max(1, round(image.width * ratio)), max(1, round(image.height * ratio)))
    return image.resize(size, Image.Resampling.LANCZOS)


def _stored_suffix(fmt: str, name: str, content_type: str | None) -> str:
    """The suffix to store under: what the upload declared, when the bytes agree with it.

    `media._suffix` is reused for exactly the case it was written for — `media.licdn.com`
    serves images from paths carrying no extension at all, and a file picked from a
    clipboard or a drag-and-drop arrives just as nameless. What a name cannot do is
    overrule the bytes, so the decoded format has the final say: a PNG named `logo.gif` is
    stored `.png`. The declared suffix only gets to settle a choice the format leaves open,
    which today is `.jpg` versus `.jpeg`.
    """
    servable = _SERVABLE_FORMATS.get(fmt)
    if servable is None:
        raise UnreadableUpload(
            f"{fmt or 'that image'} is not a format this library serves; "
            f"try one of {sorted(_SERVABLE_FORMATS)}"
        )
    declared = _suffix(name, content_type)
    return declared if declared in servable else servable[0]


def data_uri(asset: Asset) -> str:
    """An asset as a base64 `data:` URI, ready to be embedded in a template's markup.

    Embedding, not linking, because the renderer is Cloudflare Browser Rendering — a
    remote browser that cannot reach this machine, so `/media/assets/…` would resolve to
    nothing at all.

    ponytail: read straight off disk on every render, no cache. Assets are a handful of
    files and a render is already a round trip to Cloudflare; add a cache when a render
    is ever fast enough for this to be the slow part.

    ponytail: **base64 is load-bearing, not a formatting preference.** `rendering.fill`
    runs every slot value through `html.escape` because slot values come from a language
    model, and base64's alphabet plus the `data:image/png;base64,` prefix happens to
    contain none of the characters it touches, so the URI survives byte-identical. A raw
    `data:image/svg+xml,<svg…>` does not — it is mangled into `&lt;svg…` and renders as a
    broken image with **no exception raised**. So: never encode an asset any other way
    here, and do not "fix" that by widening `fill(escape=False)` on the markup path —
    that flag is the injection guard for model-written text.
    """
    mime = mimetypes.guess_type(asset.filename)[0]
    if mime is None:
        # Unreachable through upload: `_SERVABLE_FORMATS` only ever stores suffixes
        # `mimetypes` knows. Raising rather than falling back to octet-stream keeps it
        # unreachable — a URI no browser renders is the empty box this slice exists to
        # close.
        raise UnresolvableAsset(
            f"asset {asset.id} is stored as {asset.filename!r}, a type with no known mime"
        )
    try:
        raw = asset_path(asset).read_bytes()
    except OSError as exc:
        raise UnresolvableAsset(f"asset {asset.id} has no readable file: {exc}") from exc
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def resolve_asset_values(
    session: Session, visual: Template, values: dict[str, str]
) -> dict[str, str]:
    """`values` with every `image_url` slot turned into something the renderer can load.

    The one place asset resolution happens. All three render paths go through it —
    `generation._draw_visual` (reached from both `generate_draft` and `regenerate_visual`)
    and `api_templates.preview_visual` — because a slot that resolves when a draft is
    generated and not when it is previewed or re-rendered is worse than one that never
    resolves: the failure is invisible until someone looks at the picture.

    An `image_url` slot may hold:

    - **an asset id** — resolved out of the library and embedded as base64. The path the
      application itself uses.
    - **an `http(s)` URL** — passed through untouched. The renderer is a real remote
      browser and fetches it itself; this is what a template's `example` value is, and
      previewing one worked before an asset library existed.
    - **a base64 `data:` URI** — already embeddable, passed through.

    Anything else raises `UnresolvableAsset`. That includes a raw `data:image/svg+xml,<svg…>`,
    which `fill()`'s escaping would silently mangle, and a `/media/…` path, which the
    remote renderer cannot reach.

    A slot with **no value at all** is left absent rather than rejected, so `fill()` still
    names it in a `MissingSlotValue` — which is the message an operator can act on.
    """
    if visual.body.get("renderer") != "html":
        # Only the markup path embeds anything. An `ai` template's values become prose in
        # a prompt, where a megabyte of base64 is noise at best.
        return values

    resolved = dict(values)
    for slot in visual.slots:
        # `.get`, never `slot["type"]`: VISUAL templates authored before `type` existed
        # carry no `type` key at all, and indexing raises `KeyError` on them.
        if slot.get("type") != "image_url":
            continue
        name = str(slot.get("name"))
        if name in values:
            resolved[name] = _embeddable(session, name, str(values[name]))
    return resolved


def _embeddable(session: Session, slot: str, ref: str) -> str:
    """One `image_url` value, as something an `<img src>` can actually load."""
    ref = ref.strip()

    if ref.isdigit():
        asset = session.get(Asset, int(ref))
        if asset is None:
            raise UnresolvableAsset(f"slot {slot!r} names asset {ref}, which is not in the library")
        return data_uri(asset)

    if ref.startswith(("http://", "https://")):
        return ref

    if ref.startswith("data:") and ";base64," in ref:
        return ref

    raise UnresolvableAsset(
        f"slot {slot!r} takes an asset id from the library, and {ref[:60]!r} is not one. "
        "A non-base64 data: URI cannot be passed through either: slot values are escaped "
        "as markup, so it would reach the browser mangled and render as an empty box"
    )
