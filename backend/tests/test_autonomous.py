from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, func, select

from app.autonomous import AutonomousRunFailed, SpendMeter, propose_topics, run_autonomous
from app.config import settings
from app.db import get_session
from app.deps import get_html_renderer, get_llm
from app.main import app
from app.models.draft import Draft
from app.models.post import Post, Verdict
from app.models.template import TemplateKind
from app.templates import approve, create_template

# The body a written post comes back with. Long enough and plain enough to clear the
# deterministic gates, because a run whose every draft failed the gates would make every
# assertion below about `created` an assertion about a failure instead. Borrowed in shape from
# `test_studio_workflow.BODY`, which is where the gates themselves are exercised.
BODY = (
    "Most teams add detail when a decision feels unclear. The better move is often deletion. "
    "Remove the sentence that changes no choice, then ask whether the next action is obvious."
)

# Six prompts, and each is answered off a distinctive opening of its own system text rather
# than off call order. The run makes five model calls per topic now, and a queue would have to
# be rebuilt every time a topic count changed — and would answer a rubric call with an angle
# the day the order shifted, which is a green test asserting nothing.
_ANSWERS: list[tuple[str, str]] = [
    ("you propose fresh post topics", "topics"),
    ("you choose which templates", "suggest"),
    ("you turn a post idea into an editorial brief", "brief"),
    ("you plan the argument of one post", "angle"),
    ("you review one draft linkedin post", "rubric"),
]


class FakeLLM:
    """Answers every step of the reviewed workflow the autonomous run now goes through.

    It used to answer two prompts, because the run called `generate_draft` — one topic call
    and one write per topic. The run generates through `generate_reviewed_draft` now, so a
    stub that answered anything else with the written post would hand a *post* back to the
    brief step and the workflow would fail at planning, quietly, and every test here would be
    measuring a failed run.
    """

    def __init__(self, topics: dict | None = None, written: dict | None = None):
        self.topics = topics if topics is not None else {"topics": [{"idea": "topic one"}]}
        self.written = written or {
            "hook": "A hook.",
            "body": BODY,
            "visual_values": {"big_number": "40"},
        }
        self.calls: list[str] = []

    def _kind(self, system: str) -> str:
        opening = system.lower()
        for marker, kind in _ANSWERS:
            if opening.startswith(marker):
                return kind
        # Both write prompts — `draft.write` 1.0.0 and 2.0.0 — and nothing else reaches here.
        return "write"

    def complete_json(self, system: str, user: str, images=()) -> dict:
        self.calls.append(user)
        kind = self._kind(system)
        if kind == "topics":
            return self.topics
        if kind == "suggest":
            return {"hook": "h", "structure": "s", "visual": "v", "reason": "they fit"}
        if kind == "brief":
            return {
                "objective": "Help operators explain one useful product decision.",
                "audience": "product operators responsible for checkout",
                "desired_action": "review one unnecessary checkout step",
                "constraints": [],
            }
        if kind == "angle":
            return {
                "thesis": "clearer product writing improves internal decisions",
                "tension": "teams confuse more detail with more clarity",
                "audience_stake": "operators spend less time resolving ambiguity",
                "claims": [{"text": "clearer product writing helps teams make decisions"}],
                "beats": ["name the ambiguity", "show the editing principle"],
                "cta": "remove one sentence that does not change the decision",
            }
        if kind == "rubric":
            # No deductions: the draft is ready. A run of failed-review drafts is asserted
            # deliberately in the tests that want one, never as everything's background.
            return {"deductions": []}
        return self.written


# How many model calls one topic buys, end to end: suggest, brief, angle, write, rubric.
# Named rather than written as a literal at each assertion, because the number is a property
# of the workflow and every test that hardcoded it would have to be found again when it moves.
CALLS_PER_TOPIC = 5


class FakeSearch:
    """A search adapter the run now carries. Never reached by the topics below.

    `resolve_mode` puts an idea with no named subject at `none`, and `none` never reaches a
    provider — so a call here would mean the research floor moved, which is worth failing on
    rather than absorbing.
    """

    def search(self, query: str, *, limit: int):
        raise AssertionError(f"an autonomous topic reached a web search: {query!r}")


class FakeRenderer:
    def screenshot(self, html: str, width: int, height: int) -> bytes:
        return b"IMG"


class FakeImageRenderer:
    """The image renderer a run now also carries, because it names no templates: every visual
    it draws is one the model suggested, so it cannot be known in advance whether an `ai`
    template is coming. `library()` below builds only `html` visuals, so `GEN` arriving on a
    draft here would mean the renderer was chosen from something other than the template."""

    def generate(self, prompt: str, width: int, height: int) -> bytes:
        return b"GEN"


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

    result = run_autonomous(
        session, FakeLLM(), FakeSearch(), FakeRenderer(), FakeImageRenderer(), cap=1
    )

    assert result.created == 1
    assert len(drafts(session)) == 1


def test_autonomous_drafts_are_marked_as_such(session):
    library(session)
    add_post(session, "p1")

    run_autonomous(session, FakeLLM(), FakeSearch(), FakeRenderer(), FakeImageRenderer(), cap=1)

    assert drafts(session)[0].mode == "autonomous"


def test_an_autonomous_draft_carries_full_lineage(session):
    hook, structure, visual = library(session)
    add_post(session, "p1")

    run_autonomous(session, FakeLLM(), FakeSearch(), FakeRenderer(), FakeImageRenderer(), cap=1)

    draft = drafts(session)[0]
    assert draft.hook_family == hook.family_id
    assert draft.structure_family == structure.family_id
    assert draft.visual_family == visual.family_id


def test_the_per_run_cap_is_enforced(session):
    """A scheduling fault must not be able to flood the queue."""
    library(session)
    add_post(session, "p1")

    run_autonomous(
        session,
        FakeLLM(topics=THREE_TOPICS),
        FakeSearch(),
        FakeRenderer(),
        FakeImageRenderer(),
        cap=2,
    )

    assert len(drafts(session)) == 2


def test_a_cap_of_zero_produces_nothing(session):
    library(session)
    add_post(session, "p1")

    result = run_autonomous(
        session,
        FakeLLM(topics=THREE_TOPICS),
        FakeSearch(),
        FakeRenderer(),
        FakeImageRenderer(),
        cap=0,
    )

    assert result.created == 0
    assert drafts(session) == []


def test_a_run_never_pushes_anything_to_zernio(session):
    """Reaching the outside world stays a human act, even for a draft."""
    library(session)
    add_post(session, "p1")

    run_autonomous(session, FakeLLM(), FakeSearch(), FakeRenderer(), FakeImageRenderer(), cap=1)

    assert drafts(session)[0].zernio_post_id is None
    assert drafts(session)[0].pushed_at is None


def test_topics_are_drawn_from_the_corpus(session):
    library(session)
    add_post(session, "p1", engaged=185)
    llm = FakeLLM()

    propose_topics(session, llm, count=2)

    assert "post p1" in llm.calls[-1]


# --- US-010: the lessons reach the step that chooses the subject --------------------------

# The topic prompt exactly as it was built before lessons existed, for a corpus of one post.
# Captured from the code at the commit before this slice, not typed from the format by hand.
# It is here so "no verdicts changes nothing" is a byte comparison rather than a claim: a
# stray newline, or a lessons header that leaks in when the list is empty, fails this and
# nothing else would. There are zero verdicts in the live database, so that empty header
# would otherwise be in every topic prompt this app ever sends.
TOPIC_PROMPT_BEFORE_LESSONS = "Propose 2 topics.\n\nRecent posts:\npost p1"


def rule(session, zid: str, note: str, content: str = "") -> Post:
    """Record a human's ruling the way the verdict route does, without going through it.

    `content` defaults to empty so a judged post can teach without also entering the recent
    posts shown — which keeps the byte comparisons above pinned to one known corpus.
    """
    post = Post(
        zernio_id=zid,
        platform="linkedin",
        content=content,
        verdict=Verdict.WORKED,
        verdict_note=note,
        verdict_at=datetime.now(UTC),
    )
    session.add(post)
    session.flush()
    return post


def test_a_recorded_verdict_reaches_the_topic_prompt_as_a_lesson(session):
    """What to write about is the decision the feedback loop most ought to inform.

    Lessons already reached `generate_draft` and `regenerate_text` — how to write it — and
    stopped there, so a human ruling could not steer the subject at all.
    """
    add_post(session, "p1")
    rule(session, "judged-1", "the opening number did the work; the ask fell flat")
    llm = FakeLLM()

    propose_topics(session, llm, count=2)

    assert "the opening number did the work; the ask fell flat" in llm.calls[-1]
    assert "worked" in llm.calls[-1]


def test_with_no_verdicts_recorded_the_topic_prompt_is_byte_identical_to_before(session):
    add_post(session, "p1")
    llm = FakeLLM()

    propose_topics(session, llm, count=2)

    assert llm.calls[-1] == TOPIC_PROMPT_BEFORE_LESSONS


def test_a_cleared_verdict_stops_teaching_the_topic_prompt(session):
    """Clearing a ruling empties its note, and an empty note is not a lesson.

    Asserted here as well as at the writing prompts because a retraction that still steers
    the subject is a retraction that did not take.
    """
    add_post(session, "p1")
    rule(session, "judged-1", "")
    llm = FakeLLM()

    propose_topics(session, llm, count=2)

    assert llm.calls[-1] == TOPIC_PROMPT_BEFORE_LESSONS


def test_a_run_with_no_topics_is_reported_not_silently_empty(session):
    library(session)
    add_post(session, "p1")
    notifier = RecordingNotifier()

    result = run_autonomous(
        session,
        FakeLLM(topics={"topics": []}),
        FakeSearch(),
        FakeRenderer(),
        FakeImageRenderer(),
        cap=2,
        notify=notifier,
    )

    assert result.created == 0
    assert any("no topics" in m.lower() for m in notifier.messages)


def test_a_failing_run_notifies_rather_than_failing_silently(session):
    class BrokenLLM:
        def complete_json(self, system: str, user: str, images=()) -> dict:
            raise RuntimeError("model unavailable")

    library(session)
    add_post(session, "p1")
    notifier = RecordingNotifier()

    with pytest.raises(AutonomousRunFailed):
        run_autonomous(
            session,
            BrokenLLM(),
            FakeSearch(),
            FakeRenderer(),
            FakeImageRenderer(),
            cap=1,
            notify=notifier,
        )

    assert any("model unavailable" in m for m in notifier.messages)


def test_one_bad_topic_does_not_abandon_the_rest_of_the_run(session):
    """A single unwritable topic should cost that topic, not the whole run.

    **The failed topic is a row now, and that is the change rather than a regression.**
    `generate_reviewed_draft` records a planning failure on the draft and returns instead of
    raising, so a run of two topics where one fails leaves two rows: one `ready` and one
    `failed`, carrying the reason. A failed attempt that leaves nothing behind is not
    auditable, and the queue it used to inflate is filtered by stage now — see
    `main.inbox`.
    """
    calls = {"n": 0}

    class FlakyLLM(FakeLLM):
        def complete_json(self, system: str, user: str, images=()) -> dict:
            if "choose which templates" not in system.lower() and "propose" not in system.lower():
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("transient")
            return super().complete_json(system, user, images)

    library(session)
    add_post(session, "p1")
    notifier = RecordingNotifier()

    result = run_autonomous(
        session,
        FlakyLLM(topics=THREE_TOPICS),
        FakeSearch(),
        FakeRenderer(),
        FakeImageRenderer(),
        cap=2,
        notify=notifier,
    )

    assert result.created == 1
    assert result.failed == 1
    # Two rows for two topics: the failure is recorded, not swallowed.
    assert session.exec(select(func.count()).select_from(Draft)).one() == 2
    stages = sorted(d.generation_stage for d in drafts(session))
    assert stages == ["failed", "ready"]


def test_a_run_reports_what_it_did(session):
    library(session)
    add_post(session, "p1")
    notifier = RecordingNotifier()

    run_autonomous(
        session,
        FakeLLM(topics=THREE_TOPICS),
        FakeSearch(),
        FakeRenderer(),
        FakeImageRenderer(),
        cap=2,
        notify=notifier,
    )

    assert any("2" in m for m in notifier.messages)


class BrokenRenderer:
    """A renderer that cannot produce the picture.

    Stands in for what `run_autonomous` really meets: it passes no `visual_id`, so
    `suggest_templates` LLM-picks from every APPROVED visual — including `stat-hero` v2, whose
    two `image_url` slots nothing fills unless the template carries defaults. Either way the
    failure lands inside `_draw_visual`, which records it on the draft and keeps the words.
    """

    def screenshot(self, html: str, width: int, height: int) -> bytes:
        raise RuntimeError("rendering service unavailable")


def test_a_draft_whose_visual_failed_is_not_reported_as_a_clean_success(session):
    """The silence this closes: `_draw_visual` swallows the failure into `visual_error` and
    `result.created += 1` fires anyway, so the run used to report "1 draft(s) created" with no
    signal that the picture never came out.

    The tolerance is correct and stays — a draft whose words are good is worth keeping — so it
    is still created and still counted. What changes is that the run says so.
    """
    library(session)
    add_post(session, "p1")
    notifier = RecordingNotifier()

    result = run_autonomous(
        session,
        FakeLLM(),
        FakeSearch(),
        BrokenRenderer(),
        FakeImageRenderer(),
        cap=1,
        notify=notifier,
    )

    assert result.created == 1
    assert result.failed == 0
    assert result.visuals_failed == 1
    assert drafts(session)[0].visual_error is not None
    assert drafts(session)[0].hook_text == "A hook."


def test_the_run_summary_names_the_missing_visuals(session):
    """Every entry point reads the summary and not the drafts — the notifier, the scheduler's
    log line, the endpoint's response. A reader who sees only the last message must still be
    told."""
    library(session)
    add_post(session, "p1")
    notifier = RecordingNotifier()

    run_autonomous(
        session,
        FakeLLM(topics=THREE_TOPICS),
        FakeSearch(),
        BrokenRenderer(),
        FakeImageRenderer(),
        cap=2,
        notify=notifier,
    )

    assert "no visual" in notifier.messages[-1]
    assert "2" in notifier.messages[-1]
    # And named per draft as well, with the reason: the count says a redraw is needed, the
    # message says whether one could possibly help.
    assert any("rendering service unavailable" in m for m in notifier.messages)


def test_a_clean_run_says_nothing_about_visuals(session):
    """The other direction. A summary that mentioned visuals unconditionally would let a
    broken count pass, and would train whoever reads it to skip the clause."""
    library(session)
    add_post(session, "p1")
    notifier = RecordingNotifier()

    result = run_autonomous(
        session,
        FakeLLM(),
        FakeSearch(),
        FakeRenderer(),
        FakeImageRenderer(),
        cap=1,
        notify=notifier,
    )

    assert result.visuals_failed == 0
    assert "visual" not in notifier.messages[-1]


def test_a_run_refuses_when_the_library_is_not_ready(session):
    add_post(session, "p1")
    notifier = RecordingNotifier()

    with pytest.raises(AutonomousRunFailed):
        run_autonomous(
            session,
            FakeLLM(),
            FakeSearch(),
            FakeRenderer(),
            FakeImageRenderer(),
            cap=1,
            notify=notifier,
        )

    assert notifier.messages


# --- the endpoint's cap is bounded by the setting, not merely defaulted from it ------------


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_the_endpoint_cannot_ask_for_more_drafts_than_the_setting_allows(
    client, session, monkeypatch
):
    """`cap` arrives from a query string. It used to be used verbatim.

    `settings.autonomous_max_drafts` was only the default, so `?cap=500` ran 500 topics: one
    chat call for topics plus two per topic, ~1001 billed completions against no spend counter
    anywhere in this app, and 500 renders inside one synchronous request. The setting is the
    ceiling now. Asserted against the setting rather than a literal, and pinned to 1 so three
    available topics can prove the bound rather than the topic supply doing it.
    """
    monkeypatch.setattr(settings, "autonomous_max_drafts", 1)
    library(session)
    add_post(session, "p1")
    app.dependency_overrides[get_llm] = lambda: FakeLLM(topics=THREE_TOPICS)
    app.dependency_overrides[get_html_renderer] = FakeRenderer

    body = client.post("/drafts/autonomous-run?cap=500").json()

    assert body["created"] == settings.autonomous_max_drafts
    assert len(drafts(session)) == settings.autonomous_max_drafts


def test_a_cap_below_the_ceiling_is_still_honoured(client, session, monkeypatch):
    """The clamp is a ceiling, not a floor — asking for less must still mean less."""
    monkeypatch.setattr(settings, "autonomous_max_drafts", 3)
    library(session)
    add_post(session, "p1")
    app.dependency_overrides[get_llm] = lambda: FakeLLM(topics=THREE_TOPICS)
    app.dependency_overrides[get_html_renderer] = FakeRenderer

    body = client.post("/drafts/autonomous-run?cap=1").json()

    assert body["created"] == 1
    assert len(drafts(session)) == 1


# --- US-011: a paid run says what it cost --------------------------------------------------


class CountingRenderer(FakeRenderer):
    """A renderer keeping its own tally, independent of anything the app counts.

    The point of the tally is that the assertion compares two numbers arrived at
    separately — what the endpoint reports against what the adapter was actually asked to
    do — rather than comparing the app's arithmetic with itself.
    """

    def __init__(self) -> None:
        self.calls = 0

    def screenshot(self, html: str, width: int, height: int) -> bytes:
        self.calls += 1
        return super().screenshot(html, width, height)


def test_a_run_reports_the_paid_calls_it_actually_made(client, session):
    """There was no spend counter anywhere in this app. `?cap=500` bought up to 1001 billed
    completions before the clamp landed and the response said nothing about any of it.

    Asserted against each fake's own tally, not against a literal derived from the cap: the
    number has to come from calls that happened, since the case it exists for is the run
    that does not reach the end.
    """
    library(session)
    add_post(session, "p1")
    llm, renderer = FakeLLM(), CountingRenderer()
    app.dependency_overrides[get_llm] = lambda: llm
    app.dependency_overrides[get_html_renderer] = lambda: renderer

    body = client.post("/drafts/autonomous-run?cap=1").json()

    # One topic call, then a whole workflow for the single topic — suggest, brief, angle,
    # write and rubric. Read off `CALLS_PER_TOPIC` rather than a literal, because the number
    # is a property of the workflow rather than of this test.
    assert body["llm_calls"] == len(llm.calls) == 1 + CALLS_PER_TOPIC
    assert body["image_calls"] == renderer.calls == 1
    # Zero, honestly: `none` mode never reaches a provider. Reported rather than omitted now
    # that a research floor above `none` would make a run buy real searches.
    assert body["search_calls"] == 0


def test_the_count_follows_the_calls_made_and_not_the_drafts_produced(client, session):
    """The discriminator between observed and predicted.

    One topic fails at its *brief* — the first workflow call after the templates are chosen —
    so it buys two completions and produces nothing publishable. A count derived from
    `created` would report one topic's worth; only counting the calls reports what was bought.
    """
    calls = {"n": 0}

    class FlakyLLM(FakeLLM):
        def complete_json(self, system: str, user: str, images=()) -> dict:
            if "choose which templates" not in system.lower() and "propose" not in system.lower():
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("transient")
            return super().complete_json(system, user, images)

    library(session)
    add_post(session, "p1")
    renderer = CountingRenderer()
    app.dependency_overrides[get_llm] = lambda: FlakyLLM(topics=THREE_TOPICS)
    app.dependency_overrides[get_html_renderer] = lambda: renderer

    body = client.post("/drafts/autonomous-run?cap=2").json()

    assert body["created"] == 1
    assert body["failed"] == 1
    # Topics, then the failed topic's suggest and brief, then the good topic's whole workflow.
    assert body["llm_calls"] == 1 + 2 + CALLS_PER_TOPIC
    # One render: the failed topic never reached `_draw_visual`.
    assert body["image_calls"] == renderer.calls == 1


def test_a_render_that_produced_nothing_is_still_counted_as_spent(client, session):
    """A failed render is a call that was made. `_draw_visual` swallows the exception into
    `visual_error`, so nothing downstream of it knows the renderer was ever reached —
    which is exactly the reading that would make a run look cheaper than it was."""

    library(session)
    add_post(session, "p1")
    app.dependency_overrides[get_llm] = lambda: FakeLLM()
    app.dependency_overrides[get_html_renderer] = BrokenRenderer

    body = client.post("/drafts/autonomous-run?cap=1").json()

    assert body["created"] == 1
    assert body["visuals_failed"] == 1
    assert body["image_calls"] == 1


def test_metering_does_not_change_which_renderer_a_template_can_reach():
    """`render_visual` dispatches on `hasattr(renderer, "screenshot" / "generate")`.

    A meter carrying both methods outright would make an HTML-only renderer answer yes to
    `generate`, sending every `ai` template down the Azure branch of a renderer that has no
    Azure client — with nothing else in this suite failing, since `library()` only builds an
    html visual. The `generate` half is asserted here too because it is the only place it can
    be: the endpoint is injected an HTML renderer and can never reach the image path.
    """

    class ImageOnlyRenderer:
        def generate(self, prompt: str, width: int, height: int) -> bytes:
            return b"IMG"

    meter = SpendMeter()
    html, image = meter.watch(FakeRenderer()), meter.watch(ImageOnlyRenderer())

    assert hasattr(html, "screenshot") and not hasattr(html, "generate")
    assert hasattr(image, "generate") and not hasattr(image, "screenshot")

    image.generate("a prompt", 1080, 1350)
    html.screenshot("<b>x</b>", 1080, 1350)

    assert meter.spend() == {"llm_calls": 0, "image_calls": 2, "search_calls": 0}


def test_a_run_that_could_not_start_still_reports_what_it_spent(client, session):
    """`AutonomousRunFailed` is a 502, and the run had already paid for the topic call.

    This is the case the whole slice is for: the response body is the only thing a caller
    sees, and a failure that reports no spend is how a retry loop bills unbounded while
    every response says nothing happened.
    """

    class BrokenLLM(FakeLLM):
        def complete_json(self, system: str, user: str, images=()) -> dict:
            raise RuntimeError("model unavailable")

    library(session)
    add_post(session, "p1")
    app.dependency_overrides[get_llm] = BrokenLLM
    app.dependency_overrides[get_html_renderer] = FakeRenderer

    response = client.post("/drafts/autonomous-run?cap=2")

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["llm_calls"] == 1
    assert detail["image_calls"] == 0
    assert detail["search_calls"] == 0
    assert "model unavailable" in detail["error"]
