import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar, cast

from sqlmodel import Session, col, select

from app import prompts
from app.generation import (
    NoUsableTemplates,
    generate_reviewed_draft,
    lesson_lines,
    verdict_lessons,
)
from app.llm import LLM
from app.models.post import Post
from app.models.stage import review_ready
from app.prompts.tracing import new_correlation_id, traced_call
from app.rendering import HtmlRenderer, ImageRenderer

log = logging.getLogger("pixii.autonomous")

# How many recent posts the model sees when looking for a fresh angle.
TOPIC_CONTEXT_POSTS = 15

# Pinned to an exact version at import, like `generation`'s two. There is no "latest" to ask
# for; `prompts.get` says why at length.
_TOPICS = prompts.get("topics.propose", "1.0.0")


class AutonomousRunFailed(RuntimeError):
    """The run could not proceed at all. Distinct from a run where some topics failed."""


@dataclass
class RunResult:
    """What a run actually did — including the part that used to be invisible.

    `visuals_failed` counts drafts that were created and have no picture.
    `generate_draft` swallows a render failure into `draft.visual_error` on purpose (the
    words survive an image that did not come out, and they are worth keeping), but
    `result.created += 1` fires either way, so before this field a run that produced three
    drafts with no images reported "3 draft(s) created" and nothing else. Every entry point
    reads the summary and not the drafts: the notifier, the scheduler's log line and the
    `POST /drafts/autonomous-run` response. Tolerating the failure was right; not saying so
    made "it works" and "it silently didn't" the same output.

    Counted separately from `failed` rather than folded into it. They are different events —
    `visuals_failed` is a reviewed draft in the queue awaiting a redraw — and
    `created + failed == topics` is an invariant a reader can check.

    **`created` counts drafts that passed review, not drafts that exist.** Once the run moved
    to `generate_reviewed_draft` the two stopped being the same number: that function records
    a failure on the draft and returns rather than raising, so every topic produces a row. A
    `failed_review` row is real, visible in Studio with its stage and its reason, and auditable
    — and it is not work waiting on a human, which is what `created` is read as everywhere it
    is reported. `failed` therefore covers both a topic that raised and a topic whose workflow
    concluded against it; the notifier names which, per topic, because the count cannot.
    """

    created: int = 0
    failed: int = 0
    visuals_failed: int = 0
    topics: int = 0


Notifier = Callable[[str], None]

_Adapter = TypeVar("_Adapter")


class SpendMeter:
    """Counts the paid calls made through the adapters it wraps.

    There was no spend counter anywhere in this app: `POST /drafts/autonomous-run` buys chat
    completions and renders and reported neither, which is how `?cap=500` bought up to 1001
    billed completions before the clamp landed with every response still saying nothing.

    **Owned by the caller, not by `run_autonomous`.** That is the whole design: the counter
    outlives the call that raises, so an `AutonomousRunFailed` still leaves the number in the
    hands of the handler that has to answer. A run that dies after three completions has
    spent them, and that is precisely when the number is worth having.

    ponytail: one object per request, nothing module-level. A process-wide counter would
    reset on restart and would attribute one request's spend to another under concurrency —
    a number that is wrong in exactly the situation it is consulted in is worse than none.
    """

    def __init__(self) -> None:
        self.llm_calls = 0
        self.image_calls = 0
        # Web searches, bought through `research.SearchProvider`. Read off the attribute, not
        # out of `spend()` — see there for why that omission is deliberate.
        self.search_calls = 0

    def watch(self, adapter: _Adapter) -> _Adapter:
        """The same adapter, counting. Typed as what it wraps because that is what it is."""
        return cast(_Adapter, _Metered(self, adapter))

    def spend(self) -> dict[str, int]:
        """What has been bought so far, in the shape both the run response and the 502 use.

        **`search_calls` is reported, and it used to be deliberately omitted.** The reason for
        the omission was that no draft endpoint could reach a search — `/drafts/retopic` and
        `/drafts/variants` wrapped an LLM and a renderer and never a search adapter — so the
        key could only ever have said zero, which is the structurally-always-zero field
        `_Metered`'s docstring rejects one field over.

        That reason is gone. Both routes run the reviewed workflow now, and a combination whose
        research floor is `light` or `deep` buys real web searches inside the request. Leaving
        the key out would make this dict understate what a batch of variants cost, which is the
        gap `SpendMeter` was built to close: there was no spend counter anywhere in this app,
        and a paid call a request makes and does not report is the same silence in a smaller
        place.

        `research_job.queries_run` is where a *job's* searches are recorded, and the two are
        not redundant: this counter is owned by the request and survives a rollback, and that
        row does not.
        """
        return {
            "llm_calls": self.llm_calls,
            "image_calls": self.image_calls,
            "search_calls": self.search_calls,
        }


class _Metered:
    """An adapter that counts what is asked of it, and delegates everything else.

    `complete_json` is a billed completion. `screenshot` and `generate` both count as
    `image_calls` — the field says how many images the run asked a renderer for, not which
    vendor billed for them; counting only Azure's `generate` would ship a number that is
    structurally always zero at the one endpoint that reports it, since `autonomous_run` is
    injected an HTML renderer and can never reach the image path. `search` is a paid web
    search through `research.SearchProvider`, counted here for the same reason as the rest:
    the number has to outlive a run that raised, and the row recording it does not.

    Delegation goes through `__getattr__` rather than declared methods on purpose:
    `rendering.render_visual` picks its path with `hasattr(renderer, "screenshot")`, so a
    wrapper carrying both methods outright would make an HTML-only renderer claim it can
    generate images and send every `ai` template down the wrong branch.
    """

    _COUNTED = {
        "complete_json": "llm_calls",
        "screenshot": "image_calls",
        "generate": "image_calls",
        "search": "search_calls",
    }

    def __init__(self, meter: SpendMeter, adapter: Any) -> None:
        self._meter = meter
        self._adapter = adapter

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._adapter, name)
        field = self._COUNTED.get(name)
        if field is None:
            return attr

        def counted(*args: Any, **kwargs: Any) -> Any:
            # Counted before the call and never after. A completion that comes back
            # unparseable was still billed, and a call that raises is the one whose cost
            # would otherwise vanish — which is the reading this whole slice exists to
            # close. The count is what was bought, not what was usable.
            setattr(self._meter, field, getattr(self._meter, field) + 1)
            return attr(*args, **kwargs)

        return counted


def _log_notify(message: str) -> None:
    log.warning("autonomous: %s", message)


def propose_topics(
    session: Session, llm: LLM, count: int, *, correlation_id: str | None = None
) -> list[dict]:
    """Ask for angles the corpus has not already covered.

    Grounded in what has actually been posted, so unattended output follows from the real
    account rather than from nothing — and in what a human ruled about what went out. The
    verdict lessons reached only `generate_draft` and `regenerate_text` before, which are
    the steps deciding *how* to write; the choice of subject, which a ruling speaks to most
    directly, saw nothing.
    """
    recent = session.exec(
        select(Post)
        .where(col(Post.content) != "")
        .order_by(col(Post.published_at).desc())
        .limit(TOPIC_CONTEXT_POSTS)
    ).all()

    shown = "\n---\n".join(post.content.strip()[:600] for post in recent)
    # Lessons above the posts, not below, for the reason `_write_prompt` gives: `shown` ends
    # in a raw post body, so anything appended after it reads as commentary on that post.
    # `lesson_lines` contributes nothing when there are no verdicts, which keeps this prompt
    # byte-identical to the one sent before any ruling existed.
    parts = [f"Propose {count} topics."]
    lessons = verdict_lessons(session)
    parts.extend(lesson_lines(lessons))
    parts.append(f"\nRecent posts:\n{shown}")
    result = traced_call(
        session,
        llm,
        _TOPICS,
        "\n".join(parts),
        correlation_id=correlation_id or new_correlation_id(),
        # The posts the proposal was grounded in, and how many rulings were carried with them.
        # The count rather than the notes: a lesson is a person's sentence about a post, and a
        # trace row is not where a human's writing should be duplicated. What it answers is
        # "did this run see the verdicts", which is the thing that was unanswerable.
        input_artifact_ids={
            "posts": ",".join(post.zernio_id for post in recent),
            "lessons": len(lessons),
            "requested": count,
        },
    )
    topics = result.get("topics")
    return [t for t in topics if t.get("idea")] if isinstance(topics, list) else []


def run_autonomous(
    session: Session,
    llm: LLM,
    search: Any,
    html_renderer: HtmlRenderer,
    image_renderer: ImageRenderer,
    *,
    cap: int,
    notify: Notifier = _log_notify,
) -> RunResult:
    """Produce drafts unattended, up to `cap`, through the same review the operator gets.

    **`generate_reviewed_draft`, not `generate_draft`**, and that is the change that matters
    here. An unattended run is the path with no human anywhere near it, so it is the last
    place that should have been writing drafts nothing reviewed — a brief, an angle, planned
    claims, the research floor, the deterministic gates and the readiness rubric all apply,
    and a topic that cannot satisfy them produces a row saying so rather than a draft that
    reads as finished.

    **A search adapter, because research is not optional for a factual topic.** The provider
    is the same one a request gets — `research.search_provider`, shared rather than selected
    again here — so an unattended run researches exactly as a directed one does. Where no key
    is configured that provider is `NoSearchProvider`, and `light`/`deep` topics stop at a
    visible failed research state instead of falling back to `none`. That is the honest
    outcome and it is meant to stay visible: a run that proposed five factual topics with no
    search configured should report five failures, not five opinion pieces written as though
    they had been checked.

    Both renderers, and that is a fix rather than plumbing. A run names no templates at all,
    so every visual it draws is one `suggest_templates` chose — and this was handed a single
    HTML renderer, which made an `ai` visual permanently undrawable here: `render_visual`
    raised `UnsupportedRenderer`, `_draw_visual` filed it under `visual_error`, and the run
    counted the draft in `visuals_failed` as though the image service had declined. See
    `generation._renderer_for`; the choice is made there, off the row actually used.

    Two deliberate limits on what this is allowed to do, both unchanged:

    - **It never reaches Zernio.** Drafts land in this system for review; pushing them out
      stays a human act, even though a Zernio draft would not itself publish. An unattended
      loop that writes to the live account is a different risk from one that does not. It is
      now doubly true: what this writes is not review-ready unless the workflow said so, so
      even the push boundary would refuse most of it.
    - **`cap` is hard.** A scheduling fault, a retry storm or a runaway loop cannot produce
      more than this many drafts in one run. Both callers bound it by
      `settings.autonomous_max_drafts` — the scheduler passes that setting and
      `POST /drafts/autonomous-run` clamps its query parameter to it — so no caller can ask
      for more than is configured either. The cap is now worth more per unit: one topic buys a
      whole workflow rather than one completion.

    A failure that prevents the run raises; a failure on one topic costs only that topic.
    Either way the notifier is told — a scheduled job that fails in silence is the exact
    failure this system exists to correct.
    """
    if cap <= 0:
        return RunResult()

    try:
        topics = propose_topics(session, llm, cap)
    except Exception as exc:
        notify(f"autonomous run could not propose topics: {exc}")
        raise AutonomousRunFailed(str(exc)) from exc

    if not topics:
        notify("autonomous run produced no topics; nothing generated")
        return RunResult()

    result = RunResult(topics=len(topics))
    for topic in topics[:cap]:
        idea = str(topic.get("idea", "")).strip()
        try:
            draft = generate_reviewed_draft(
                session, llm, search, html_renderer, image_renderer, idea=idea, mode="autonomous"
            )
            # **Counted by what the workflow concluded, not by whether a row was written.**
            # `generate_reviewed_draft` records its failures on the draft and returns rather
            # than raising, so `created += 1` unconditionally would count a `failed_review`
            # row as a draft ready for review — and `created` is what the daily card puts in
            # its title ("N draft(s) ready for review") and what the Inbox queue promises.
            # A failed attempt is still a row, visible in Studio with its stage and its
            # reason; what it is not is work waiting on a human.
            if review_ready(draft.generation_stage):
                result.created += 1
            else:
                result.failed += 1
                notify(
                    f"autonomous topic did not pass review ({idea[:60]}): "
                    f"{draft.generation_stage} — {draft.generation_error}"
                )
                # No visual check below: a run that failed before `_draw_visual` has no
                # picture because it never asked for one, which is not an image failure.
                continue
            if draft.visual_error:
                # Named per draft rather than only counted, because the count says a redraw
                # is needed and the message says whether a redraw could possibly help. An
                # `UnresolvableAsset` naming a missing default is a template to fix; a
                # `MissingSlotValue` is an image slot nobody has chosen an asset for.
                result.visuals_failed += 1
                notify(
                    f"autonomous draft {draft.id} has no visual "
                    f"({idea[:60]}): {draft.visual_error}"
                )
        except NoUsableTemplates as exc:
            # Nothing approved means no topic can succeed — stop rather than fail n times.
            notify(f"autonomous run cannot generate: {exc}")
            raise AutonomousRunFailed(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 — one bad topic must not end the run
            result.failed += 1
            log.exception("autonomous: topic failed: %s", idea[:80])
            notify(f"autonomous topic failed ({idea[:60]}): {exc}")

    session.flush()
    # The visual count is in the summary line and not only in the per-draft messages: a
    # notifier that keeps the last message, or a reader who skims to the end, must not come
    # away with "3 drafts created" when none of the three has a picture.
    summary = (
        f"autonomous run complete: {result.created} draft(s) created, "
        f"{result.failed} failed, from {result.topics} topic(s)"
    )
    if result.visuals_failed:
        summary += f" — {result.visuals_failed} of the drafts created has no visual"
    notify(summary)
    return result
