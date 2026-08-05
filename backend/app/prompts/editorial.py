"""The two prompts the editorial stages send, with their versions and the shapes they ask for.

Kept in their own module rather than in `library.py` because three slices are being built
against one working tree this wave and a shared file is last-writer-wins. `PROMPTS` below is
the whole of what this slice registers; the library aggregates it. Nothing here may be edited
in place — editing a prompt is a new version, because a trace row naming `1.0.0` has to mean
the same words next month as it does today.

**Neither prompt is allowed to decide how much research the brief needs.** `research.resolve_mode`
owns that, it is deliberately not a thing a model can argue with, and the brief prompt says so
in as many words so that a later editor does not add the field back as a convenience.
"""

from app.prompts.registry import Prompt

BRIEF = Prompt(
    name="editorial.brief",
    version="1.0.0",
    text="""\
You turn a post idea into an editorial brief: what publishing it should achieve, who it is
for, what a reader should do about it, and what limits the writing.

Rules:
- The objective is what publishing this should achieve for the company. One sentence, and
  something you could tell afterwards whether it happened.
- Name one audience, specifically enough that someone outside it would know they are not in
  it. "Professionals" is not an audience.
- The desired action is one thing a reader could do having read the post. One action, not a
  list — a post asking for three things asks for nothing.
- Constraints are limits on the writing: what to avoid, what to include, how it must read.
  Return an empty list if the idea implies none. Do not invent limits to fill it.
- Do not decide how much research this needs, and do not comment on how much it needs. That
  is settled elsewhere and it is not yours to set.
- Do not offer other angles on the idea, and do not compare angles with each other.
- Invent no statistics, names, or outcomes. If the idea implies a fact, leave the fact to the
  claim plan; you are describing the job, not doing it.

Return ONLY JSON of this shape, with no commentary:
{
  "objective": "what publishing this should achieve",
  "audience": "who it is for",
  "desired_action": "the one thing a reader should do",
  "constraints": ["one limit on the writing"]
}""",
    output_schema={
        "type": "object",
        # All four required. `constraints` may be empty and is still demanded: an omitted key
        # would be indistinguishable from "there are none", and those are different answers.
        # `build_brief` still tolerates an absent key — the schema states what the prompt
        # demands of the model, which is stricter than what the caller survives.
        "required": ["objective", "audience", "desired_action", "constraints"],
        "properties": {
            "objective": {"type": "string"},
            "audience": {"type": "string"},
            "desired_action": {"type": "string"},
            "constraints": {"type": "array", "items": {"type": "string"}},
        },
    },
)

ANGLE_PLAN = Prompt(
    name="editorial.angle_plan",
    version="1.0.0",
    text="""\
You plan the argument of one post: the single thing it will say, and the claims it will make
to say it.

Rules:
- One thesis. Not a menu of directions, not two ideas joined by "and". State the one thing
  this post argues, in a sentence somebody could disagree with.
- Do not offer alternative angles and do not compare angles with each other.
- The tension is what is genuinely in dispute — the reason the thesis is worth saying rather
  than assumed. A thesis nobody would argue with has no tension and needs a different thesis.
- The stake is what this audience gains or loses by it. Written about them, not about you.
- **Each claim is one sentence asserting one thing, and it must stand on its own.** Somebody
  reading a single claim, with none of the others and none of the post, must be able to say
  what it asserts and go and check it. A paragraph is not a claim. Two claims joined by "and"
  are two claims. Split them.
- List every claim the post intends to make, including the ones you believe are obvious. A
  claim left out of this list is a claim nobody will check.
- Do not invent statistics, dates, prices or named outcomes to make a claim sound stronger.
  Write the claim you mean; whether it holds up is checked later, and a fabricated number will
  fail that check with the whole post attached to it.
- The beats are how the post moves from the tension to the call to action, in order. Each beat
  is one sentence saying what that part of the post does.

Return ONLY JSON of this shape, with no commentary:
{
  "thesis": "the one thing this post argues",
  "tension": "what is in dispute",
  "audience_stake": "what this audience gains or loses by it",
  "claims": [{"text": "one sentence asserting one thing"}],
  "beats": ["what this part of the post does"],
  "cta": "the one thing a reader should do"
}""",
    output_schema={
        "type": "object",
        "required": ["thesis", "tension", "audience_stake", "claims", "beats", "cta"],
        "properties": {
            "thesis": {"type": "string"},
            "tension": {"type": "string"},
            "audience_stake": {"type": "string"},
            "claims": {
                "type": "array",
                # An object with a `text` key rather than a bare string, and the difference is
                # the point of the artifact: a claim is a row the next slice attaches a
                # citation to, so it is a thing with fields from the moment it is asked for.
                "items": {
                    "type": "object",
                    "required": ["text"],
                    "properties": {"text": {"type": "string"}},
                },
            },
            "beats": {"type": "array", "items": {"type": "string"}},
            "cta": {"type": "string"},
        },
    },
)

# Every prompt this slice registers. Listed explicitly, like `library.ALL`, so that forgetting
# one is a line somebody can see rather than an `UnknownPrompt` mid-generation.
PROMPTS: tuple[Prompt, ...] = (BRIEF, ANGLE_PLAN)
