import pytest

from app.extraction import ExtractionError, compatible_hooks, propose_structures
from app.models.post import Post
from app.models.template import TemplateKind, TemplateStatus
from app.templates import approve, create_template


class FakeLLM:
    def __init__(self, response: dict):
        self.response = response
        self.user: str | None = None

    def complete_json(self, system: str, user: str) -> dict:
        self.user = user
        return self.response


OFFER_REWARD = {
    "structures": [
        {
            "name": "offer-reward",
            "post_type": "offer-reward",
            "sections": [
                {"name": "hook", "guidance": "Lead with the outcome and its price."},
                {"name": "proof", "guidance": "Show the artefact. Concrete numbers only."},
                {"name": "reward", "guidance": "Name the free resource and how to claim it."},
            ],
            "compatible_hooks": ["ai-time-value-equation", "does-not-exist"],
        }
    ]
}


def add_post(session, zid: str, engaged: int, content: str) -> Post:
    post = Post(zernio_id=zid, platform="linkedin", content=content, engaged_actions=engaged)
    session.add(post)
    session.flush()
    return post


def a_hook(session, name: str, *, approved: bool = True):
    hook = create_template(
        session, kind=TemplateKind.HOOK, name=name, body={"pattern": "{a} = {b}"}
    )
    if approved:
        approve(session, hook)
    return hook


def test_creates_a_proposed_structure_with_its_sections_in_order(session):
    add_post(session, "win-1", 185, "Hook line.\n\nProof body.\n\nComment WORD for the doc.")

    structures = propose_structures(session, FakeLLM(OFFER_REWARD))

    assert len(structures) == 1
    structure = structures[0]
    assert structure.kind == TemplateKind.STRUCTURE
    assert structure.status == TemplateStatus.PROPOSED
    assert [s["name"] for s in structure.body["sections"]] == ["hook", "proof", "reward"]


def test_each_section_carries_its_own_guidance(session):
    add_post(session, "win-1", 185, "Body.")

    structure = propose_structures(session, FakeLLM(OFFER_REWARD))[0]

    assert structure.body["sections"][1]["guidance"].startswith("Show the artefact")


def test_the_post_type_is_recorded(session):
    add_post(session, "win-1", 185, "Body.")

    structure = propose_structures(session, FakeLLM(OFFER_REWARD))[0]

    assert structure.body["post_type"] == "offer-reward"


def test_compatible_hooks_are_resolved_to_families_that_exist(session):
    hook = a_hook(session, "ai-time-value-equation")
    add_post(session, "win-1", 185, "Body.")

    structure = propose_structures(session, FakeLLM(OFFER_REWARD))[0]

    assert structure.body["compatible_hook_families"] == [hook.family_id]


def test_a_named_hook_that_does_not_exist_is_dropped(session):
    a_hook(session, "ai-time-value-equation")
    add_post(session, "win-1", 185, "Body.")

    structure = propose_structures(session, FakeLLM(OFFER_REWARD))[0]

    assert len(structure.body["compatible_hook_families"]) == 1


def test_selecting_a_structure_surfaces_its_compatible_hooks(session):
    hook = a_hook(session, "ai-time-value-equation")
    add_post(session, "win-1", 185, "Body.")
    structure = propose_structures(session, FakeLLM(OFFER_REWARD))[0]

    assert [h.id for h in compatible_hooks(session, structure)] == [hook.id]


def test_an_unapproved_compatible_hook_is_not_surfaced(session):
    a_hook(session, "ai-time-value-equation", approved=False)
    add_post(session, "win-1", 185, "Body.")
    structure = propose_structures(session, FakeLLM(OFFER_REWARD))[0]

    assert compatible_hooks(session, structure) == []


def test_a_structure_naming_no_hooks_surfaces_none(session):
    add_post(session, "win-1", 185, "Body.")
    structure = create_template(
        session, kind=TemplateKind.STRUCTURE, name="bare", body={"sections": []}
    )

    assert compatible_hooks(session, structure) == []


def test_the_model_sees_whole_posts_not_just_their_openings(session):
    body = "Opening line.\n\n" + ("Middle paragraph that carries the argument. " * 8) + "\n\nClose."
    add_post(session, "win-1", 185, body)

    llm = FakeLLM(OFFER_REWARD)
    propose_structures(session, llm)

    assert "Middle paragraph that carries the argument." in llm.user
    assert "Close." in llm.user


def test_the_strongest_posts_lead_the_prompt(session):
    add_post(session, "weak", 1, "Weak post body.")
    add_post(session, "strong", 500, "Strong post body.")

    llm = FakeLLM(OFFER_REWARD)
    propose_structures(session, llm)

    assert llm.user.index("Strong post body.") < llm.user.index("Weak post body.")


def test_an_empty_corpus_produces_nothing_and_does_not_call_the_model(session):
    llm = FakeLLM(OFFER_REWARD)

    assert propose_structures(session, llm) == []
    assert llm.user is None


def test_a_response_without_structures_is_an_error(session):
    add_post(session, "win-1", 185, "Body.")

    with pytest.raises(ExtractionError):
        propose_structures(session, FakeLLM({"hooks": []}))


def test_a_structure_without_sections_is_rejected(session):
    add_post(session, "win-1", 185, "Body.")

    with pytest.raises(ExtractionError):
        propose_structures(session, FakeLLM({"structures": [{"name": "hollow", "sections": []}]}))
