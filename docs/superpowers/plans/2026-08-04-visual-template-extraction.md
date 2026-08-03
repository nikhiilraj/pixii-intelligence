# Visual Template Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Teach `extraction.py` to read the images of posts that performed and propose HTML visual templates from them, with the brand logo locked to a real file rather than drawn by a model.

**Architecture:** A vision call on the existing Azure `gpt-5-5` deployment reads the strongest posts' images and returns HTML layouts. Each proposal is validated — slots agree with markup, and it actually renders through Cloudflare — before a human ever sees it. Logo slots are pinned to a real `Asset` via `default_asset_id`, so the logo is embedded file bytes and no model touches it. Preview is made to agree with generation, because it is now both the validator and what the template editor shows.

**Tech Stack:** Python 3.12 · FastAPI · SQLModel · Alembic · Postgres · Pillow · httpx · pytest. Frontend: Next.js 16 · React 19 · Vitest · Testing Library.

**Spec:** `docs/superpowers/specs/2026-08-04-visual-template-extraction-design.md`

## Global Constraints

- **Never rank, never say "best".** This slice records; it does not score. No sort-by-performance anywhere.
- **Lineage keys on `(family_id, version)`, never a template row id.** Editing writes a new row.
- **Slot behaviour is read off the declared `type`, never guessed from the slot's name.** Use `.get("type")`, never `slot["type"]` — VISUAL rows authored before `type` existed carry no key.
- **`.env` is a symlink to the vault.** Never write a credential value into this repo. `BRAND_LOGO_ASSET_ID` is an integer row id, not a credential — no `repr=False`, no `/health` flag.
- **Every datetime is normalised through `main._utc`.** Not touched by this plan, but do not introduce a naive/aware comparison.
- **Test commands:** backend `cd backend && .venv/bin/pytest -q`; frontend `cd frontend && pnpm test`; full gate `make check`.
- **The standard for a test is: break the code on purpose and check it fails.** A green suite is not evidence.
- **ponytail:** take the laziest thing that works, and mark deliberate simplifications with a `ponytail:` comment naming the ceiling and the upgrade path.

## Seams under test

Per the TDD skill, these are the public boundaries every test in this plan is written at. Nothing below tests a private helper directly.

| Seam | Kind |
|---|---|
| `llm.AzureChat.complete_json(system, user, images=())` | module function, driven through a stub `httpx` transport |
| `extraction.propose_visuals(session, llm, ...)` | module function, driven through a `FakeLLM` |
| `generation.render_template(session, visual, values, renderer)` | module function |
| `POST /templates/extract/visuals` | HTTP |
| `POST /templates/preview`, `POST /templates/{id}/preview` | HTTP |
| `POST /drafts/{id}/regenerate-visual`, `POST /drafts/{id}/restore-visual` | HTTP |
| `TemplateManager`, `Studio` | React components via Testing Library |

Private helpers (`_visual_sample`, `_to_visual`, `_strongest_posts`) are observed **through** these seams — e.g. "a video is excluded from the sample" is asserted by inspecting what the `FakeLLM` was handed, not by calling `_visual_sample`.

---

## File structure

**Create:**
- `backend/alembic/versions/<hash>_draft_previous_visual.py` — one nullable column
- `backend/tests/test_visual_extraction.py` — Tasks 2, 4, 5
- `backend/tests/test_template_preview.py` — Task 3
- `frontend/src/app/templates/TemplatePreview.tsx` — the editor's preview pane
- `frontend/src/app/templates/TemplatePreview.test.tsx`

**Modify:**
- `backend/app/llm.py` — `images` on the protocol and on `AzureChat`
- `backend/app/extraction.py` — `require_content`, `_visual_sample`, `_VISUAL_SYSTEM`, `_to_visual`, `propose_visuals`, `BRAND`
- `backend/app/generation.py` — `render_template`, `_draw_visual` routed through it
- `backend/app/api_templates.py` — preview via `render_template`, `POST /templates/preview`, `POST /templates/extract/visuals`
- `backend/app/api_drafts.py` — `previous_visual` kept and restorable
- `backend/app/models/draft.py` — `previous_visual`
- `backend/app/config.py`, `.env.example` — `brand_logo_asset_id`
- `frontend/src/app/templates/TemplateManager.tsx` — mount the preview pane
- `frontend/src/app/studio/Studio.tsx` — previous/new comparison

---

## Task 1: Vision on the LLM protocol

**Files:**
- Modify: `backend/app/llm.py:17-64`
- Test: `backend/tests/test_llm.py`

**Interfaces:**
- Consumes: nothing
- Produces: `LLM.complete_json(self, system: str, user: str, images: Sequence[bytes] = ()) -> dict`. Every existing caller passes two arguments and is unaffected.

- [ ] **Step 1: Write the failing test**

In `backend/tests/test_llm.py`, following the stub-transport style already in that file:

```python
def test_images_become_multimodal_parts():
    """An image is sent as a data URI part alongside the text, sniffed for its real mime."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    chat = AzureChat(
        endpoint="https://example.invalid",
        api_key="k",
        deployment="d",
        api_version="v",
        transport=httpx.MockTransport(handler),
    )
    chat.complete_json("sys", "look at this", images=[_one_pixel_png()])

    parts = captured["messages"][1]["content"]
    assert parts[0] == {"type": "text", "text": "look at this"}
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_no_images_sends_a_plain_string():
    """The payload without images is byte-identical to what it was before vision existed."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    chat = AzureChat(
        endpoint="https://example.invalid",
        api_key="k",
        deployment="d",
        api_version="v",
        transport=httpx.MockTransport(handler),
    )
    chat.complete_json("sys", "plain")

    assert captured["messages"][1] == {"role": "user", "content": "plain"}
```

Add the helper at the top of the file:

```python
def _one_pixel_png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (1, 1), "white").save(buffer, format="PNG")
    return buffer.getvalue()
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_llm.py -q -k multimodal`
Expected: FAIL — `complete_json() got an unexpected keyword argument 'images'`

- [ ] **Step 3: Widen the protocol and `AzureChat`**

In `backend/app/llm.py`, add the imports (`base64`, `io`, `Sequence` from `collections.abc`, `Image` from `PIL`) and change:

```python
class LLM(Protocol):
    """What the rest of the app needs from a language model. One method, one shape."""

    def complete_json(self, system: str, user: str, images: Sequence[bytes] = ()) -> dict: ...
```

```python
    def complete_json(self, system: str, user: str, images: Sequence[bytes] = ()) -> dict:
        response = self._client.post(
            f"/openai/deployments/{self._deployment}/chat/completions",
            params={"api-version": self._api_version},
            json={
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": _user_content(user, images)},
                ],
                "max_completion_tokens": 16000,
            },
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"] or ""
        return _parse_json(content)
```

```python
def _user_content(user: str, images: Sequence[bytes]) -> str | list[dict]:
    """The user message, as a plain string when there are no images.

    Kept as a string in the no-image case rather than a one-element parts list: every
    existing caller passes no images, and a payload that changed shape for all of them
    would put the whole app behind one untested serialisation difference.
    """
    if not images:
        return user
    return [{"type": "text", "text": user}, *(_image_part(raw) for raw in images)]


def _image_part(raw: bytes) -> dict:
    """One image as a data URI part, typed by what the bytes actually are.

    The mime is sniffed rather than assumed: `media/` holds .png, .jpg, .jpeg and .gif
    side by side, and declaring the wrong one is a 400 from the deployment that reads
    like a prompt problem.
    """
    mime = Image.MIME.get(Image.open(io.BytesIO(raw)).format or "") or "image/png"
    encoded = base64.b64encode(raw).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}}
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_llm.py -q`
Expected: PASS, including every test that was already there.

- [ ] **Step 5: Break it on purpose**

Change `if not images:` to `if True:` and confirm `test_images_become_multimodal_parts` fails. Revert.

- [ ] **Step 6: Commit**

```bash
git add backend/app/llm.py backend/tests/test_llm.py
git commit -m "llm: accept images, sent as data URI parts

gpt-5-5 is multimodal, so visual extraction needs no new provider. The
no-image payload keeps its plain-string content deliberately: every
existing caller passes no images and must not move behind one untested
serialisation change."
```

---

## Task 2: The visual sample

**Files:**
- Modify: `backend/app/extraction.py:106-135` (`_strongest_posts`), add `_visual_sample`
- Test: `backend/tests/test_visual_extraction.py` (create)

**Interfaces:**
- Consumes: nothing from Task 1
- Produces:
  - `_strongest_posts(session, platform, sample_size, cohort, require_content: bool = True) -> list[Post]`
  - `_visual_sample(session, platform, sample_size, cohort) -> list[tuple[Post, bytes]]`
  - `VISUAL_SAMPLE_SIZE = 5`

  - `propose_visuals(session, llm, *, platform, sample_size, cohort) -> list[Template]`, returning `[]` for now

Both helpers are private and are only ever observed **through** `propose_visuals` — by inspecting what the `FakeLLM` was handed. That is why this task ships `propose_visuals` immediately, returning an empty list: the seam exists from the first cycle, and Tasks 4 and 5 grow its body.

> **Task 5 adds a third positional parameter, `renderer`.** Every `propose_visuals(...)` call written in Tasks 2 and 4 gains a `FakeRenderer()` argument there. It is called out again in Task 5's steps.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_visual_extraction.py`:

```python
import io
from datetime import datetime

import pytest
from PIL import Image

from app.config import settings
from app.extraction import Cohort, propose_visuals
from app.models.post import Post

CURRENT = datetime(2026, 6, 1)


class FakeLLM:
    """Records what it was handed, including the images, and replays a canned response."""

    def __init__(self, response: dict | None = None):
        self.response = response or {"visuals": []}
        self.system: str | None = None
        self.user: str | None = None
        self.images: list[bytes] = []

    def complete_json(self, system: str, user: str, images=()) -> dict:
        self.system, self.user, self.images = system, user, list(images)
        return self.response


def png(width: int = 1080, height: int = 1350) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def add_post(session, zid, engaged, *, media=None, content="Body.", author=None):
    """A post, with its media written to `settings.media_dir` when bytes are supplied."""
    filename = None
    if media is not None:
        raw, suffix = media
        filename = f"{zid}{suffix}"
        settings.media_dir.mkdir(parents=True, exist_ok=True)
        (settings.media_dir / filename).write_bytes(raw)
    post = Post(
        zernio_id=zid,
        platform="linkedin",
        account_username=author or settings.voice_account,
        content=content,
        engaged_actions=engaged,
        published_at=CURRENT,
        local_media_path=filename,
    )
    session.add(post)
    session.flush()
    return post


def test_sample_is_image_posts_strongest_first(session):
    add_post(session, "weak", 10, media=(png(), ".png"))
    add_post(session, "strong", 900, media=(png(), ".png"))
    llm = FakeLLM()

    propose_visuals(session, llm)

    assert "strong" in (llm.user or "")
    assert (llm.user or "").index("strong") < (llm.user or "").index("weak")
    assert len(llm.images) == 2


def test_a_post_with_no_image_is_not_in_the_sample(session):
    add_post(session, "wordy", 900)
    llm = FakeLLM()

    propose_visuals(session, llm)

    assert llm.images == []


def test_a_video_is_not_visual_evidence(session):
    add_post(session, "clip", 900, media=(b"not-an-image", ".mp4"))
    llm = FakeLLM()

    propose_visuals(session, llm)

    assert llm.images == []


def test_a_corpus_with_no_images_proposes_nothing_and_raises_nothing(session):
    """Empty sample returns [], matching propose_hooks and propose_structures.

    The spec's error table said ExtractionError here. Returning [] is what the two
    sibling extractors already do for an empty sample, and one extractor that raises
    where its siblings return empty is a difference an operator has to memorise.
    """
    llm = FakeLLM()

    assert propose_visuals(session, llm) == []
    assert llm.user is None  # the model was never called


def test_an_image_only_post_is_still_evidence(session):
    """A post with no text carries no hook and is still a picture that worked.

    `_strongest_posts` excludes empty content because a hook cannot come from nothing.
    Visual extraction passes `require_content=False`; without that, the purest sample in
    the corpus is silently discarded.
    """
    add_post(session, "picture-only", 900, media=(png(), ".png"), content="   ")
    llm = FakeLLM()

    propose_visuals(session, llm)

    assert len(llm.images) == 1
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_visual_extraction.py -q`
Expected: FAIL — `ImportError: cannot import name 'propose_visuals'`

- [ ] **Step 3: Implement the sample and a minimal `propose_visuals`**

In `backend/app/extraction.py`, add `from pathlib import Path` is not needed — `settings.media_dir` is already a `Path`. Add near `DEFAULT_SAMPLE_SIZE`:

```python
# Fewer than a hook sample: every entry here is a full image in the request, and a layout
# repeats visibly across far fewer examples than a sentence pattern does.
VISUAL_SAMPLE_SIZE = 5

# Suffixes `media.download_post_media` stores that are still images. A video frame is not
# the artefact — the post's picture is — so `.mp4`, `.mov` and `.webm` are not here.
_STILL_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp")
```

Change the signature and the content filter of `_strongest_posts`:

```python
def _strongest_posts(
    session: Session,
    platform: str,
    sample_size: int,
    cohort: Cohort,
    require_content: bool = True,
) -> list[Post]:
```

and make the existing content filter conditional, keeping its comment and adding to it:

```python
    if require_content:
        # A post with no text carries no hook, so it is no evidence. Load-bearing in both
        # cohorts: at least one creator post is an image with no text at all.
        #
        # Visual extraction passes False. That same textless post is a picture that
        # performed, which is exactly the evidence a layout is drawn from — filtering it
        # here would discard the purest sample in the set.
        statement = statement.where(func.trim(col(Post.content)) != "")
```

Note this requires moving the filter out of the chained `select(...)` expression; keep the platform, account and exclusion filters where they are.

Then:

```python
def _visual_sample(
    session: Session, platform: str, sample_size: int, cohort: Cohort
) -> list[tuple[Post, bytes]]:
    """The strongest posts that have a still image, paired with the image's bytes.

    Returns pairs rather than posts because the caller needs the bytes twice — once to
    send to the model, once to take the template's dimensions from. Re-resolving a file
    from `Template.provenance` later is not possible in one step: that column is
    `list[str]` of Zernio ids, so it would mean a `Post` lookup for bytes already in hand.

    An unreadable file is skipped, not raised. `media/` is a cache of Zernio's files; one
    truncated download must not cost the other four posts their slot.
    """
    pairs: list[tuple[Post, bytes]] = []
    # Over-fetch, because the media filter cannot be expressed in the ranking query without
    # assuming a suffix convention the column does not guarantee.
    for post in _strongest_posts(
        session, platform, sample_size * 4, cohort, require_content=False
    ):
        name = post.local_media_path or ""
        if not name.lower().endswith(_STILL_SUFFIXES):
            continue
        try:
            pairs.append((post, (settings.media_dir / name).read_bytes()))
        except OSError:
            continue
        if len(pairs) == sample_size:
            break
    return pairs


def propose_visuals(
    session: Session,
    llm: LLM,
    *,
    platform: str = "linkedin",
    sample_size: int = VISUAL_SAMPLE_SIZE,
    cohort: Cohort = Cohort.VOICE,
) -> list[Template]:
    """Propose visual layouts from the images of the strongest posts. Proposals only."""
    cohort = Cohort(cohort)
    sample = _visual_sample(session, platform, sample_size, cohort)
    if not sample:
        return []

    llm.complete_json(_VISUAL_SYSTEM, _visual_prompt(sample), [raw for _, raw in sample])
    return []
```

with the two constants it needs, kept deliberately thin for now — Task 4 replaces the body of `_VISUAL_SYSTEM` and grows the return:

```python
_VISUAL_SYSTEM = "Placeholder — Task 4 writes the real extraction prompt."


def _visual_prompt(sample: list[tuple[Post, bytes]]) -> str:
    lines = [
        "Post images, strongest first. 'engaged' is likes + comments + shares + saves — "
        "the measure that matters. Weight the top of this list most heavily.",
        "",
    ]
    for post, _ in sample:
        lines.append(f"id: {post.zernio_id} | engaged: {post.engaged_actions}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_visual_extraction.py tests/test_extraction.py -q`
Expected: PASS — including every pre-existing extraction test, which proves `require_content=True` preserved the old behaviour.

- [ ] **Step 5: Break it on purpose**

Change `require_content=False` to `True` in `_visual_sample` and confirm `test_an_image_only_post_is_still_evidence` fails. Change `_STILL_SUFFIXES` to include `".mp4"` and confirm `test_a_video_is_not_visual_evidence` fails. Revert both.

- [ ] **Step 6: Commit**

```bash
git add backend/app/extraction.py backend/tests/test_visual_extraction.py
git commit -m "extraction: the visual sample — strongest posts that have a picture

Pairs the post with its bytes because both are needed: the image goes to
the model, and its dimensions become the template's. Passes
require_content=False, so the textless creator post — a picture that
performed, with no hook in it — stays in the sample."
```

---

## Task 3: Preview agrees with generation, and works on unsaved bodies

**Files:**
- Modify: `backend/app/generation.py:354-372` (`_draw_visual`), add `render_template`
- Modify: `backend/app/api_templates.py:175-211` (`preview_visual`), add `POST /templates/preview`
- Test: `backend/tests/test_template_preview.py` (create)

**Interfaces:**
- Consumes: nothing from Tasks 1–2
- Produces: `generation.render_template(session: Session, visual: Template, values: dict[str, str], renderer: HtmlRenderer | ImageRenderer) -> bytes` — applies `chosen_assets` then `resolve_asset_values` then `render_visual`. Used by `api_templates` in this task and by `extraction` in Task 5.
- Produces: `POST /templates/preview` taking `{"body": {...}, "slots": [...], "values": {...}}` and returning `image/png`.

**Why this is a behaviour change to an existing route:** `preview_visual` passes caller-supplied `values` straight into `resolve_asset_values`, which never consults a slot's `default_asset_id`. So a logo slot previews from its `example` URL and generates from the pinned asset — two pictures from one template, nothing raised. That is the divergence `resolve_asset_values`' own docstring names as the reason all render paths go through it.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_template_preview.py`:

```python
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.deps import get_html_renderer
from app.main import app
from app.models.asset import Asset, AssetKind
from app.models.template import TemplateKind
from app.templates import create_template


class FakeHtmlRenderer:
    """Records the markup it was asked to screenshot."""

    def __init__(self) -> None:
        self.html: str | None = None

    def screenshot(self, html: str, width: int, height: int) -> bytes:
        self.html = html
        return b"PNG"


def a_logo(session, tmp_path_factory) -> Asset:
    """A real one-pixel asset on disk, so `data_uri` has bytes to embed."""
    from app.assets import assets_dir

    buffer = io.BytesIO()
    Image.new("RGB", (1, 1), "red").save(buffer, format="PNG")
    raw = buffer.getvalue()
    assets_dir().mkdir(parents=True, exist_ok=True)
    (assets_dir() / "logo.png").write_bytes(raw)
    asset = Asset(
        filename="logo.png", label="logo", kind=AssetKind.LOGO,
        width=1, height=1, sha256="deadbeef",
    )
    session.add(asset)
    session.flush()
    return asset


def test_preview_renders_a_slots_pinned_default(session, tmp_path_factory):
    """A logo slot the caller says nothing about renders from its pinned asset.

    This is the assertion that fails today: preview reads only what the caller sent, so
    the picture it shows is not the picture generation produces.
    """
    logo = a_logo(session, tmp_path_factory)
    template = create_template(
        session,
        kind=TemplateKind.VISUAL,
        name="pinned",
        body={"renderer": "html", "html": "<img src='{logo}'>"},
        slots=[{
            "name": "logo", "type": "image_url",
            "example": "https://example.invalid/x.png",
            "default_asset_id": logo.id,
        }],
    )
    session.commit()

    renderer = FakeHtmlRenderer()
    app.dependency_overrides[get_html_renderer] = lambda: renderer
    try:
        response = TestClient(app).post(f"/templates/{template.id}/preview", json={})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "data:image/png;base64," in (renderer.html or "")
    assert "example.invalid" not in (renderer.html or "")


def test_preview_of_an_unsaved_body(session):
    """The editor previews what is in the textarea, which has no template id."""
    renderer = FakeHtmlRenderer()
    app.dependency_overrides[get_html_renderer] = lambda: renderer
    try:
        response = TestClient(app).post(
            "/templates/preview",
            json={
                "body": {"renderer": "html", "html": "<h1>{headline}</h1>"},
                "slots": [{"name": "headline", "type": "text"}],
                "values": {"headline": "Edited, not saved"},
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "Edited, not saved" in (renderer.html or "")


def test_preview_of_an_unsaved_body_names_a_missing_slot(session):
    app.dependency_overrides[get_html_renderer] = lambda: FakeHtmlRenderer()
    try:
        response = TestClient(app).post(
            "/templates/preview",
            json={
                "body": {"renderer": "html", "html": "<h1>{headline}</h1>"},
                "slots": [{"name": "headline", "type": "text"}],
                "values": {},
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert "headline" in response.json()["detail"]
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_template_preview.py -q`
Expected: FAIL — the first on `example.invalid` appearing in the markup (or a 422 for the absent value), the other two on a 404 for `/templates/preview`.

- [ ] **Step 3: Add `render_template` and route both previews through it**

In `backend/app/generation.py`, above `_draw_visual`:

```python
def render_template(
    session: Session,
    visual: Template,
    values: dict[str, str],
    renderer: HtmlRenderer | ImageRenderer,
) -> bytes:
    """Render a visual the one way the whole application renders visuals.

    Three steps, in this order, and the order is the point:

    1. `chosen_assets` settles each `image_url` slot — what was picked, else the slot's
       `default_asset_id`. It also drops any key naming a non-image slot, so a caller
       cannot write `big_number` through this door.
    2. `resolve_asset_values` turns each settled reference into something loadable.
    3. `render_visual` draws it.

    Preview used to skip step 1, so a slot with a pinned default previewed from its
    `example` and generated from the asset — two pictures from one template, with nothing
    raised. Every render path goes through here now so that cannot recur.
    """
    settled = {**values, **chosen_assets(visual, values)}
    return render_visual(visual, resolve_asset_values(session, visual, settled), renderer)
```

Then simplify `_draw_visual` to call it:

```python
    try:
        draft.visual_image = render_template(session, visual, render_values(draft), renderer)
        draft.visual_error = None
```

In `backend/app/api_templates.py`, import `render_template` from `app.generation`, and replace the render line in `preview_visual`:

```python
        image = render_template(session, template, values, renderer)
```

Then add the unsaved-body route **above** `preview_visual`, so `/templates/preview` is matched before `/templates/{template_id}/preview` cannot claim it — FastAPI matches in declaration order and `preview` is not an int, but declaring it first removes the question:

```python
class PreviewIn(BaseModel):
    body: dict = {}
    slots: list[dict] = []
    values: dict[str, str] = {}


@router.post("/preview")
def preview_unsaved(
    session: SessionDep,
    html_renderer: HtmlRendererDep,
    image_renderer: ImageRendererDep,
    payload: PreviewIn,
) -> Response:
    """Render a visual body that has not been saved, for the template editor.

    An unsaved edit has no id, so this takes the body and slots directly. The row is
    built in memory and never added to the session — previewing must not be able to
    write a template, and a `Template(...)` that is never `session.add`ed cannot.
    """
    draft = Template(
        family_id="preview", version=0, kind=TemplateKind.VISUAL,
        name="preview", body=payload.body, slots=payload.slots,
    )
    return _rendered(session, draft, payload.values, html_renderer, image_renderer)
```

and lift the shared body of `preview_visual` into `_rendered`, keeping every existing `except` clause and its comment verbatim:

```python
def _rendered(
    session: SessionDep, template: Template, values: dict[str, str],
    html_renderer, image_renderer,
) -> Response:
    renderer = image_renderer if template.body.get("renderer") == "ai" else html_renderer
    try:
        image = render_template(session, template, values, renderer)
    except (MissingSlotValue, UnresolvableAsset) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except UnsupportedRenderer as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except ImageGenerationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"rendering service returned {exc.response.status_code}",
        ) from exc
    return Response(content=image, media_type="image/png")
```

`preview_visual` keeps its `kind` check and then returns `_rendered(...)`.

- [ ] **Step 4: Run the tests and verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_template_preview.py tests/test_api_templates.py tests/test_generation.py tests/test_asset_resolution.py tests/test_asset_picker.py -q`
Expected: PASS. If a pre-existing preview test asserted a 422 for an unsupplied slot that now has a default, that test was asserting the bug — update it and say so in the commit message.

- [ ] **Step 5: Break it on purpose**

Remove `chosen_assets` from `render_template`'s first line and confirm `test_preview_renders_a_slots_pinned_default` fails. Revert.

- [ ] **Step 6: Commit**

```bash
git add backend/app/generation.py backend/app/api_templates.py backend/tests/test_template_preview.py
git commit -m "preview: render the way generation renders, and on unsaved bodies

preview_visual passed caller values straight to resolve_asset_values,
which never consults default_asset_id — so a pinned logo slot previewed
from its example URL and generated from the asset. Two pictures from one
template, nothing raised, exactly the divergence resolve_asset_values
documents as the reason every path goes through it.

render_template is now that one path. POST /templates/preview takes an
unsaved body for the editor; the Template it builds is never added to the
session, so previewing cannot write one."
```

---

## Task 4: Proposals — the prompt, the markup/slot contract, the dimensions

**Files:**
- Modify: `backend/app/extraction.py` — real `_VISUAL_SYSTEM`, `BRAND`, `_to_visual`, `propose_visuals`
- Test: `backend/tests/test_visual_extraction.py`

**Interfaces:**
- Consumes: `_visual_sample`, `VISUAL_SAMPLE_SIZE`, `propose_visuals` (Task 2); `LLM.complete_json(..., images)` (Task 1)
- Produces: `propose_visuals(...) -> list[Template]` returning PROPOSED VISUAL rows with `body = {"renderer": "html", "html": ..., "width": int, "height": int, "rationale": str, "cohort": str}`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_visual_extraction.py`:

```python
ONE_VISUAL = {
    "visuals": [{
        "name": "ranked-bars",
        "html": "<div>{kicker}</div><h1>{headline}</h1>",
        "slots": [
            {"name": "kicker", "type": "text", "example": "PRIME DAY 2026"},
            {"name": "headline", "type": "text", "example": "AI did the shopping."},
        ],
        "rationale": "the number is the picture",
        "source_post_ids": ["strong"],
    }]
}


def test_a_proposal_becomes_a_proposed_visual_template(session):
    add_post(session, "strong", 900, media=(png(), ".png"))

    [template] = propose_visuals(session, FakeLLM(ONE_VISUAL))

    assert template.kind is TemplateKind.VISUAL
    assert template.status is TemplateStatus.PROPOSED
    assert template.body["renderer"] == "html"
    assert template.provenance == ["strong"]


def test_dimensions_come_from_the_source_image(session):
    """The corpus is not one size — 1080x1350 and 1080x1080 both appear.

    A model asked to state a size states a plausible one. The image is in hand, so it is
    measured instead.
    """
    add_post(session, "strong", 900, media=(png(1080, 1080), ".png"))

    [template] = propose_visuals(session, FakeLLM(ONE_VISUAL))

    assert (template.body["width"], template.body["height"]) == (1080, 1080)


def test_dimensions_fall_back_when_the_source_is_ambiguous(session):
    add_post(session, "a", 900, media=(png(1080, 1080), ".png"))
    add_post(session, "b", 800, media=(png(1080, 1080), ".png"))
    two_sources = {"visuals": [{**ONE_VISUAL["visuals"][0], "source_post_ids": ["a", "b"]}]}

    [template] = propose_visuals(session, FakeLLM(two_sources))

    assert (template.body["width"], template.body["height"]) == (1080, 1350)


def test_an_invented_source_id_is_dropped(session):
    add_post(session, "strong", 900, media=(png(), ".png"))
    invented = {"visuals": [{**ONE_VISUAL["visuals"][0], "source_post_ids": ["strong", "made-up"]}]}

    [template] = propose_visuals(session, FakeLLM(invented))

    assert template.provenance == ["strong"]


def test_markup_referencing_an_undeclared_slot_is_dropped(session):
    """Left in, this raises MissingSlotValue in Studio — after a human approved it."""
    add_post(session, "strong", 900, media=(png(), ".png"))
    broken = {"visuals": [
        {**ONE_VISUAL["visuals"][0], "name": "broken", "html": "<h1>{headline}</h1><p>{ghost}</p>"},
        ONE_VISUAL["visuals"][0],
    ]}

    proposals = propose_visuals(session, FakeLLM(broken))

    assert [t.name for t in proposals] == ["ranked-bars"]


def test_a_declared_slot_missing_from_the_markup_is_dropped(session):
    add_post(session, "strong", 900, media=(png(), ".png"))
    orphan = {"visuals": [{
        **ONE_VISUAL["visuals"][0],
        "slots": [*ONE_VISUAL["visuals"][0]["slots"], {"name": "unused", "type": "text"}],
    }]}

    assert propose_visuals(session, FakeLLM(orphan)) == []


def test_a_response_without_a_visuals_list_is_an_extraction_error(session):
    add_post(session, "strong", 900, media=(png(), ".png"))

    with pytest.raises(ExtractionError):
        propose_visuals(session, FakeLLM({"layouts": []}))


def test_the_prompt_carries_the_brand_tokens(session):
    add_post(session, "strong", 900, media=(png(), ".png"))
    llm = FakeLLM(ONE_VISUAL)

    propose_visuals(session, llm)

    assert "#d65831" in (llm.system or "")
```

Extend the imports at the top of the file with `ExtractionError`, `TemplateKind`, `TemplateStatus`.

- [ ] **Step 2: Run the tests and verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_visual_extraction.py -q`
Expected: FAIL — `propose_visuals` returns `[]`, so every unpack fails.

- [ ] **Step 3: Write the prompt, the validator and the conversion**

In `backend/app/extraction.py`, replace the placeholder `_VISUAL_SYSTEM` and add the brand constant. Import `Image` from `PIL`, `io`, and `_SLOT` and the size defaults from `app.rendering`:

```python
from app.rendering import DEFAULT_HEIGHT, DEFAULT_WIDTH, _SLOT
```

```python
# Copied from monte-workshop/bots-and-tools/brand/brand.json (version 2026-06-10) rather
# than read across repositories at runtime. ponytail: one constant, re-copied when the
# brand changes. Make it a fetch when a second tool in this repo needs the same values.
BRAND = """\
Colours: #d65831 primary orange — the ONLY saturated colour, used for one accent per
image. #FAFAF8 page background (warm, never cold white). #FFFFFF card surface. #1A1816
text. #7A756D muted text. #E0DFDB borders.
Type: Cabinet Grotesk bold/extrabold for headlines, set tight and large. A neutral
grotesque for body and labels. Kickers are small, letterspaced and uppercase.
Layout: generous margins, one idea per image, a source line at the foot."""

_VISUAL_SYSTEM = f"""\
You extract reusable visual layouts from the images of social posts that already
performed well.

You are given those images, strongest first. Your job is to express the *repeatable
layout* underneath the specific subject matter as HTML, so it can be reused for a
different subject next week.

The output is rendered by a headless browser at the size of the source image. It is not
sent to an image model, so every word and number in it is exact.

Rules:
- Return complete, self-contained HTML: one root element with an inline <style>. No
  external stylesheets, no <img> src you invent, no JavaScript, no web fonts.
- Put a {{slot_name}} placeholder wherever the content changes between posts. Every
  placeholder in the markup must appear in "slots", and every slot must appear in the
  markup. This is checked, and a mismatch discards the proposal.
- Slot "type" is "text" for words and numbers, "image_url" for a picture. An "image_url"
  slot renders as <img src="{{slot}}">.
- If the source image carries a brand mark, give that slot "role": "logo". It is filled
  from a real logo file, never drawn.
- Do not reproduce the source's words. The example values are illustrations of the shape.
- Abstract only what genuinely repeats. Do not invent a layout no image shows.
- Ground every layout in the images that justify it, by their id. Never cite an id you
  were not given.
- Prefer two or three sharply different layouts over many similar ones.

Brand:
{BRAND}

Return ONLY JSON of this shape, with no commentary:
{{
  "visuals": [
    {{
      "name": "short-kebab-name",
      "html": "<div style=…>{{kicker}}</div><h1>{{headline}}</h1>",
      "slots": [
        {{"name": "kicker", "type": "text", "example": "a real example"}},
        {{"name": "logo", "type": "image_url", "role": "logo", "example": "https://…"}}
      ],
      "source_post_ids": ["id"],
      "rationale": "why this shape works, one sentence"
    }}
  ]
}}"""
```

Add the conversion:

```python
class _RejectedProposal(RuntimeError):
    """One proposal is unusable. The others in the batch are not."""


def _to_visual(
    session: Session, proposal: dict, sizes: dict[str, tuple[int, int]], cohort: Cohort
) -> Template:
    name = (proposal.get("name") or "").strip()
    markup = (proposal.get("html") or "").strip()
    if not name or not markup:
        raise _RejectedProposal(f"proposal missing name or html: {proposal!r}")

    slots = proposal.get("slots") or []
    declared = {str(slot.get("name")) for slot in slots}
    used = set(_SLOT.findall(markup))
    if used != declared:
        # Both directions matter and they fail differently. A placeholder with no slot
        # raises MissingSlotValue in Studio, after a human approved it. A slot with no
        # placeholder is a control the picker offers that changes nothing on the image.
        raise _RejectedProposal(
            f"{name}: markup and slots disagree — "
            f"undeclared {sorted(used - declared)}, unused {sorted(declared - used)}"
        )

    # Keep only ids the model was actually shown — provenance has to be checkable.
    provenance = [pid for pid in proposal.get("source_post_ids") or [] if pid in sizes]
    # The size is the source's, and only when there is exactly one source to take it from.
    # Averaging two sizes would invent a third that no post ever used.
    width, height = sizes[provenance[0]] if len(provenance) == 1 else (
        DEFAULT_WIDTH,
        DEFAULT_HEIGHT,
    )

    return create_template(
        session,
        kind=TemplateKind.VISUAL,
        name=name,
        body={
            "renderer": "html",
            "html": markup,
            "width": width,
            "height": height,
            "rationale": (proposal.get("rationale") or "").strip(),
            "cohort": cohort.value,
        },
        slots=slots,
        provenance=provenance,
    )
```

and grow `propose_visuals`:

```python
    result = llm.complete_json(
        _VISUAL_SYSTEM, _visual_prompt(sample), [raw for _, raw in sample]
    )
    proposals = result.get("visuals")
    if not isinstance(proposals, list):
        raise ExtractionError(f"expected a 'visuals' list, got keys {sorted(result)}")

    sizes = {post.zernio_id: _size_of(raw) for post, raw in sample}
    kept: list[Template] = []
    for proposal in proposals:
        try:
            kept.append(_to_visual(session, proposal, sizes, cohort))
        except _RejectedProposal as exc:
            # One bad layout in five must not cost the other four. The reason is logged
            # rather than raised, and the proposal simply never appears.
            log.warning("visual proposal rejected: %s", exc)
    return kept
```

with, at module scope, `log = logging.getLogger(__name__)` (add `import logging` if absent) and:

```python
def _size_of(raw: bytes) -> tuple[int, int]:
    """The source image's dimensions, which become the template's."""
    return Image.open(io.BytesIO(raw)).size
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_visual_extraction.py -q`
Expected: PASS

- [ ] **Step 5: Break it on purpose**

Change `if used != declared:` to `if False:` and confirm both mismatch tests fail. Change `len(provenance) == 1` to `len(provenance) >= 1` and confirm `test_dimensions_fall_back_when_the_source_is_ambiguous` fails. Revert both.

- [ ] **Step 6: Commit**

```bash
git add backend/app/extraction.py backend/tests/test_visual_extraction.py
git commit -m "extraction: propose HTML layouts from post images

Markup and slots must agree in both directions before a proposal is
offered. An undeclared placeholder raises MissingSlotValue in Studio
after a human has already approved it; a slot with no placeholder is a
picker control that changes nothing. A rejection drops one proposal and
logs why — one bad layout must not cost the other four.

Dimensions are measured off the source image, and only when a proposal
names exactly one, because averaging two sizes invents a third."
```

---

## Task 5: The render gate, the logo pin, and the route

**Files:**
- Modify: `backend/app/config.py`, `.env.example` — `brand_logo_asset_id`
- Modify: `backend/app/extraction.py` — render validation, logo pinning
- Modify: `backend/app/api_templates.py` — `POST /templates/extract/visuals`
- Test: `backend/tests/test_visual_extraction.py`

**Interfaces:**
- Consumes: `propose_visuals` (Task 4), `generation.render_template` (Task 3)
- Produces: `propose_visuals(session, llm, renderer, *, platform, sample_size, cohort)` — `renderer` is a new **positional** third parameter
- Produces: `POST /templates/extract/visuals?platform=&cohort=` → `201`, `list[Template]`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_visual_extraction.py`:

```python
class FakeRenderer:
    """Screenshots, or refuses the layouts named in `broken`."""

    def __init__(self, broken: set[str] = frozenset()):
        self.broken = broken
        self.rendered: list[str] = []

    def screenshot(self, html: str, width: int, height: int) -> bytes:
        self.rendered.append(html)
        if any(name in html for name in self.broken):
            raise MissingSlotValue("nothing to fill it with")
        return b"PNG"


def test_a_proposal_that_does_not_render_is_dropped(session):
    """A layout that fails is not a proposal — it is a trap with an approval on it."""
    add_post(session, "strong", 900, media=(png(), ".png"))
    two = {"visuals": [
        {**ONE_VISUAL["visuals"][0], "name": "bad", "html": "<h1>BOOM {headline}</h1>",
         "slots": [{"name": "headline", "type": "text", "example": "x"}]},
        ONE_VISUAL["visuals"][0],
    ]}

    proposals = propose_visuals(session, FakeLLM(two), FakeRenderer(broken={"BOOM"}))

    assert [t.name for t in proposals] == ["ranked-bars"]


def test_every_kept_proposal_was_rendered_once(session):
    add_post(session, "strong", 900, media=(png(), ".png"))
    renderer = FakeRenderer()

    propose_visuals(session, FakeLLM(ONE_VISUAL), renderer)

    assert len(renderer.rendered) == 1
    assert "PRIME DAY 2026" in renderer.rendered[0]


def test_a_logo_role_is_pinned_to_the_configured_asset(session, monkeypatch):
    monkeypatch.setattr(settings, "brand_logo_asset_id", 42)
    add_post(session, "strong", 900, media=(png(), ".png"))
    with_logo = {"visuals": [{
        "name": "with-logo",
        "html": "<h1>{headline}</h1><img src='{mark}'>",
        "slots": [
            {"name": "headline", "type": "text", "example": "x"},
            {"name": "mark", "type": "image_url", "role": "logo", "example": "https://x/y.png"},
        ],
        "source_post_ids": ["strong"],
    }]}

    [template] = propose_visuals(session, FakeLLM(with_logo), FakeRenderer())

    mark = next(s for s in template.slots if s["name"] == "mark")
    assert mark["default_asset_id"] == 42


def test_a_slot_merely_named_logo_is_not_pinned(session, monkeypatch):
    """Pinned on the declared role, never the name.

    `left_image_url` reads as an asset and `hero` does not — the same reason
    `asset_slots` reads `type` rather than guessing.
    """
    monkeypatch.setattr(settings, "brand_logo_asset_id", 42)
    add_post(session, "strong", 900, media=(png(), ".png"))
    named = {"visuals": [{
        "name": "named-only",
        "html": "<h1>{headline}</h1><img src='{logo}'>",
        "slots": [
            {"name": "headline", "type": "text", "example": "x"},
            {"name": "logo", "type": "image_url", "example": "https://x/y.png"},
        ],
        "source_post_ids": ["strong"],
    }]}

    [template] = propose_visuals(session, FakeLLM(named), FakeRenderer())

    assert "default_asset_id" not in next(s for s in template.slots if s["name"] == "logo")


def test_no_configured_logo_leaves_the_slot_unpinned_and_raises_nothing(session, monkeypatch):
    """A first run happens before anyone has uploaded a logo."""
    monkeypatch.setattr(settings, "brand_logo_asset_id", None)
    add_post(session, "strong", 900, media=(png(), ".png"))
    with_logo = {"visuals": [{
        "name": "with-logo",
        "html": "<h1>{headline}</h1><img src='{mark}'>",
        "slots": [
            {"name": "headline", "type": "text", "example": "x"},
            {"name": "mark", "type": "image_url", "role": "logo", "example": "https://x/y.png"},
        ],
        "source_post_ids": ["strong"],
    }]}

    [template] = propose_visuals(session, FakeLLM(with_logo), FakeRenderer())

    assert "default_asset_id" not in next(s for s in template.slots if s["name"] == "mark")
```

Add `from app.rendering import MissingSlotValue` to the test imports.

- [ ] **Step 2: Run the tests and verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_visual_extraction.py -q -k "render or logo"`
Expected: FAIL — `propose_visuals() takes 2 positional arguments but 3 were given`

- [ ] **Step 3: Add the config field**

In `backend/app/config.py`, alongside the other non-credential settings:

```python
    # The Asset holding the real brand logo. Extraction pins it as the default for every
    # slot a proposal declares with `role: "logo"`, so the mark is embedded file bytes and
    # no model ever draws it.
    #
    # Not a credential — a row id — so no `repr=False` and no /health flag. Unset is the
    # normal first-run state: a logo slot simply carries no default and the picker asks.
    brand_logo_asset_id: int | None = None
```

In `.env.example`, under a new heading:

```
# The asset id of the brand logo, so extracted layouts pin it instead of drawing it.
# Upload the logo via POST /assets, then put the returned id here. Not a credential.
BRAND_LOGO_ASSET_ID=
```

- [ ] **Step 4: Pin the logo and render every kept proposal**

In `backend/app/extraction.py`, add the renderer parameter and the two new steps:

```python
def propose_visuals(
    session: Session,
    llm: LLM,
    renderer: HtmlRenderer,
    *,
    platform: str = "linkedin",
    sample_size: int = VISUAL_SAMPLE_SIZE,
    cohort: Cohort = Cohort.VOICE,
) -> list[Template]:
    """Propose visual layouts from the images of the strongest posts. Proposals only.

    Every proposal is rendered once before it is offered. A layout that does not render is
    not a proposal — it is a trap with a human's approval on it, and there are already 50
    proposals waiting in that queue.

    **This route is slow, and that is expected rather than a hang.** One LLM call plus one
    Cloudflare render per proposal, and `CloudflareRenderer` absorbs a per-minute 429 with
    5 + 10 + 20 = 35s of backoff. `MAX_VISUAL_PROPOSALS` bounds the worst case.
    ponytail: rendering at approval time instead was considered and rejected — it moves
    the failure to the moment a human has already said yes.
    """
```

Add near `VISUAL_SAMPLE_SIZE`:

```python
# Bounds how long the extract route can take: each one costs a render, and a rate-limited
# render costs 35s of backoff.
MAX_VISUAL_PROPOSALS = 5
```

In `_to_visual`, after the `used != declared` check and before `create_template`, pin the logo:

```python
    # Pinned on the slot's declared role, never its name. `left_image_url` reads as an
    # asset and `hero` does not, so a name heuristic here reintroduces exactly the silent
    # wrongness the typed-slot design closes.
    if settings.brand_logo_asset_id is not None:
        slots = [
            {**slot, "default_asset_id": settings.brand_logo_asset_id}
            if slot.get("role") == "logo"
            else slot
            for slot in slots
        ]
```

In the loop, cap and render:

```python
    for proposal in proposals[:MAX_VISUAL_PROPOSALS]:
        try:
            template = _to_visual(session, proposal, sizes, cohort)
            _must_render(session, template, renderer)
        except (_RejectedProposal, MissingSlotValue, UnresolvableAsset, UnsupportedRenderer) as exc:
            log.warning("visual proposal rejected: %s", exc)
            continue
        kept.append(template)
    if len(proposals) > MAX_VISUAL_PROPOSALS:
        # Never silently. A truncated batch that reads as a complete one is how a partial
        # answer gets mistaken for the whole picture.
        log.warning(
            "model returned %d visuals; kept the first %d",
            len(proposals),
            MAX_VISUAL_PROPOSALS,
        )
```

and:

```python
def _must_render(session: Session, template: Template, renderer: HtmlRenderer) -> None:
    """Render the proposal from its own slot examples, or reject it.

    The examples are what the model wrote down as representative, so they are the honest
    values to prove the layout with — the same ones the template editor previews from.
    """
    values = {
        str(slot.get("name")): str(slot.get("example") or slot.get("name") or "")
        for slot in template.slots
    }
    render_template(session, template, values, renderer)
```

Imports needed in `extraction.py`: `MissingSlotValue`, `UnsupportedRenderer`, `HtmlRenderer` from `app.rendering`, `UnresolvableAsset` from `app.assets`, `render_template` from `app.generation`.

- [ ] **Step 5: Run the tests and verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_visual_extraction.py -q`
Expected: PASS — including every test from Task 4, whose `propose_visuals(session, FakeLLM(...))` calls must be updated to pass a `FakeRenderer()`.

- [ ] **Step 6: Add the route**

In `backend/app/api_templates.py`, next to `extract_structures`:

```python
@router.post("/extract/visuals", status_code=201)
def extract_visuals(
    session: SessionDep,
    llm: LLMDep,
    html_renderer: HtmlRendererDep,
    platform: str = "linkedin",
    cohort: Cohort = Cohort.VOICE,
) -> list[Template]:
    """Ask the model for visual layouts from the images of the strongest posts.

    Slower than the other two extract routes by design: each proposal is rendered once
    before it is offered, so nothing reaches the review queue that cannot be drawn.
    """
    try:
        proposals = propose_visuals(session, llm, html_renderer, platform=platform, cohort=cohort)
    except (ExtractionError, LLMResponseError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    session.commit()
    for proposal in proposals:
        session.refresh(proposal)
    return proposals
```

- [ ] **Step 7: Write the route test**

Append to `backend/tests/test_visual_extraction.py`:

```python
def test_extract_visuals_route_returns_proposals(session):
    add_post(session, "strong", 900, media=(png(), ".png"))
    session.commit()

    app.dependency_overrides[get_llm] = lambda: FakeLLM(ONE_VISUAL)
    app.dependency_overrides[get_html_renderer] = lambda: FakeRenderer()
    try:
        response = TestClient(app).post("/templates/extract/visuals")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    assert [t["name"] for t in response.json()] == ["ranked-bars"]
    assert response.json()[0]["status"] == "proposed"
```

- [ ] **Step 8: Run the whole backend suite**

Run: `cd backend && .venv/bin/pytest -q`
Expected: PASS

- [ ] **Step 9: Break it on purpose**

Delete the `_must_render` call and confirm `test_a_proposal_that_does_not_render_is_dropped` fails. Change `slot.get("role") == "logo"` to `slot.get("name") == "logo"` and confirm both logo tests fail. Revert.

- [ ] **Step 10: Commit**

```bash
git add backend/app/extraction.py backend/app/api_templates.py backend/app/config.py .env.example backend/tests/test_visual_extraction.py
git commit -m "extraction: render every proposal before offering it, and pin the logo

A layout that does not render is not a proposal — it is a trap with an
approval on it, and 50 proposals already wait in that queue. So each one
is drawn once from its own slot examples first.

The logo is pinned by default_asset_id on the slot's declared role, never
its name: left_image_url reads as an asset and hero does not. Unset
BRAND_LOGO_ASSET_ID is the normal first-run state and raises nothing.

The route is slow on purpose and says so. Over-limit batches are logged,
never silently truncated."
```

---

## Task 6: Preview pane in the template editor

**Files:**
- Create: `frontend/src/app/templates/TemplatePreview.tsx`
- Create: `frontend/src/app/templates/TemplatePreview.test.tsx`
- Modify: `frontend/src/app/templates/TemplateManager.tsx:210-230`

**Interfaces:**
- Consumes: `POST /templates/preview` (Task 3)
- Produces: `<TemplatePreview body={string} slots={unknown[]} />` — renders a button and, once pressed, an `<img>` of the result

**Read first:** `frontend/AGENTS.md` — this Next.js version has breaking changes; check `node_modules/next/dist/docs/` before writing component code.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/app/templates/TemplatePreview.test.tsx`, following the mocking style already used in `TemplateManager.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import TemplatePreview from "./TemplatePreview";

const postBlob = vi.fn();
vi.mock("@/lib/api", () => ({ postBlob: (...args: unknown[]) => postBlob(...args) }));

describe("TemplatePreview", () => {
  it("previews the body in the editor, not a saved template", async () => {
    postBlob.mockResolvedValue({ ok: true, data: new Blob(["x"]) });
    render(
      <TemplatePreview
        body={'{"renderer":"html","html":"<h1>{headline}</h1>"}'}
        slots={[{ name: "headline", type: "text", example: "Edited" }]}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: /preview/i }));

    expect(postBlob).toHaveBeenCalledWith("/templates/preview", {
      body: { renderer: "html", html: "<h1>{headline}</h1>" },
      slots: [{ name: "headline", type: "text", example: "Edited" }],
      values: { headline: "Edited" },
    });
    expect(await screen.findByAltText(/preview/i)).toBeInTheDocument();
  });

  it("says so rather than rendering nothing when the body is not JSON", async () => {
    render(<TemplatePreview body="{not json" slots={[]} />);

    await userEvent.click(screen.getByRole("button", { name: /preview/i }));

    expect(postBlob).not.toHaveBeenCalled();
    expect(screen.getByText(/not valid JSON/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `cd frontend && pnpm test TemplatePreview`
Expected: FAIL — cannot resolve `./TemplatePreview`

- [ ] **Step 3: Write the component**

Create `frontend/src/app/templates/TemplatePreview.tsx`:

```tsx
"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { postBlob } from "@/lib/api";

/**
 * Renders what is in the editor, not what is saved.
 *
 * The saved-template preview already existed; this one exists because the thing an author
 * needs to see is the edit they have not committed yet. Slot examples are the values,
 * for the same reason the saved preview uses them: they are what the author wrote down
 * as representative.
 */
export default function TemplatePreview({ body, slots }: { body: string; slots: unknown[] }) {
  const [url, setUrl] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function preview() {
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(body);
    } catch {
      setProblem("Body is not valid JSON.");
      return;
    }

    const values = Object.fromEntries(
      (slots as Record<string, unknown>[]).map((slot) => [
        String(slot.name),
        String(slot.example ?? slot.name ?? ""),
      ]),
    );

    setBusy(true);
    setProblem(null);
    const result = await postBlob("/templates/preview", { body: parsed, slots, values });
    setBusy(false);

    if (result.ok) setUrl(URL.createObjectURL(result.data));
    else setProblem(result.message);
  }

  return (
    <div className="mt-4">
      <Button type="button" onClick={preview} disabled={busy}>
        {busy ? "Rendering…" : "Preview"}
      </Button>
      {problem && <p className="mt-2 text-meta text-muted">{problem}</p>}
      {/* eslint-disable-next-line @next/next/no-img-element -- a blob: URL from the render,
          not an asset next/image can optimise. */}
      {url && <img src={url} alt="Template preview" className="mt-3 w-full rounded-input" />}
    </div>
  );
}
```

- [ ] **Step 4: Run the test and verify it passes**

Run: `cd frontend && pnpm test TemplatePreview`
Expected: PASS

- [ ] **Step 5: Mount it, only for visuals**

In `TemplateManager.tsx`, inside the author/edit form below the body textarea:

```tsx
{kind === "visual" && (
  <TemplatePreview body={body} slots={editing?.slots ?? []} />
)}
```

- [ ] **Step 6: Write the mounting test**

Append to `frontend/src/app/templates/TemplateManager.test.tsx`:

```tsx
it("offers a preview for a visual and not for a hook", async () => {
  render(<TemplateManager initial={[]} />);

  expect(screen.queryByRole("button", { name: /^preview$/i })).not.toBeInTheDocument();

  await userEvent.selectOptions(screen.getByLabelText(/kind/i), "visual");

  expect(screen.getByRole("button", { name: /^preview$/i })).toBeInTheDocument();
});
```

Adjust the kind-selection query to whatever `TemplateManager.test.tsx` already uses for that control — it is a Radix `Select`, not a native `<select>`, so copy the existing pattern rather than inventing one.

- [ ] **Step 7: Run the frontend suite**

Run: `cd frontend && pnpm test`
Expected: PASS

- [ ] **Step 8: Break it on purpose**

Change `kind === "visual"` to `kind === "hook"` and confirm the mounting test fails. Revert.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/app/templates/
git commit -m "templates: preview the edit, not the saved version

Editing a layout meant hand-writing HTML blind and pressing save to find
out what you got. The saved-template preview already existed; the thing
an author needs to see is the edit they have not committed yet."
```

---

## Task 7: A redraw keeps the previous image

**Files:**
- Modify: `backend/app/models/draft.py:52`
- Create: `backend/alembic/versions/<hash>_draft_previous_visual.py`
- Modify: `backend/app/api_drafts.py:98-118` (`_out`), `454-476` (`redraw`)
- Test: `backend/tests/test_visual_iteration.py` (create)

**Interfaces:**
- Consumes: nothing from Tasks 1–6
- Produces: `Draft.previous_visual: bytes | None`; `DraftOut.has_previous_visual: bool`; `GET /drafts/{id}/previous-visual` → `image/png`; `POST /drafts/{id}/restore-visual` → `DraftOut`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_visual_iteration.py`:

```python
def test_a_redraw_keeps_the_image_it_replaced(session):
    """Two images answer 'is this better than what I had', which is the question asked."""
    draft = a_draft_with_visual(session, image=b"FIRST")
    session.commit()

    app.dependency_overrides[get_html_renderer] = lambda: ConstantRenderer(b"SECOND")
    try:
        response = TestClient(app).post(f"/drafts/{draft.id}/regenerate-visual")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["has_previous_visual"] is True
    session.refresh(draft)
    assert draft.visual_image == b"SECOND"
    assert draft.previous_visual == b"FIRST"


def test_the_previous_image_is_downloadable(session):
    draft = a_draft_with_visual(session, image=b"FIRST", previous=b"OLDER")
    session.commit()

    response = TestClient(app).get(f"/drafts/{draft.id}/previous-visual")

    assert response.status_code == 200
    assert response.content == b"OLDER"


def test_restoring_swaps_them_rather_than_discarding(session):
    """Restore is itself undoable — a swap, not a rollback, so neither image is lost."""
    draft = a_draft_with_visual(session, image=b"NEW", previous=b"OLD")
    session.commit()

    response = TestClient(app).post(f"/drafts/{draft.id}/restore-visual")

    assert response.status_code == 200
    session.refresh(draft)
    assert draft.visual_image == b"OLD"
    assert draft.previous_visual == b"NEW"


def test_restoring_with_nothing_to_restore_is_a_409(session):
    draft = a_draft_with_visual(session, image=b"ONLY")
    session.commit()

    response = TestClient(app).post(f"/drafts/{draft.id}/restore-visual")

    assert response.status_code == 409


def test_a_failed_redraw_does_not_destroy_the_image_that_worked(session):
    """The previous image is only kept when there is a new one to replace it with.

    `_draw_visual` records a failure by setting `visual_image = None`. Moving that None
    into `previous_visual` would turn one bad render into the loss of both pictures.
    """
    draft = a_draft_with_visual(session, image=b"GOOD", previous=b"OLDER")
    session.commit()

    app.dependency_overrides[get_html_renderer] = lambda: ExplodingRenderer()
    try:
        TestClient(app).post(f"/drafts/{draft.id}/regenerate-visual")
    finally:
        app.dependency_overrides.clear()

    session.refresh(draft)
    assert draft.previous_visual == b"OLDER"
```

Write `a_draft_with_visual`, `ConstantRenderer` and `ExplodingRenderer` at the top of the file, copying the draft-construction helper already in `backend/tests/test_generation.py` so the lineage fields match what that file uses.

- [ ] **Step 2: Run the tests and verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_visual_iteration.py -q`
Expected: FAIL — `Draft` has no attribute `previous_visual`

- [ ] **Step 3: Add the column and the migration**

In `backend/app/models/draft.py`, below `visual_image`:

```python
    # The image the last redraw replaced, so "is this better than what I had" is answerable.
    #
    # ponytail: one level of undo, not a history table. Two images answer the question
    # actually being asked. Add a table when someone wants three.
    previous_visual: bytes | None = Field(default=None, sa_column=Column(LargeBinary))
```

Generate the migration:

```bash
cd backend && .venv/bin/alembic revision --autogenerate -m "draft previous visual"
```

Check the generated file adds exactly one nullable `LargeBinary` column and nothing else — autogenerate has previously proposed unrelated drops in this project. Then `cd backend && .venv/bin/alembic upgrade head`.

- [ ] **Step 4: Keep, expose and restore**

In `backend/app/api_drafts.py`, in `redraw`, before the redraw call:

```python
    previous = draft.visual_image
```

and after it:

```python
    # Only when the redraw produced something. `_draw_visual` records a failure by setting
    # `visual_image = None`, and moving that None across would turn one bad render into
    # the loss of both pictures.
    if draft.visual_image is not None:
        draft.previous_visual = previous
```

In `_out`, add `has_previous_visual=draft.previous_visual is not None` and the matching field on `DraftOut`. Then:

```python
@router.get("/{draft_id}/previous-visual")
def previous_visual(session: SessionDep, draft_id: int) -> Response:
    """The image the last redraw replaced, for showing beside the current one."""
    draft = _load(session, draft_id)
    if draft.previous_visual is None:
        raise HTTPException(status_code=404, detail=f"draft {draft_id} has no previous visual")
    return Response(content=draft.previous_visual, media_type="image/png")


@router.post("/{draft_id}/restore-visual")
def restore_visual(session: SessionDep, draft_id: int) -> DraftOut:
    """Put the previous image back, keeping the one it replaces.

    A swap, not a rollback: restoring is itself undoable, so a mis-click costs nothing.
    """
    draft = _load(session, draft_id)
    if draft.previous_visual is None:
        raise HTTPException(status_code=409, detail=f"draft {draft_id} has no previous visual")
    draft.visual_image, draft.previous_visual = draft.previous_visual, draft.visual_image
    draft.visual_error = None
    session.add(draft)
    session.commit()
    session.refresh(draft)
    return _out(session, draft)
```

- [ ] **Step 5: Run the tests and verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_visual_iteration.py tests/test_generation.py tests/test_publishing.py -q`
Expected: PASS — the latter two are the files that already exercise `DraftOut`, so they prove the new field broke no existing response.

- [ ] **Step 6: Break it on purpose**

Remove the `if draft.visual_image is not None:` guard and confirm `test_a_failed_redraw_does_not_destroy_the_image_that_worked` fails. Change the restore to `draft.visual_image = draft.previous_visual` without the swap and confirm `test_restoring_swaps_them_rather_than_discarding` fails. Revert.

- [ ] **Step 7: Commit**

```bash
git add backend/app/models/draft.py backend/alembic/versions/ backend/app/api_drafts.py backend/tests/test_visual_iteration.py
git commit -m "drafts: a redraw keeps the image it replaced

regenerate-visual overwrote, so 'make it warmer' cost you the version you
had. The previous image is kept only when the redraw actually produced
one — _draw_visual signals failure with visual_image = None, and moving
that across would turn one bad render into the loss of both pictures.

Restore is a swap, not a rollback, so it is itself undoable."
```

---

## Task 8: Studio shows both images

**Files:**
- Modify: `frontend/src/app/studio/Studio.tsx`
- Modify: `frontend/src/app/studio/Studio.test.tsx`

**Interfaces:**
- Consumes: `DraftOut.has_previous_visual`, `GET /drafts/{id}/previous-visual`, `POST /drafts/{id}/restore-visual` (Task 7)
- Produces: nothing downstream

**Read first:** `frontend/AGENTS.md`.

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/app/studio/Studio.test.tsx`, matching the existing draft fixture shape in that file:

```tsx
it("shows the replaced image beside the new one, and can put it back", async () => {
  const draft = { ...aDraft(), id: 7, has_previous_visual: true };
  render(<Studio initial={draft} />);

  expect(screen.getByAltText(/previous visual/i)).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: /keep the previous/i }));

  expect(postJson).toHaveBeenCalledWith("/drafts/7/restore-visual", undefined, "POST");
});

it("shows no comparison when nothing has been replaced", () => {
  render(<Studio initial={{ ...aDraft(), has_previous_visual: false }} />);

  expect(screen.queryByAltText(/previous visual/i)).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `cd frontend && pnpm test Studio`
Expected: FAIL — no element with alt text "previous visual"

- [ ] **Step 3: Render the comparison**

In `Studio.tsx`, beside the existing visual `<img>`, gated on `draft.has_previous_visual`:

```tsx
{draft.has_previous_visual && (
  <div className="mt-4">
    <p className="text-meta text-muted">Replaced by the last redraw</p>
    {/* eslint-disable-next-line @next/next/no-img-element -- bytes from the API, not an asset. */}
    <img
      src={`${API_BASE}/drafts/${draft.id}/previous-visual`}
      alt="Previous visual"
      className="mt-2 w-full rounded-input opacity-70"
    />
    <Button type="button" onClick={() => call(`/drafts/${draft.id}/restore-visual`)}>
      Keep the previous one
    </Button>
  </div>
)}
```

Use whatever `Studio.tsx` already calls its mutation helper and its API base constant — copy the existing pattern rather than importing a new one.

- [ ] **Step 4: Run the frontend suite**

Run: `cd frontend && pnpm test`
Expected: PASS

- [ ] **Step 5: Break it on purpose**

Change the gate to `!draft.has_previous_visual` and confirm both tests fail. Revert.

- [ ] **Step 6: Run the full gate**

Run: `make check`
Expected: PASS — ruff, mypy, eslint, tsc, pytest, vitest.

- [ ] **Step 7: Look at it in a browser**

Three of the worst defects in this project were found by rendering the page and looking at it with a fully green suite. Run `make api` and `make web`, then:

- `/templates` — author a visual, press Preview, confirm an image appears and that a broken body reports rather than showing nothing.
- `/studio` — open a draft, redraw, confirm both images appear and that "Keep the previous one" swaps them.
- Check both in **light and dark**, at **390px and 1440px**. The dark-mode-invisible-control and the 10,384px-wide-page defects were both of exactly this kind.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/app/studio/
git commit -m "studio: show the image a redraw replaced, and offer to keep it

Verified in a browser at 390 and 1440, light and dark — the suite is
green either way, which is what makes that step load-bearing here."
```

---

## Not in this plan

Carried from the spec, so nobody adds them by reflex:

- **No ranking of visual templates and no visual lessons from verdicts.** There are zero verdicts. Recording is what this plan builds; learning turns on when there is something to learn from.
- **No change to the `ai` renderer path.** `AzureImageRenderer` is untouched.
- **No Gemini client.** Slice 1 needs vision, and `gpt-5-5` has it.
- **No visual template builder.** The editor stays a JSON textarea with a preview beside it.
- **Slice 2** — Nano Banana Pro generating pictorial `image_url` slots, `POST /assets/generate`, and `Asset` gaining a prompt, source template and parent id — is scoped at the end of the spec and is a separate plan.

## After the plan

`README.md`'s "Current state" block and its screen table both describe the tool as it was before this. Update the template counts and add visual extraction to the Templates row in the same commit as Task 5, or immediately after.
