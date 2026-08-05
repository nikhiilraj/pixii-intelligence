"""The registry, and the guarantee that moving a prompt into it changed nothing.

The pinned texts below are the whole point of this file. They were taken from the module
constants *before* the move and are written out here as literals — never derived from
`app.prompts`, which would make every assertion a tautology that passes however the text
drifts. A one-character change to a prompt fails here, loudly, with a readable diff.
"""

import hashlib

import pytest

from app import prompts
from app.autonomous import _TOPICS
from app.extraction import _HOOKS, _STRUCTURES, _VISUALS
from app.generation import _SUGGEST, _WRITE
from app.prompts.registry import DuplicatePrompt, Prompt, index

WRITE_TEXT = """\
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
}"""

SUGGEST_TEXT = """\
You choose which templates suit an idea.

Pick exactly one hook, one structure and one visual from the supplied lists, by name.
Choose on fit between the idea and what each template is for. Return ONLY JSON:
{"hook": "name", "structure": "name", "visual": "name", "reason": "one sentence"}"""

TOPICS_TEXT = """\
You propose fresh post topics for a company that already posts regularly.

You will be shown its recent posts. Propose angles it has NOT already covered, that follow
plausibly from the same expertise and audience. Each topic must be specific enough to write
from — a claim, a finding, or a question with a stake in it — never a category.

Rules:
- Do not repeat a subject already covered in the posts shown.
- Do not invent statistics or outcomes. A topic may point at something worth checking, but
  it must not assert numbers as fact.
- Prefer topics the audience would argue about over topics they would nod at.

Return ONLY JSON: {"topics": [{"idea": "one specific angle", "why": "one sentence"}]}"""

# The same three texts as they hashed on 2026-08-05, straight off the module constants before
# they moved. Belt and braces with the literals above: a literal can be edited in the same
# commit as the prompt it is meant to pin, and these digests make that a second, deliberate
# act rather than a find-and-replace.
FROZEN_DIGESTS = {
    "draft.write": "1fe2f85d6cb8054014bc9343215bf667b10c34398ed77c6311657fad583fc9ed",
    "draft.suggest_templates": "e75b978e9ebfd994be439e45a3a1dfcc87e8e08d09482b50c788e7c9fae8cd7e",
    "topics.propose": "29c5e00f7fc6e2c47f3330a0dff0878e2502e38f9a5bfdf489d0a099e8952981",
    # Extraction's three, hashed on 2026-08-05 off `extraction.py`'s module constants in the
    # commit before they moved. Digests and no literal, unlike the three above, for one real
    # reason and one practical one. `extraction.visuals` is composed from `BRAND` — a literal
    # copy of the rendered text would fork the brand rules into a second place that can
    # silently disagree with the one the prompt actually interpolates, which is worse than no
    # copy at all. And the three run to 5.3KB between them; a diff nobody reads is not a
    # readable diff. What the digest buys is unchanged: editing `BRAND`, or a space anywhere
    # in any of the three, fails here rather than quietly proposing something different.
    "extraction.hooks": "6efd4bc4d244bc150eb54ecb77a8fc19ea471493eaca6df6616fd676e4868e4a",
    "extraction.structures": "0e1d752f8b597fd6eb0f988825819aefe05f4d0a14a76973af3068656a9ecede",
    "extraction.visuals": "dd2c2803f1b0dd440a8fadc13da00dda41f716aeb07fb2bd700be54df2436fe9",
}


# --- the move changed nothing ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("draft.write", WRITE_TEXT),
        ("draft.suggest_templates", SUGGEST_TEXT),
        ("topics.propose", TOPICS_TEXT),
    ],
)
def test_a_registered_prompt_is_byte_identical_to_the_constant_it_replaced(name, text):
    assert prompts.get(name, "1.0.0").text == text


@pytest.mark.parametrize(("name", "digest"), sorted(FROZEN_DIGESTS.items()))
def test_a_registered_prompt_still_hashes_to_what_it_did_before_the_move(name, digest):
    assert hashlib.sha256(prompts.get(name, "1.0.0").text.encode("utf-8")).hexdigest() == digest


@pytest.mark.parametrize(
    ("pinned", "name"),
    [
        (_WRITE, "draft.write"),
        (_SUGGEST, "draft.suggest_templates"),
        (_TOPICS, "topics.propose"),
        (_HOOKS, "extraction.hooks"),
        (_STRUCTURES, "extraction.structures"),
        (_VISUALS, "extraction.visuals"),
    ],
)
def test_the_call_sites_send_the_registered_prompt_and_not_a_copy(pinned, name):
    """`generation`, `autonomous` and `extraction` hold the registry's own object, not a copy.

    Together with the pinned texts above, this is what makes the byte-identity claim reach the
    model rather than stopping at the registry: the constant these modules pass to
    `complete_json` *is* the row the pin covers.
    """
    assert pinned is prompts.get(name, "1.0.0")


def test_the_json_shape_stays_inside_the_prompt_text():
    """`output_schema` is a declaration alongside the text, never a substitute for it.

    Lifting the `Return ONLY JSON` block out of the prompt to avoid stating the shape twice
    would change what the model is sent — which is exactly the thing this slice may not do.
    """
    for prompt in prompts.ALL:
        assert "Return ONLY JSON" in prompt.text


# --- lookup: exact, or nothing --------------------------------------------------------------


def test_a_version_that_was_never_registered_raises_rather_than_falling_back():
    """The mutation this guards: `get` reaching for the newest version on a miss.

    That is the `(family_id, version)` rule one layer up. A registry that answered `2.0.0` to
    a caller asking for `1.5.0` would send different words while every trace row, evaluation
    baseline and already-written draft still named the version that was asked for.
    """
    with pytest.raises(prompts.UnknownPrompt):
        prompts.get("draft.write", "9.9.9")


def test_a_name_that_was_never_registered_raises():
    with pytest.raises(prompts.UnknownPrompt):
        prompts.get("draft.wrote", "1.0.0")


def test_each_registered_version_of_one_name_answers_as_itself():
    """Two versions coexist and neither shadows the other, in either lookup direction."""
    old = Prompt(name="t.example", version="1.0.0", text="old words", output_schema={})
    new = Prompt(name="t.example", version="2.0.0", text="new words", output_schema={})
    table = index((old, new))

    assert table[("t.example", "1.0.0")].text == "old words"
    assert table[("t.example", "2.0.0")].text == "new words"


def test_the_registry_offers_no_way_to_ask_for_the_newest_version():
    """Its absence is the design, so its arrival should fail a test rather than pass review."""
    assert not hasattr(prompts, "latest")
    assert not [name for name in dir(prompts) if "latest" in name.lower()]


def test_two_prompts_claiming_one_name_and_version_are_refused():
    """Silently keeping one of them would make every trace row under that key ambiguous."""
    twin = Prompt(name="t.example", version="1.0.0", text="a", output_schema={})
    other = Prompt(name="t.example", version="1.0.0", text="b", output_schema={})
    with pytest.raises(DuplicatePrompt):
        index((twin, other))


def test_the_library_indexes_without_collision():
    assert len(index(prompts.ALL)) == len(prompts.ALL)


# --- versions are versions ------------------------------------------------------------------


@pytest.mark.parametrize("version", ["1.0", "1", "v1.0.0", "1.0.0-rc1", "latest", ""])
def test_a_version_that_is_not_major_minor_patch_is_refused_at_construction(version):
    """`"1.0"` and `"1.0.0"` naming one intent is an `UnknownPrompt` mid-generation."""
    with pytest.raises(ValueError):
        Prompt(name="t.example", version=version, text="words", output_schema={})


def test_every_registered_prompt_carries_a_semantic_version():
    for prompt in prompts.ALL:
        assert prompt.version.count(".") == 2


def test_a_prompt_cannot_be_edited_in_place():
    """Editing a prompt is a new version, not a mutation of the one already traced."""
    prompt = prompts.get("draft.write", "1.0.0")
    with pytest.raises(Exception):  # noqa: B017 — dataclasses raise FrozenInstanceError
        prompt.text = "something else"  # type: ignore[misc]


# --- the declared output shape --------------------------------------------------------------


def test_every_prompt_declares_an_object_schema_whose_required_keys_it_defines():
    """A `required` key with no `properties` entry describes nothing an evaluator can check."""
    for prompt in prompts.ALL:
        schema = prompt.output_schema
        assert schema["type"] == "object"
        assert set(schema.get("required", [])) <= set(schema["properties"])


def test_the_write_prompt_declares_the_keys_the_generator_actually_reads():
    """`generation` reads `hook`, `body` and `visual_values` off the model's answer."""
    schema = prompts.get("draft.write", "1.0.0").output_schema
    assert set(schema["properties"]) == {"hook", "body", "visual_values"}
    # `visual_values` is not required: a visual with no writable slots asks for none, and
    # `_written_values` reads it with `.get(...) or {}`.
    assert set(schema["required"]) == {"hook", "body"}


def test_the_suggest_prompt_declares_the_four_keys_it_asks_the_model_for():
    """All four required, matching the text — including `reason`, which the operator reads.

    `required` states what the *prompt* demands, which is stricter than what `suggest_templates`
    survives: `_by_name` falls back to the first approved template deliberately, so a model
    naming nothing does not cost a draft. The schema is the contract; the caller's tolerance is
    a separate, documented decision.
    """
    schema = prompts.get("draft.suggest_templates", "1.0.0").output_schema
    assert set(schema["required"]) == {"hook", "structure", "visual", "reason"}


def test_the_topics_prompt_requires_an_idea_on_every_topic():
    """`propose_topics` drops any topic without an `idea` — it is the only load-bearing key."""
    schema = prompts.get("topics.propose", "1.0.0").output_schema
    assert schema["required"] == ["topics"]
    item = schema["properties"]["topics"]["items"]
    assert item["required"] == ["idea"]
    assert set(item["properties"]) == {"idea", "why"}


def test_every_prompt_module_is_aggregated_into_the_registry():
    """A prompt module that exists but is not in `library.ALL` is registered nowhere.

    This one is here because a mutation caught its absence. `app/prompts/editorial.py` and
    `app/prompts/rubric.py` were written by separate agents against one working tree —
    `library.py` is last-writer-wins, so each exports its own `PROMPTS` and the aggregation
    is a single line elsewhere. Deleting either from that line failed **no test**: the
    owning modules resolve prompts out of their own tuple, so they kept working while their
    prompts quietly stopped being subject to every invariant below and stopped being
    reachable by `prompts.get`.

    Discovered by walking the package rather than listing the modules, because the failure
    this guards against is a *new* module nobody remembered to aggregate — a hardcoded list
    would need the same edit that was forgotten.
    """
    import importlib
    import pkgutil

    import app.prompts as package

    registered = {(p.name, p.version) for p in prompts.ALL}
    missing: list[str] = []
    for info in pkgutil.iter_modules(package.__path__):
        if info.name in {"registry", "library", "tracing"}:
            continue
        module = importlib.import_module(f"app.prompts.{info.name}")
        for prompt in getattr(module, "PROMPTS", ()):
            if (prompt.name, prompt.version) not in registered:
                missing.append(f"app/prompts/{info.name}.py: {prompt.name} {prompt.version}")

    assert not missing, (
        "these prompts exist but are not in library.ALL, so no invariant here applies to "
        f"them and prompts.get cannot find them: {missing}"
    )
