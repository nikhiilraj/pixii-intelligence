"""Every prompt this application sends, with its version and the shape it asks for.

Moved here from module constants in `generation.py` and `autonomous.py`. The texts are
**byte-identical** to the constants they replaced — `tests/test_prompts.py` pins each one
against a literal written independently of this file, so a stray space is a failing test and
not a quietly different draft. Editing one of these is not an edit: it is a new version,
because a trace row naming `1.0.0` has to mean the same words next month as it did today.

`extraction.py`'s three prompts are deliberately still module constants. They belong here
too and the move is mechanical, but it is a separate file with a separate owner this wave.
"""

from app.prompts.registry import Prompt

WRITE = Prompt(
    name="draft.write",
    version="1.0.0",
    text="""\
You write LinkedIn posts in an established voice, following a given hook pattern and post
structure.

Rules:
- Follow the hook pattern's shape. Do not copy its example wording.
- Follow the structure's sections in order. Each section's guidance is a requirement.
- Match the voice of the exemplar posts: their casing, rhythm, sentence length and how they
  handle numbers. Imitate the manner, never the content.
- Use only facts present in the idea. Invent no statistics, names, or outcomes.
- No hashtags. No emoji. No "in today's fast-paced world" openings.
- Fill every visual slot you are given with a short value drawn from the post.

Return ONLY JSON of this shape, with no commentary:
{
  "hook": "the opening line or two",
  "body": "the rest of the post, blank line between paragraphs",
  "visual_values": {"slot_name": "short value"}
}""",
    output_schema={
        "type": "object",
        # `visual_values` is absent from `required` on purpose: a visual with no writable
        # slots asks for none, and `_written_values` reads it with `.get(...) or {}`.
        "required": ["hook", "body"],
        "properties": {
            "hook": {"type": "string"},
            "body": {"type": "string"},
            "visual_values": {"type": "object", "additionalProperties": {"type": "string"}},
        },
    },
)

SUGGEST = Prompt(
    name="draft.suggest_templates",
    version="1.0.0",
    text="""\
You choose which templates suit an idea.

Pick exactly one hook, one structure and one visual from the supplied lists, by name.
Choose on fit between the idea and what each template is for. Return ONLY JSON:
{"hook": "name", "structure": "name", "visual": "name", "reason": "one sentence"}""",
    output_schema={
        "type": "object",
        "required": ["hook", "structure", "visual", "reason"],
        "properties": {
            # Template *names*, not ids — the model is shown names and answers in them, and
            # `_by_name` resolves. A schema saying "integer" here would describe a prompt
            # nobody wrote.
            "hook": {"type": "string"},
            "structure": {"type": "string"},
            "visual": {"type": "string"},
            "reason": {"type": "string"},
        },
    },
)

PROPOSE_TOPICS = Prompt(
    name="topics.propose",
    version="1.0.0",
    text="""\
You propose fresh post topics for a company that already posts regularly.

You will be shown its recent posts. Propose angles it has NOT already covered, that follow
plausibly from the same expertise and audience. Each topic must be specific enough to write
from — a claim, a finding, or a question with a stake in it — never a category.

Rules:
- Do not repeat a subject already covered in the posts shown.
- Do not invent statistics or outcomes. A topic may point at something worth checking, but
  it must not assert numbers as fact.
- Prefer topics the audience would argue about over topics they would nod at.

Return ONLY JSON: {"topics": [{"idea": "one specific angle", "why": "one sentence"}]}""",
    output_schema={
        "type": "object",
        "required": ["topics"],
        "properties": {
            "topics": {
                "type": "array",
                "items": {
                    "type": "object",
                    # `why` is asked for but not required: `propose_topics` keeps any topic
                    # carrying an `idea` and drops the rest, so a missing rationale costs a
                    # sentence of context, not the topic.
                    "required": ["idea"],
                    "properties": {"idea": {"type": "string"}, "why": {"type": "string"}},
                },
            }
        },
    },
)

# Every prompt the registry knows about. Listed explicitly rather than discovered by scanning
# the module: an import-time side effect that silently registers nothing — a renamed module, a
# failed import swallowed somewhere — would surface as `UnknownPrompt` mid-generation instead
# of at the line that forgot to add it here.
ALL: tuple[Prompt, ...] = (WRITE, SUGGEST, PROPOSE_TOPICS)
