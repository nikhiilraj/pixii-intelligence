import pytest

from app.config import settings
from app.extraction import ExtractionError, propose_hooks
from app.models.post import Post
from app.models.template import TemplateKind, TemplateStatus
from app.templates import latest_versions, usable_templates


class FakeLLM:
    """Records the prompt it was given and replays a canned response."""

    def __init__(self, response: dict):
        self.response = response
        self.system: str | None = None
        self.user: str | None = None

    def complete_json(self, system: str, user: str) -> dict:
        self.system, self.user = system, user
        return self.response


TWO_HOOKS = {
    "hooks": [
        {
            "name": "transformation",
            "pattern": "{small_value} turned into {large_value} {unit}",
            "tone": "plain, lowercase, numbers first",
            "slots": [{"name": "small_value", "example": "$450"}],
            "source_post_ids": ["win-1"],
            "rationale": "leads with the delta",
        },
        {
            "name": "deadline",
            "pattern": "On {date}, {platform} will {change}",
            "tone": "urgent",
            "slots": [{"name": "date", "example": "July 27"}],
            "source_post_ids": ["win-2"],
        },
    ]
}


def add_post(
    session,
    zid: str,
    engaged: int,
    content: str = "A hook line.\n\nBody text.",
    author: str | None = None,
) -> Post:
    post = Post(
        zernio_id=zid,
        platform="linkedin",
        content=content,
        engaged_actions=engaged,
        impressions=engaged * 30,
        account_username=author or settings.voice_account,
    )
    session.add(post)
    session.flush()
    return post


def test_creates_a_proposed_hook_template_per_proposal(session):
    add_post(session, "win-1", 185)
    add_post(session, "win-2", 139)

    proposals = propose_hooks(session, FakeLLM(TWO_HOOKS))

    assert len(proposals) == 2
    assert all(t.kind == TemplateKind.HOOK for t in proposals)
    assert all(t.status == TemplateStatus.PROPOSED for t in proposals)
    assert all(t.version == 1 for t in proposals)


def test_a_proposal_is_not_usable_until_approved(session):
    add_post(session, "win-1", 185)

    propose_hooks(session, FakeLLM(TWO_HOOKS))

    assert usable_templates(session, TemplateKind.HOOK) == []
    assert len(latest_versions(session, TemplateKind.HOOK)) == 2


def test_each_proposal_records_the_posts_it_came_from(session):
    add_post(session, "win-1", 185)
    add_post(session, "win-2", 139)

    proposals = propose_hooks(session, FakeLLM(TWO_HOOKS))

    by_name = {t.name: t for t in proposals}
    assert by_name["transformation"].provenance == ["win-1"]
    assert by_name["deadline"].provenance == ["win-2"]


def test_pattern_tone_and_slots_are_persisted(session):
    add_post(session, "win-1", 185)

    proposals = propose_hooks(session, FakeLLM(TWO_HOOKS))
    proposal = next(t for t in proposals if t.name == "transformation")

    assert proposal.body["pattern"] == "{small_value} turned into {large_value} {unit}"
    assert proposal.body["tone"] == "plain, lowercase, numbers first"
    assert proposal.slots == [{"name": "small_value", "example": "$450"}]


def test_the_model_only_sees_the_strongest_posts_by_engaged_actions(session):
    for index in range(12):
        add_post(session, f"p{index}", engaged=index, content=f"Hook number {index}.")

    llm = FakeLLM(TWO_HOOKS)
    propose_hooks(session, llm, sample_size=3)

    assert "Hook number 11." in llm.user
    assert "Hook number 10." in llm.user
    assert "Hook number 0." not in llm.user


def test_the_prompt_carries_each_posts_engagement_so_the_model_can_weigh_it(session):
    add_post(session, "win-1", 185)

    llm = FakeLLM(TWO_HOOKS)
    propose_hooks(session, llm)

    assert "185" in llm.user
    assert "win-1" in llm.user


def test_extraction_is_scoped_to_one_platform(session):
    add_post(session, "li", 185)
    other = add_post(session, "tw", 900)
    other.platform = "twitter"
    session.add(other)
    session.flush()

    llm = FakeLLM(TWO_HOOKS)
    propose_hooks(session, llm, platform="linkedin")

    assert "li" in llm.user
    assert "tw" not in llm.user


def test_posts_without_content_are_not_offered_as_evidence(session):
    add_post(session, "empty", 500, content="   ")
    add_post(session, "win-1", 185)

    llm = FakeLLM(TWO_HOOKS)
    propose_hooks(session, llm)

    assert "empty" not in llm.user


def test_a_textless_post_does_not_consume_a_sample_slot(session):
    # It outranks everything, so it is drawn first and would otherwise cost a real post
    # its place in the sample.
    add_post(session, "empty", 500, content="   ")
    for index in range(3):
        add_post(session, f"real{index}", engaged=index, content=f"Hook number {index}.")

    llm = FakeLLM(TWO_HOOKS)
    propose_hooks(session, llm, sample_size=3)

    assert [f"Hook number {i}." in llm.user for i in range(3)] == [True, True, True]


def test_only_the_voice_accounts_own_posts_shape_templates(session):
    # A creator post stays in the corpus as reference material, but the templates claim to
    # describe Monte's voice, so they cannot be led by someone else's writing.
    add_post(session, "creator", 1240, content="Someone else's hook.", author="External creator")
    add_post(session, "ours", 185, content="Monte's own hook.")

    llm = FakeLLM(TWO_HOOKS)
    propose_hooks(session, llm)

    assert "Monte's own hook." in llm.user
    assert "Someone else's hook." not in llm.user


def test_a_post_excluded_from_extraction_is_not_evidence(session):
    strongest = add_post(session, "launch", 500, content="Breaking out of stealth.")
    strongest.excluded_from_extraction = True
    session.add(strongest)
    session.flush()
    add_post(session, "win-1", 185, content="A repeatable hook.")

    llm = FakeLLM(TWO_HOOKS)
    propose_hooks(session, llm)

    assert "A repeatable hook." in llm.user
    assert "Breaking out of stealth." not in llm.user


def test_an_excluded_post_stays_in_the_corpus(session):
    post = add_post(session, "launch", 500)
    post.excluded_from_extraction = True
    session.add(post)
    session.flush()

    assert session.get(Post, post.id) is not None


def test_an_empty_corpus_produces_no_proposals_and_does_not_call_the_model(session):
    llm = FakeLLM(TWO_HOOKS)

    assert propose_hooks(session, llm) == []
    assert llm.user is None


def test_a_response_without_hooks_is_an_error(session):
    add_post(session, "win-1", 185)

    with pytest.raises(ExtractionError):
        propose_hooks(session, FakeLLM({"something_else": []}))


def test_a_proposal_missing_its_pattern_is_rejected(session):
    add_post(session, "win-1", 185)

    with pytest.raises(ExtractionError):
        propose_hooks(session, FakeLLM({"hooks": [{"name": "nameless"}]}))
