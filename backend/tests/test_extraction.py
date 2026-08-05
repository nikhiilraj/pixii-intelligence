from datetime import datetime

import pytest
from sqlmodel import select

from app.config import settings
from app.extraction import Cohort, ExtractionError, propose_hooks
from app.models.generation_trace import GenerationTrace
from app.models.post import Post
from app.models.template import TemplateKind, TemplateStatus
from app.templates import latest_versions, usable_templates


class FakeLLM:
    """Records the prompt it was given and replays a canned response."""

    def __init__(self, response: dict):
        self.response = response
        self.system: str | None = None
        self.user: str | None = None

    def complete_json(self, system: str, user: str, images=()) -> dict:
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


# Comfortably after `settings.voice_since`, so a post is current unless a test says so.
CURRENT = datetime(2026, 6, 1)

# The account other creators' posts are collected under.
INSPIRATION = settings.inspiration_account


def add_post(
    session,
    zid: str,
    engaged: int,
    content: str = "A hook line.\n\nBody text.",
    author: str | None = None,
    published_at: datetime | None = CURRENT,
) -> Post:
    post = Post(
        zernio_id=zid,
        platform="linkedin",
        content=content,
        engaged_actions=engaged,
        impressions=engaged * 30,
        account_username=author or settings.voice_account,
        published_at=published_at,
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


def test_the_inspiration_cohort_reads_creator_posts_instead_of_montes(session):
    # A hook is a borrowable shape, so a creator's posts may teach one. The cohorts are
    # never mixed: whichever ranked higher would otherwise lead the sample.
    add_post(session, "theirs", 1240, content="Someone else's hook.", author=INSPIRATION)
    add_post(session, "ours", 185, content="Monte's own hook.")

    llm = FakeLLM(TWO_HOOKS)
    propose_hooks(session, llm, cohort=Cohort.INSPIRATION)

    assert "Someone else's hook." in llm.user
    assert "Monte's own hook." not in llm.user


def test_the_cohort_is_matched_by_value_so_a_plain_string_still_routes_correctly(session):
    """`Cohort` is a StrEnum, so `"voice" == Cohort.VOICE` while `"voice" is Cohort.VOICE`
    is False. An identity check would send any caller passing the bare string — a value
    that compares equal everywhere else — silently down the *inspiration* branch, and
    Monte's voice templates would quietly be built from other people's writing.
    """
    add_post(session, "theirs", 1240, content="Someone else's hook.", author=INSPIRATION)
    add_post(session, "ours", 185, content="Monte's own hook.")

    llm = FakeLLM(TWO_HOOKS)
    propose_hooks(session, llm, cohort="voice")  # type: ignore[arg-type]

    assert "Monte's own hook." in llm.user
    assert "Someone else's hook." not in llm.user


def test_the_voice_era_floor_does_not_gate_inspiration_posts(session):
    # `voice_since` marks where Monte's current era begins. A creator's posts are never
    # ranked against his, and his era says nothing about their timeline.
    add_post(
        session,
        "theirs",
        900,
        content="An older creator hook.",
        author=INSPIRATION,
        published_at=datetime(2024, 6, 1),
    )

    llm = FakeLLM(TWO_HOOKS)
    propose_hooks(session, llm, cohort=Cohort.INSPIRATION)

    assert "An older creator hook." in llm.user


def test_a_textless_inspiration_post_is_not_evidence(session):
    # At least one collected creator post is an image with no text at all, and it outranks
    # the rest, so it would otherwise lead the sample carrying no hook.
    add_post(session, "image-only", 2000, content="   ", author=INSPIRATION)
    add_post(session, "theirs", 185, content="Someone else's hook.", author=INSPIRATION)

    llm = FakeLLM(TWO_HOOKS)
    propose_hooks(session, llm, cohort=Cohort.INSPIRATION, sample_size=1)

    assert "Someone else's hook." in llm.user
    assert "image-only" not in llm.user


def test_an_excluded_inspiration_post_is_not_evidence(session):
    held_out = add_post(session, "viral", 5000, content="A one-off.", author=INSPIRATION)
    held_out.excluded_from_extraction = True
    session.add(held_out)
    session.flush()
    add_post(session, "theirs", 185, content="Someone else's hook.", author=INSPIRATION)

    llm = FakeLLM(TWO_HOOKS)
    propose_hooks(session, llm, cohort=Cohort.INSPIRATION)

    assert "Someone else's hook." in llm.user
    assert "A one-off." not in llm.user


def test_a_hook_records_the_cohort_it_was_extracted_from(session):
    # "Borrowed from a creator" has to stay distinguishable from "proven in Monte's own
    # posts" long after the extraction run.
    add_post(session, "ours", 185)
    add_post(session, "theirs", 185, author=INSPIRATION)

    voice = propose_hooks(session, FakeLLM(TWO_HOOKS))
    borrowed = propose_hooks(session, FakeLLM(TWO_HOOKS), cohort=Cohort.INSPIRATION)

    assert {t.body["cohort"] for t in voice} == {"voice"}
    assert {t.body["cohort"] for t in borrowed} == {"inspiration"}


def test_extraction_reads_the_voice_cohort_unless_told_otherwise(session):
    add_post(session, "ours", 185, content="Monte's own hook.")
    add_post(session, "theirs", 1240, content="Someone else's hook.", author=INSPIRATION)

    llm = FakeLLM(TWO_HOOKS)
    proposals = propose_hooks(session, llm)

    assert "Monte's own hook." in llm.user
    assert "Someone else's hook." not in llm.user
    assert all(t.body["cohort"] == "voice" for t in proposals)


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


def test_a_post_from_before_the_voice_era_is_not_evidence(session):
    # The corpus reaches back further than the voice does. The old posts are a different
    # genre — AI-industry commentary rather than the Amazon-listing work Pixii publishes
    # now — so templates drawn from them would describe a voice the company has left.
    stale = datetime(2024, 6, 1)
    add_post(session, "old", 900, content="Sora just changed everything.", published_at=stale)
    add_post(session, "win-1", 185, content="A repeatable hook.")

    llm = FakeLLM(TWO_HOOKS)
    propose_hooks(session, llm)

    assert "A repeatable hook." in llm.user
    assert "Sora just changed everything." not in llm.user


def test_an_undated_post_is_not_evidence(session):
    # Nothing shows an undated post is current, and the whole point of the floor is
    # currency, so it cannot be allowed to pass by default.
    add_post(session, "undated", 900, content="No date on this one.", published_at=None)
    add_post(session, "win-1", 185, content="A repeatable hook.")

    llm = FakeLLM(TWO_HOOKS)
    propose_hooks(session, llm)

    assert "A repeatable hook." in llm.user
    assert "No date on this one." not in llm.user


def test_a_pre_era_post_does_not_consume_a_sample_slot(session):
    # It outranks everything, so it is drawn first and would otherwise cost a current post
    # its place in the sample.
    stale = datetime(2024, 6, 1)
    add_post(session, "old", 500, content="Sora just changed everything.", published_at=stale)
    for index in range(3):
        add_post(session, f"real{index}", engaged=index, content=f"Hook number {index}.")

    llm = FakeLLM(TWO_HOOKS)
    propose_hooks(session, llm, sample_size=3)

    assert [f"Hook number {i}." in llm.user for i in range(3)] == [True, True, True]


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


# --- the calls extraction makes are recorded ------------------------------------------------


def test_the_hook_proposal_writes_a_trace_naming_the_registered_prompt(session):
    """Extraction was the last stage whose model calls left no record at all.

    It matters more here than the name "audit trail" suggests: a human approves what this call
    proposes, and every draft generated afterwards is generated *from* that template. "Which
    prompt, at which version, proposed this" is therefore a lineage question — the same kind
    `(family_id, version)` answers one layer down — and it had no answer.
    """
    add_post(session, "win-1", 185)

    propose_hooks(session, FakeLLM(TWO_HOOKS))
    session.flush()

    (trace,) = session.exec(select(GenerationTrace)).all()
    assert (trace.prompt_name, trace.prompt_version) == ("extraction.hooks", "1.0.0")
    assert trace.input_artifact_ids["posts"] == "win-1"
    assert trace.input_artifact_ids["cohort"] == "voice"
    assert trace.output_hash is not None and trace.error is None
    # Never claimed, because nothing at this layer has seen a token count.
    assert (trace.prompt_tokens, trace.completion_tokens) == (None, None)


def test_a_failed_extraction_call_is_recorded_with_its_error(session):
    class Broken:
        def complete_json(self, system, user, images=()):
            raise RuntimeError("vision endpoint unavailable")

    add_post(session, "win-1", 185)

    with pytest.raises(RuntimeError):
        propose_hooks(session, Broken())
    session.flush()

    (trace,) = session.exec(select(GenerationTrace)).all()
    assert trace.error == "RuntimeError: vision endpoint unavailable"
    assert trace.output_hash is None
    assert trace.latency_ms is not None
