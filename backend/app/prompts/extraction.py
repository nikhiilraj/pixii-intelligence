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

# The instruction inverted, not reworded. 1.0.0 asks the model to abstract a shape out of the
# twelve strongest posts; this asks what *recurs* across every post in the corpus. That is a
# different question, so it is a major version and 1.0.0 stays registered — every trace row and
# every template already proposed still names the words that actually produced it.
#
# Both removals from the sample show up here. There is no "strongest first" because the sample
# is no longer ordered, and there is no engagement figure because engagement cannot answer
# "what repeats". The last rule is not decoration either: a model handed 274 posts and asked
# for ten patterns will offer to rank them unless told the order means nothing.
#
# The `family_id` rule is the half of reconciliation that lives in the prompt: the families that
# already have names go into the user message, and a pattern that is the same shape as one of
# them comes back carrying its id instead of a new name. Without it the write path has nothing
# to reconcile *on* — which is how roughly 12 posts produced 42 hook families.
HOOKS_TEXT_V2 = """\
You find the hook patterns that recur across a body of social posts.

A hook is the opening — the first line or two that decides whether someone keeps reading.
Your job is to find the *repeatable shape* underneath specific wording, so it can be reused
for different subject matter.

You are given every post in the corpus, in no particular order. Report the shapes that
genuinely **recur** across them. A shape that appears once is not a pattern, it is one post.

Rules:
- Return at most 10 patterns. Fewer, sharper patterns beat many overlapping ones.
- For each pattern, list the id of EVERY post it covers, not one example. A pattern citing a
  single post is a transcription of that post.
- Only cite a post the pattern actually describes. A longer list is not a better one, and a
  pattern stretched loosely over thirty posts is worse than an honest one over five.
- Never cite an id you were not given.
- You are also given the hook families that already have names, with their ids. If one of
  your patterns is the same shape as one of those, return that family's family_id and
  describe the pattern as you find it now. Leave family_id out only for a shape none of
  them covers.
- Express each pattern with {slot_name} placeholders for the parts that change.
- Describe tone concretely (casing, rhythm, whether it leads with a number), not as praise.
- Do not rank the patterns and do not call any of them best. The order you return them in
  carries no meaning.

Return ONLY JSON of this shape, with no commentary:
{
  "hooks": [
    {
      "name": "short-kebab-name",
      "family_id": "an existing family's id, omitted when the pattern is a new one",
      "pattern": "{slot} literal text {slot}",
      "tone": "concrete description",
      "slots": [{"name": "slot", "example": "a real example"}],
      "source_post_ids": ["id", "..."],
      "rationale": "what makes this recur, one sentence"
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

# The same inversion `HOOKS_TEXT_V2` records, applied to structures, so a major version for the
# same reason: 1.0.0 asks the model to describe what the strongest posts do, this asks what
# shape *recurs* across every post. 1.0.0 stays registered — the 15 structures already in the
# library came out of those words.
#
# Two changes are specific to structures rather than carried across. `source_post_ids` is asked
# for in the example JSON at all, which 1.0.0 never did: that omission is the whole reason all
# 15 rows cite nothing, since a field the shape does not show is a field the model does not
# send. And `compatible_hooks` asks for a family_id where 1.0.0 asked for a name — a hook that
# gains a version takes the name the model gave it, so names stopped being stable identifiers
# the day extraction started reconciling.
STRUCTURES_TEXT_V2 = """\
You find the post structures that recur across a body of social posts.

A structure is the ordered shape of a whole post — what the beginning, middle and end each
do — independent of the subject matter. It is what gives a draft a shape to follow.

You are given every post in the corpus, in full and in no particular order. Report the shapes
that genuinely **recur** across them. A shape that appears once is not a pattern, it is one
post.

Rules:
- Return at most 10 structures. Two or three sharply different ones beat many similar ones.
- For each structure, list the id of EVERY post it covers, not one example. A structure
  citing a single post is a transcription of that post.
- Only cite a post the structure actually describes. A longer list is not a better one, and a
  shape stretched loosely over thirty posts is worse than an honest one over five.
- Never cite an id you were not given.
- You are also given the structure families that already have names, with their ids. If one of
  your structures is the same shape as one of those, return that family's family_id and
  describe the structure as you find it now. Leave family_id out only for a shape none of
  them covers.
- Give each section a short name and concrete guidance on what belongs there. Guidance must
  be actionable ("state the cost in dollars"), never vague ("be engaging").
- Sections are ordered: beginning first, end last.
- Name the post type you are describing, e.g. "offer-reward" (a post that gives something
  away in exchange for a comment or follow) or "deep-research" (a post presenting original
  findings or a teardown).
- In "compatible_hooks", give the family_id of hooks from the supplied list, never their
  names. If none fit, return an empty list.
- Do not rank the structures and do not call any of them best. The order you return them in
  carries no meaning.

Return ONLY JSON of this shape, with no commentary:
{
  "structures": [
    {
      "name": "short-kebab-name",
      "family_id": "an existing family's id, omitted when the structure is a new one",
      "post_type": "offer-reward",
      "sections": [{"name": "hook", "guidance": "what this section must do"}],
      "compatible_hooks": ["a hook family_id from the list you were given"],
      "source_post_ids": ["id", "..."],
      "rationale": "what makes this recur, one sentence"
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

HOOKS_V2 = Prompt(
    name="extraction.hooks",
    version="2.0.0",
    text=HOOKS_TEXT_V2,
    output_schema={
        "type": "object",
        "required": ["hooks"],
        "properties": {
            "hooks": {
                "type": "array",
                "items": {
                    "type": "object",
                    # `source_post_ids` joins `name` and `pattern` here where 1.0.0 left it
                    # optional, and it is not a tightening for its own sake: `_to_template`
                    # now rejects a proposal whose citations do not survive the filter, and
                    # coverage decides whether the template arrives approved. A schema that
                    # still called the field optional would describe a laxer prompt than the
                    # one that is sent — the mirror of the reason 1.0.0 demands so little.
                    "required": ["name", "pattern", "source_post_ids"],
                    "properties": {
                        "name": {"type": "string"},
                        # Offered, never demanded: the first run has no family to cite, and
                        # `traced_call` does not validate a response against this schema
                        # anyway — the schema documents the prompt and `_to_template` is what
                        # enforces it. A required field here would describe a promise nothing
                        # keeps.
                        "family_id": {"type": "string"},
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

STRUCTURES_V2 = Prompt(
    name="extraction.structures",
    version="2.0.0",
    text=STRUCTURES_TEXT_V2,
    output_schema={
        "type": "object",
        "required": ["structures"],
        "properties": {
            "structures": {
                "type": "array",
                "items": {
                    "type": "object",
                    # `source_post_ids` joins `name` and `sections`, and this is the change
                    # that ends structure provenance being empty in every row. It is safe to
                    # demand only because `_to_structure` filters the citations to ids the
                    # model was shown first: coverage decides whether the template arrives
                    # approved, so an unfiltered list of invented ids would auto-approve a
                    # structure covering nothing.
                    "required": ["name", "sections", "source_post_ids"],
                    "properties": {
                        "name": {"type": "string"},
                        # Offered, never demanded, for the reason `HOOKS_V2` records: the
                        # first run has no family to cite, and `traced_call` does not validate
                        # a response against this schema anyway — `_to_structure` is what
                        # enforces it.
                        "family_id": {"type": "string"},
                        "post_type": {"type": "string"},
                        "sections": {"type": "array", "items": {"type": "object"}},
                        # Hook *family ids* now, where 1.0.0 carried names. The type is the
                        # same and the meaning is not, which is half of why this is a major
                        # version rather than a reworded 1.0.1.
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

PROMPTS: tuple[Prompt, ...] = (HOOKS, HOOKS_V2, STRUCTURES, STRUCTURES_V2, VISUALS)
