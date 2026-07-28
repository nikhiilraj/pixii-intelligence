import pytest
from sqlmodel import func, select

from app.autonomous import AutonomousRunFailed, propose_topics, run_autonomous
from app.models.draft import Draft
from app.models.post import Post
from app.models.template import TemplateKind
from app.templates import approve, create_template


class FakeLLM:
    """Returns topics on the first call, then written posts thereafter."""

    def __init__(self, topics: dict | None = None, written: dict | None = None):
        self.topics = topics if topics is not None else {"topics": [{"idea": "topic one"}]}
        self.written = written or {
            "hook": "A hook.",
            "body": "A body.",
            "visual_values": {"big_number": "40"},
        }
        self.calls: list[str] = []

    def complete_json(self, system: str, user: str) -> dict:
        self.calls.append(user)
        if "topics" in system.lower() and "propose" in system.lower():
            return self.topics
        if "choose which templates" in system.lower():
            return {"hook": "h", "structure": "s", "visual": "v"}
        return self.written


class FakeRenderer:
    def screenshot(self, html: str, width: int, height: int) -> bytes:
        return b"IMG"


class RecordingNotifier:
    def __init__(self):
        self.messages: list[str] = []

    def __call__(self, message: str) -> None:
        self.messages.append(message)


def library(session):
    hook = create_template(
        session, kind=TemplateKind.HOOK, name="h", body={"pattern": "{a}"}, provenance=["p1"]
    )
    structure = create_template(
        session, kind=TemplateKind.STRUCTURE, name="s", body={"sections": []}
    )
    visual = create_template(
        session,
        kind=TemplateKind.VISUAL,
        name="v",
        body={"renderer": "html", "html": "<b>{big_number}</b>"},
        slots=[{"name": "big_number", "type": "text"}],
    )
    for template in (hook, structure, visual):
        approve(session, template)
    return hook, structure, visual


def add_post(session, zid: str, engaged: int = 10):
    session.add(
        Post(zernio_id=zid, platform="linkedin", content=f"post {zid}", engaged_actions=engaged)
    )
    session.flush()


def drafts(session) -> list[Draft]:
    return list(session.exec(select(Draft)).all())


THREE_TOPICS = {"topics": [{"idea": "one"}, {"idea": "two"}, {"idea": "three"}]}


def test_a_run_produces_drafts_with_no_operator_involvement(session):
    library(session)
    add_post(session, "p1")

    result = run_autonomous(session, FakeLLM(), FakeRenderer(), cap=1)

    assert result.created == 1
    assert len(drafts(session)) == 1


def test_autonomous_drafts_are_marked_as_such(session):
    library(session)
    add_post(session, "p1")

    run_autonomous(session, FakeLLM(), FakeRenderer(), cap=1)

    assert drafts(session)[0].mode == "autonomous"


def test_an_autonomous_draft_carries_full_lineage(session):
    hook, structure, visual = library(session)
    add_post(session, "p1")

    run_autonomous(session, FakeLLM(), FakeRenderer(), cap=1)

    draft = drafts(session)[0]
    assert draft.hook_family == hook.family_id
    assert draft.structure_family == structure.family_id
    assert draft.visual_family == visual.family_id


def test_the_per_run_cap_is_enforced(session):
    """A scheduling fault must not be able to flood the queue."""
    library(session)
    add_post(session, "p1")

    run_autonomous(session, FakeLLM(topics=THREE_TOPICS), FakeRenderer(), cap=2)

    assert len(drafts(session)) == 2


def test_a_cap_of_zero_produces_nothing(session):
    library(session)
    add_post(session, "p1")

    result = run_autonomous(session, FakeLLM(topics=THREE_TOPICS), FakeRenderer(), cap=0)

    assert result.created == 0
    assert drafts(session) == []


def test_a_run_never_pushes_anything_to_zernio(session):
    """Reaching the outside world stays a human act, even for a draft."""
    library(session)
    add_post(session, "p1")

    run_autonomous(session, FakeLLM(), FakeRenderer(), cap=1)

    assert drafts(session)[0].zernio_post_id is None
    assert drafts(session)[0].pushed_at is None


def test_topics_are_drawn_from_the_corpus(session):
    library(session)
    add_post(session, "p1", engaged=185)
    llm = FakeLLM()

    propose_topics(session, llm, count=2)

    assert "post p1" in llm.calls[-1]


def test_a_run_with_no_topics_is_reported_not_silently_empty(session):
    library(session)
    add_post(session, "p1")
    notifier = RecordingNotifier()

    result = run_autonomous(
        session, FakeLLM(topics={"topics": []}), FakeRenderer(), cap=2, notify=notifier
    )

    assert result.created == 0
    assert any("no topics" in m.lower() for m in notifier.messages)


def test_a_failing_run_notifies_rather_than_failing_silently(session):
    class BrokenLLM:
        def complete_json(self, system: str, user: str) -> dict:
            raise RuntimeError("model unavailable")

    library(session)
    add_post(session, "p1")
    notifier = RecordingNotifier()

    with pytest.raises(AutonomousRunFailed):
        run_autonomous(session, BrokenLLM(), FakeRenderer(), cap=1, notify=notifier)

    assert any("model unavailable" in m for m in notifier.messages)


def test_one_bad_topic_does_not_abandon_the_rest_of_the_run(session):
    """A single unwritable topic should cost that topic, not the whole run."""
    calls = {"n": 0}

    class FlakyLLM(FakeLLM):
        def complete_json(self, system: str, user: str) -> dict:
            if "choose which templates" not in system.lower() and "propose" not in system.lower():
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("transient")
            return super().complete_json(system, user)

    library(session)
    add_post(session, "p1")
    notifier = RecordingNotifier()

    result = run_autonomous(
        session, FlakyLLM(topics=THREE_TOPICS), FakeRenderer(), cap=2, notify=notifier
    )

    assert result.created == 1
    assert result.failed == 1
    assert session.exec(select(func.count()).select_from(Draft)).one() == 1


def test_a_run_reports_what_it_did(session):
    library(session)
    add_post(session, "p1")
    notifier = RecordingNotifier()

    run_autonomous(session, FakeLLM(topics=THREE_TOPICS), FakeRenderer(), cap=2, notify=notifier)

    assert any("2" in m for m in notifier.messages)


def test_a_run_refuses_when_the_library_is_not_ready(session):
    add_post(session, "p1")
    notifier = RecordingNotifier()

    with pytest.raises(AutonomousRunFailed):
        run_autonomous(session, FakeLLM(), FakeRenderer(), cap=1, notify=notifier)

    assert notifier.messages
