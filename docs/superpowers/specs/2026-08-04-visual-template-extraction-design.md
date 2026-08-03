# Visual template extraction — design

**Date:** 2026-08-04
**Slice:** 1 of 2. Slice 2 (generative pictorial assets) is scoped at the end and not built here.

---

## The problem

`extraction.py` has `propose_hooks` and `propose_structures`. It has no `propose_visuals`.
Every one of the 11 approved VISUAL templates was hand-authored, so the half of the circuit
that learns from what worked runs on words only. A post's picture is not evidence of anything
yet, and the tool has no way to turn a picture that worked into a shape it can reuse.

Two things follow from that, and they are what this slice fixes:

1. **A visual that performed cannot become a template.** 58 post images sit on disk, joined to
   engagement, and nothing reads them.
2. **A template cannot be changed usefully from inside the app.** `/templates` edits a raw JSON
   textarea: changing a layout means hand-writing HTML blind and pressing save to find out what
   you got.

---

## What already exists, and is not rebuilt here

Naming these explicitly because a design for "template-driven image generation" reads like all
of it is missing, and none of it is:

| Capability | Where |
|---|---|
| Visual templates with `renderer: "html"` → Cloudflare screenshot | `rendering.render_visual`, `rendering.CloudflareRenderer` |
| Visual templates with `renderer: "ai"` → Azure image generation | `rendering.AzureImageRenderer` |
| `image_url` slots resolved to embedded file bytes | `assets.resolve_asset_values`, `assets.data_uri` |
| Per-slot `default_asset_id` fallback | `generation.chosen_assets` |
| Versioned templates, PROPOSED → APPROVED flow | `models.template.Template`, `api_templates` |
| Preview one saved template | `POST /templates/{id}/preview` |
| Re-render a draft's visual | `POST /drafts/{id}/regenerate-visual` |
| Delete guard naming every holder of an asset | `assets.holders_of` |

---

## The finding that decides the architecture

Four post images were read directly — two of Monte's, two from the creator-inspiration cohort.
All four are the same species: **a typographic data-graphic.** Kicker, large headline, subhead,
ranked rows or bars carrying real numbers, an annotation pill, a source line, brand orange as
the only saturated colour, warm off-white ground, third-party brand marks as real PNGs.

An image model cannot produce these. Asked for the Purina graphic it returns garbled brand
names, invented dollar figures, a hallucinated Anthropic mark and a Pixii logo that is *close*.
The concern about the logo breaking is correct and it does not stop at the logo — it is every
number and every word.

So: **extraction proposes HTML layouts, not prompts.** The renderer that already exists produces
these correctly, and the logo problem is solved by construction rather than by conditioning —
an `image_url` slot pinned to the real logo asset embeds the actual file bytes, and no model
ever draws it.

This is a stronger guarantee than reference-image conditioning, and it is cheaper, because the
mechanism is already written.

---

## Design

### 1 · `extraction.propose_visuals`

Mirrors `propose_hooks` and `propose_structures` in shape, sample and provenance discipline.

**Sample.** `_strongest_posts`, ordered by `engaged_actions`, narrowed to posts whose
`local_media_path` names a still image. `.mp4` is excluded — there are three in `media/` and a
video frame is not the artefact.

`_strongest_posts` gains one parameter, `require_content: bool = True`, and visual extraction
passes `False`. The existing `func.trim(Post.content) != ""` filter exists because a post with
no text carries no hook. A post that is *only* an image carries no hook and is still visual
evidence; its own comment records that at least one creator post is exactly that. Excluding it
here would discard the purest sample in the set.

**Vision.** The `LLM` protocol widens by one optional argument:

```python
def complete_json(self, system: str, user: str, images: Sequence[bytes] = ()) -> dict: ...
```

`AzureChat` builds a multimodal user message when `images` is non-empty — content becomes a list
of parts, each image a `{"type": "image_url", "image_url": {"url": "data:image/png;base64,…"}}`.
The deployment is `gpt-5-5`, which is multimodal, so no new provider is introduced in this slice.
Existing callers pass nothing and are unaffected.

**Output.** The model returns proposals shaped:

```json
{
  "proposals": [{
    "name": "ranked-bars",
    "html": "<div class=…>{kicker}</div><h1>{headline}</h1>…",
    "slots": [
      {"name": "kicker",   "type": "text",      "example": "PRIME DAY 2026 · THE AI SHELF"},
      {"name": "headline", "type": "text",      "example": "AI did the shopping this year."},
      {"name": "logo",     "type": "image_url", "role": "logo", "example": "https://…"}
    ],
    "rationale": "one sentence",
    "source_post_ids": ["…"]
  }]
}
```

The prompt carries the brand tokens — the colours, type stack and spacing from
`monte-workshop/bots-and-tools/brand/brand.json`, copied into a constant in this repo rather
than read across repositories at runtime. `renderer: "html"` is set by `_to_visual`, not asked
for: a proposal is a layout by definition in this slice.

**Dimensions are read off the source image, not asked for.** The corpus is not one size — the
sample read for this design contained both 1080×1350 portrait and 1080×1080 square — and a model
asked to state a size will state a plausible one rather than the true one.

The sample step therefore returns `(Post, bytes)` pairs and keeps them, and `_to_visual` is handed
that mapping. `width` and `height` come from the image already in memory, keyed by post.
**Not re-resolved from `provenance`** — that column is `list[str]` of Zernio ids, so recovering a
file from it means a `Post` lookup by `zernio_id` and then `local_media_path`, for bytes the
caller is already holding. Falls back to `rendering.DEFAULT_WIDTH` / `DEFAULT_HEIGHT` when a
proposal cites more than one source, or none.

### 2 · Three validations, before a proposal is ever offered

A proposal that reaches `/templates` broken is worse than no proposal — it is a broken thing
with a human's approval on it. 50 proposals already wait in that queue; none of them should be
traps.

**a. Slots and markup agree.** Every `{slot}` found in the markup by `rendering._SLOT` must be
declared, and every declared slot must appear in the markup. An undeclared placeholder raises
`MissingSlotValue` at render time — after approval, in Studio, to whoever is trying to write a
post. Caught here instead.

**b. It renders.** Each surviving proposal is rendered once through the preview path using its
own slot examples. A layout that does not render is not a proposal.

> **This route is slow, and that is expected rather than a hang.** Extraction is already one
> LLM call; this adds one Cloudflare render per proposal, and `CloudflareRenderer` absorbs a
> per-minute 429 with 5 + 10 + 20 = 35s of backoff across 4 attempts. A rate-limited run of five
> proposals can therefore sit for minutes where `extract_hooks` returns in seconds. Proposals are
> capped at **5** so the worst case is bounded, and the route documents the wait. Rendering at
> approval time instead was considered and rejected: it moves the failure to the moment a human
> has already said yes, which is precisely what this validation exists to prevent.

**c. Provenance is checkable.** Only `source_post_ids` the model was actually shown survive —
same rule as `_to_template`.

A proposal failing (a) or (b) is dropped with its reason recorded in `notes`, not raised. One bad
layout in five must not cost the other four.

### 3 · The logo, locked

`settings.brand_logo_asset_id: int | None = None`.

When set, `_to_visual` writes `default_asset_id` onto every slot the model declared with
`"role": "logo"`. `generation.chosen_assets` already falls back to `default_asset_id` when
nothing is picked, and `assets.data_uri` embeds the real bytes, so the logo is byte-exact in
every render with no further work.

**Pinned on the declared `role`, never on the slot's name.** `assets._image_slot_names` carries
the reason: `left_image_url` reads like an asset and `hero` does not, so a name heuristic here
reintroduces exactly the class of silent wrongness the typed-slot design exists to close.

When the setting is unset, extraction still works — the logo slot simply carries no default and
Studio's asset picker asks. No hard failure, because a first run happens before anyone has
uploaded a logo.

Setup is one existing call: `POST /assets` with the logo file, then the returned id into `.env`.

### 4 · Edit with live preview

`POST /templates/preview` — takes a body and slots directly instead of a template id, because an
unsaved edit has no id.

**Both preview routes must first apply `generation.chosen_assets`, and today neither does.**
`preview_visual` passes caller-supplied `values` straight into `resolve_asset_values`, which never
consults a slot's `default_asset_id`. So a logo slot previews from its `example` URL while
generating from the pinned asset — two different pictures from the same template, with nothing
raised. That is the same divergence `resolve_asset_values` documents in its own docstring as the
reason all three render paths go through it: *a slot that resolves when a draft is generated and
not when it is previewed is worse than one that never resolves, because the failure is invisible
until someone looks at the picture.*

Preview is now load-bearing twice over — it is validation (b), and it is what the editor shows —
so this is fixed here rather than noted. `chosen_assets(template, values)` first, then
`resolve_asset_values`, in both routes. Without it, validation (b) proves the layout renders but
proves nothing about the logo, and the editor shows a logo that is not the one that ships.

`TemplateManager` grows a preview pane beside the JSON editor for `kind === "visual"`: edit,
render, look, then save. Saving still writes a new version through the existing `PUT`, so past
drafts keep pointing at the wording that earned their numbers.

This is the "freedom to change the template from inside Pixii Intelligence" the tool did not
really have. The editor stays JSON — authoring markup in a visual builder is not this slice.

### 5 · Iteration recorded

`Draft.previous_visual: bytes | None`. `regenerate-visual` moves the current image there before
writing the new one, and the draft response exposes both so Studio can show them side by side
with a "keep the previous one" control.

> ponytail: one level of undo, not a history table. Two images answer "is this better than what
> I had", which is the question actually being asked. Add a table when someone wants three.

---

## What this deliberately does not do

**It does not rank visual templates, and it does not learn from verdicts yet.** There are zero
verdicts recorded. A system that infers visual preferences from nothing and presents them as
evidence is the exact failure the README refuses on the text side, where the threshold is ~300
posts with lineage and the count is 0. Recording is built now — every generated image already
carries `visual_family` / `visual_version` — and the learning turns on when there is something
to learn from.

**It does not touch the `ai` renderer path.** `AzureImageRenderer` stays exactly as it is.

**It does not add a Gemini client.** Slice 1 needs vision, which `gpt-5-5` already has.

---

## Data and config changes

| Change | Kind |
|---|---|
| `Draft.previous_visual: bytes \| None` | Alembic migration, nullable, no backfill |
| `settings.brand_logo_asset_id: int \| None = None` | config field + `.env.example` line |
| `LLM.complete_json(..., images=())` | protocol widening, existing callers unaffected |
| `_strongest_posts(..., require_content=True)` | parameter, default preserves behaviour |
| `preview_visual` applies `chosen_assets` before resolving | **behaviour change to an existing route** — a slot left unset now renders its pinned default instead of failing or showing the example |

`BRAND_LOGO_ASSET_ID` is not a credential — it is an integer row id, so it needs no `repr=False`
and no `/health` flag. No new secret is introduced by this slice.

---

## Errors

| Condition | Behaviour |
|---|---|
| No post has a still image | returns `[]`, model never called — same as an empty hook sample. `propose_hooks` and `propose_structures` both return `[]` here, and one extractor that raises where its siblings return empty is a difference an operator has to memorise |
| Model returns no proposals | `ExtractionError` |
| One proposal has undeclared slots | dropped, reason into `notes`, others proceed |
| One proposal fails to render | dropped, reason into `notes`, others proceed |
| Cloudflare rate-limits | absorbed by the existing backoff in `CloudflareRenderer` |
| `brand_logo_asset_id` names a missing asset | logo slot gets no default; render asks for a pick |

---

## Testing

Backend, against fixtures — no live calls in the suite:

- `_strongest_posts(require_content=False)` includes an image-only post and still excludes
  `excluded_from_extraction` and the wrong cohort.
- Video-only `local_media_path` is excluded from the visual sample.
- `AzureChat.complete_json` with `images` builds multimodal parts; without, the payload is
  byte-identical to today's.
- A proposal whose markup references an undeclared slot is dropped, and the others survive.
- A proposal whose render raises is dropped, and the others survive.
- `role: "logo"` gets `default_asset_id`; a slot merely *named* `logo` without the role does not.
- `brand_logo_asset_id` unset produces a logo slot with no default and no error.
- `POST /templates/preview` renders an unsaved body; a body with a missing slot value 4xxs.
- Both preview routes render a logo slot from its pinned `default_asset_id` when the caller
  supplies no value for it — the assertion that fails today.
- A proposal derived from a 1080×1080 source carries 1080×1080, not the 1080×1350 default.
- `regenerate-visual` moves the old image to `previous_visual` and does not lose it.

Frontend:

- Preview pane appears for `kind === "visual"` and not for hook or structure.
- The side-by-side previous/new control renders when `previous_visual` is present.

Per the README's own standard: each of these is confirmed by breaking the code on purpose and
checking the test fails.

---

## Slice 2 — generative pictorial assets (scoped, not built)

Once layouts are real, the image model gets a narrow and correct job: filling `image_url` slots
that are **pictorial** — a background texture, a hero illustration, a product render. No text in
them, so nothing to garble, and never the logo.

That slice adds a Gemini renderer (`gemini-3-pro-image-preview`, reached with the
`GOOGLE_AI_API_KEY` already populated in the vault `.env`), whose contract differs from
`ImageRenderer` because it takes reference images:
`generate(prompt, width, height, references: Sequence[bytes] = ())`. It adds
`POST /assets/generate` and gives `Asset` a prompt, a source template and a parent asset id, so a
generated image can be traced and iterated rather than merely stored.

Splitting there keeps the widened renderer protocol out of a slice that has no use for it.

---

## Open risks

- **Whether `gpt-5-5` returns usable HTML from a screenshot is unproven.** It is the load-bearing
  assumption of the slice. First real run is the test; if the layouts come back unusable, the
  fallback is a fixed set of hand-authored layouts with the model choosing among them and filling
  slots — smaller, still useful, and it reuses everything above except the markup generation.
- **Rendered output will not match the source pixel for pixel**, and should not be expected to.
  The template is the *shape* — the same abstraction a hook template is.
- **`brand.json` is copied, not referenced.** It lives in another repo, so it will drift. One
  constant in one file, re-copied when the brand changes.
