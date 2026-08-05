"""The three prompts extraction sends: hooks, structures, and visual layouts.

Module constants in `extraction.py` until now. `library.py` said the move was mechanical and
deferred because that file had a separate owner; this is that move. The texts are **carried
across verbatim** — the digests in `tests/test_prompts.py` were taken off the constants before
they moved, so a stray space is a failing test rather than a quietly different proposal.

Registering them is what puts them in a `GenerationTrace` row. Extraction is the one stage
whose output a human then approves into a template that every later draft is generated from, so
"which prompt proposed this template" is a lineage question, not a diagnostic one — and it was
the only stage that could not answer it.

`BRAND` travels with `VISUALS_TEXT` because the prompt interpolates it. **Editing `BRAND` is
editing that prompt**, and needs a version bump exactly as editing the words around it would:
the two are one string by the time a model sees them, and a trace row naming `1.0.0` has to
mean the same string next month. Keeping the interpolation rather than snapshotting the
rendered text is deliberate — a snapshot would fork the brand rules into a second copy that can
silently disagree with the one `extraction` documents.
"""

from app.prompts.registry import Prompt

HOOKS_TEXT = """\
You extract reusable hook patterns from social posts that already performed well.

A hook is the opening — the first line or two that decides whether someone keeps reading.
Your job is to find the *repeatable shape* underneath specific wording, so it can be reused
for different subject matter.

Rules:
- Abstract only what genuinely repeats or is genuinely transferable. Do not invent patterns.
- Express each pattern with {slot_name} placeholders for the parts that change.
- Ground every pattern in the posts that justify it, by their id. Never cite an id you were
  not given.
- Describe tone concretely (casing, rhythm, whether it leads with a number), not as praise.
- Prefer fewer, sharper patterns over many overlapping ones.

Return ONLY JSON of this shape, with no commentary:
{
  "hooks": [
    {
      "name": "short-kebab-name",
      "pattern": "{slot} literal text {slot}",
      "tone": "concrete description",
      "slots": [{"name": "slot", "example": "a real example"}],
      "source_post_ids": ["id", "..."],
      "rationale": "why this works, one sentence"
    }
  ]
}"""

STRUCTURES_TEXT = """\
You extract reusable post structures from social posts that already performed well.

A structure is the ordered shape of a whole post — what the beginning, middle and end each
do — independent of the subject matter. It is what gives a draft a shape to follow.

Rules:
- Give each section a short name and concrete guidance on what belongs there. Guidance must
  be actionable ("state the cost in dollars"), never vague ("be engaging").
- Sections are ordered: beginning first, end last.
- Base every structure on what the strongest posts actually do. Do not invent a shape.
- Name the post type you are describing, e.g. "offer-reward" (a post that gives something
  away in exchange for a comment or follow) or "deep-research" (a post presenting original
  findings or a teardown).
- In "compatible_hooks", name only hooks from the supplied list of available hook templates.
  If none fit, return an empty list.
- Prefer two or three sharply different structures over many similar ones.

Return ONLY JSON of this shape, with no commentary:
{
  "structures": [
    {
      "name": "short-kebab-name",
      "post_type": "offer-reward",
      "sections": [{"name": "hook", "guidance": "what this section must do"}],
      "compatible_hooks": ["hook-template-name"],
      "rationale": "one sentence"
    }
  ]
}"""

# A transcription of monte-workshop/bots-and-tools/brand/brand.json's colors, fonts and
# quick_rules (version 2026-06-10), rather than read across repositories at runtime.
# ponytail: one constant, re-copied when the brand changes. Make it a fetch when a second
# tool in this repo needs the same values. Re-copying it is a prompt edit — see above.
BRAND = """\
Colours: #d65831 primary orange is the ONLY saturated colour — everything else is neutral.
#FAFAF8 page background (warm smoke, not cold white). #FFFFFF card surface. #1A1816 text.
#7A756D muted text. #E0DFDB borders. On an orange background use white text, with
rgba(0,0,0,0.1-0.15) for any card overlay.
Type: Cabinet Grotesk Bold or Extrabold for headlines, set large and tight. No full stops
in headlines. Use digits, never spelled-out numbers.
Layout: rounded corners — 12-20px on cards, 8-10px on buttons. No heavy drop shadows.
Clean whitespace. No eyebrow or kicker tag above a headline.
The logo is never typed as text in any font. It arrives as a real file through an image
slot."""

VISUALS_TEXT = f"""\
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
- The brand rules below outrank the source image. Some posts that performed well break
  them — a kicker above the headline, a full stop at the end of one. Do not carry that
  across: follow the rule, and name the rule the source broke in the rationale.
  brand.json is where the brand is decided; the corpus is only where shapes are found.

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


HOOKS = Prompt(
    name="extraction.hooks",
    version="1.0.0",
    text=HOOKS_TEXT,
    output_schema={
        "type": "object",
        "required": ["hooks"],
        "properties": {
            "hooks": {
                "type": "array",
                "items": {
                    "type": "object",
                    # `name` and `pattern` only. `_to_template` rejects a proposal missing
                    # either and accepts one missing everything else — `tone`, `rationale` and
                    # `slots` all read through `or`-defaults — so demanding them here would
                    # describe a stricter prompt than the one that is sent.
                    "required": ["name", "pattern"],
                    "properties": {
                        "name": {"type": "string"},
                        "pattern": {"type": "string"},
                        "tone": {"type": "string"},
                        "slots": {"type": "array", "items": {"type": "object"}},
                        "source_post_ids": {"type": "array", "items": {"type": "string"}},
                        "rationale": {"type": "string"},
                    },
                },
            }
        },
    },
)

STRUCTURES = Prompt(
    name="extraction.structures",
    version="1.0.0",
    text=STRUCTURES_TEXT,
    output_schema={
        "type": "object",
        "required": ["structures"],
        "properties": {
            "structures": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["name", "sections"],
                    "properties": {
                        "name": {"type": "string"},
                        "post_type": {"type": "string"},
                        "sections": {"type": "array", "items": {"type": "object"}},
                        "compatible_hooks": {"type": "array", "items": {"type": "string"}},
                        "rationale": {"type": "string"},
                        "source_post_ids": {"type": "array", "items": {"type": "string"}},
                    },
                },
            }
        },
    },
)

VISUALS = Prompt(
    name="extraction.visuals",
    version="1.0.0",
    text=VISUALS_TEXT,
    output_schema={
        "type": "object",
        "required": ["visuals"],
        "properties": {
            "visuals": {
                "type": "array",
                "items": {
                    "type": "object",
                    # `slots` is required here where the hook prompt leaves it optional, and
                    # the difference is real: every placeholder in the markup must appear in
                    # `slots` or `_must_render` discards the proposal, so a layout with none
                    # is a layout with nothing to fill in.
                    "required": ["name", "html", "slots"],
                    "properties": {
                        "name": {"type": "string"},
                        "html": {"type": "string"},
                        "slots": {"type": "array", "items": {"type": "object"}},
                        "source_post_ids": {"type": "array", "items": {"type": "string"}},
                        "rationale": {"type": "string"},
                    },
                },
            }
        },
    },
)

PROMPTS: tuple[Prompt, ...] = (HOOKS, STRUCTURES, VISUALS)
