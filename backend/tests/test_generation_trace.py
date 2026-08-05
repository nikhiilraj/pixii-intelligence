"""One row per model call, and what each of its columns is allowed to claim.

The recurring subject here is the difference between `0` and NULL. A trace that reports zero
tokens says the call was free; the honest answer, while `LLM.complete_json` returns a parsed
dict and drops the response's usage block, is that nobody measured it.
"""

import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlmodel import SQLModel, col, select

from app.db import utc
from app.models.generation_trace import GenerationTrace
from app.prompts.registry import Prompt
from app.prompts.tracing import new_correlation_id, output_hash, traced_call

PROMPT = Prompt(
    name="t.example",
    version="1.0.0",
    text="say something",
    output_schema={"type": "object", "properties": {}},
)

ANSWER = {"hook": "a hook", "body": "a body"}


class FakeLLM:
    """Records what it was asked, answers with `responses` in turn, or raises."""

    def __init__(self, *responses, raises: Exception | None = None, delay: float = 0.0):
        self.responses = list(responses) or [ANSWER]
        self.raises = raises
        self.delay = delay
        self.calls: list[tuple[str, str, tuple]] = []

    def complete_json(self, system: str, user: str, images=()) -> dict:
        self.calls.append((system, user, tuple(images)))
        if self.delay:
            time.sleep(self.delay)
        if self.raises is not None:
            raise self.raises
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


def traces(session) -> list[GenerationTrace]:
    return list(session.exec(select(GenerationTrace).order_by(col(GenerationTrace.id))).all())


# --- what a call records --------------------------------------------------------------------


def test_a_call_writes_one_row_naming_the_prompt_and_its_version(session):
    traced_call(session, FakeLLM(), PROMPT, "the user message", correlation_id="corr-1")
    session.flush()

    (trace,) = traces(session)
    assert (trace.prompt_name, trace.prompt_version) == ("t.example", "1.0.0")
    assert trace.correlation_id == "corr-1"


def test_the_prompt_reaches_the_model_verbatim(session):
    """`traced_call` sends `prompt.text`, not a rendering of it."""
    llm = FakeLLM()
    traced_call(session, llm, PROMPT, "the user message", correlation_id="corr-1")

    (system, user, images) = llm.calls[0]
    assert system == PROMPT.text
    assert user == "the user message"
    assert images == ()


def test_images_are_passed_through(session):
    llm = FakeLLM()
    traced_call(session, llm, PROMPT, "u", correlation_id="c", images=(b"PNGBYTES",))

    assert llm.calls[0][2] == (b"PNGBYTES",)


def test_the_answer_is_returned_to_the_caller_unchanged(session):
    result = traced_call(session, FakeLLM(ANSWER), PROMPT, "u", correlation_id="c")

    assert result == ANSWER


def test_the_input_artifacts_are_recorded_as_given(session):
    traced_call(
        session,
        FakeLLM(),
        PROMPT,
        "u",
        correlation_id="c",
        input_artifact_ids={"hook": "fam-a/1", "structure": "fam-b/2"},
    )
    session.flush()

    (trace,) = traces(session)
    assert trace.input_artifact_ids == {"hook": "fam-a/1", "structure": "fam-b/2"}


def test_no_input_artifacts_is_an_empty_dict_and_never_null(session):
    """Following `draft.asset_values`: every reader merges this, none should guard for None."""
    traced_call(session, FakeLLM(), PROMPT, "u", correlation_id="c")
    session.flush()

    (trace,) = traces(session)
    assert trace.input_artifact_ids == {}


def test_one_correlation_id_groups_every_call_of_one_operation(session):
    """The unit worth costing is the draft, not the completion — two calls, one id."""
    correlation = new_correlation_id()
    traced_call(session, FakeLLM(), PROMPT, "suggest", correlation_id=correlation)
    traced_call(session, FakeLLM(), PROMPT, "write", correlation_id=correlation)
    session.flush()

    assert [t.correlation_id for t in traces(session)] == [correlation, correlation]


def test_two_operations_do_not_share_a_correlation_id():
    assert new_correlation_id() != new_correlation_id()


# --- NULL means nobody measured it ----------------------------------------------------------


def test_token_usage_is_null_and_not_zero(session):
    """`0` tokens claims a call that consumed none. Nothing here has seen a token count.

    The mutation this guards is a tracer that defaults these to `0` — an absence dressed as a
    measurement, and the same defect the `—` versus `0` rule exists to prevent on screen.
    """
    traced_call(session, FakeLLM(), PROMPT, "u", correlation_id="c")
    session.flush()

    (trace,) = traces(session)
    assert trace.prompt_tokens is None
    assert trace.completion_tokens is None


def test_the_model_deployment_is_null_when_the_caller_does_not_say(session):
    """Not `settings.azure_openai_chat_deployment` — that is configuration, not observation."""
    traced_call(session, FakeLLM(), PROMPT, "u", correlation_id="c")
    session.flush()

    (trace,) = traces(session)
    assert trace.model_deployment is None


def test_the_model_deployment_is_recorded_when_the_caller_does_say(session):
    traced_call(session, FakeLLM(), PROMPT, "u", correlation_id="c", model_deployment="gpt-5-chat")
    session.flush()

    (trace,) = traces(session)
    assert trace.model_deployment == "gpt-5-chat"


def test_retries_is_zero_because_one_attempt_was_actually_observed(session):
    """The one count here that is measured rather than absent: made once, not repeated."""
    traced_call(session, FakeLLM(), PROMPT, "u", correlation_id="c")
    session.flush()

    (trace,) = traces(session)
    assert trace.retries == 0


def test_latency_is_the_time_the_call_took_and_not_a_constant(session):
    """`assert latency_ms >= 0` passed against a hardcoded `0`, which is the same
    structurally-always-zero number `SpendMeter` warns about. The call is made slow on
    purpose, so a tracer that stopped timing fails here instead of reporting free calls."""
    traced_call(session, FakeLLM(delay=0.05), PROMPT, "u", correlation_id="c")
    session.flush()

    (trace,) = traces(session)
    assert trace.latency_ms is not None
    assert trace.latency_ms >= 40


def test_a_failing_call_is_timed_too(session):
    """The time a failing call spent is real, and usually the interesting number."""
    with pytest.raises(RuntimeError):
        traced_call(
            session,
            FakeLLM(raises=RuntimeError("boom"), delay=0.05),
            PROMPT,
            "u",
            correlation_id="c",
        )
    session.flush()

    (trace,) = traces(session)
    assert trace.latency_ms is not None
    assert trace.latency_ms >= 40


# --- the output hash ------------------------------------------------------------------------


def test_the_output_hash_is_the_sha256_of_the_canonicalised_answer(session):
    traced_call(session, FakeLLM(ANSWER), PROMPT, "u", correlation_id="c")
    session.flush()

    expected = hashlib.sha256(
        json.dumps(ANSWER, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    (trace,) = traces(session)
    assert trace.output_hash == expected


def test_the_same_answer_in_a_different_key_order_hashes_the_same():
    """Without `sort_keys` this column means nothing: Python preserves insertion order, so a
    model returning the same answer with its keys swapped would look like a changed output."""
    assert output_hash({"a": 1, "b": 2}) == output_hash({"b": 2, "a": 1})


def test_a_different_answer_hashes_differently():
    assert output_hash({"hook": "one"}) != output_hash({"hook": "two"})


def test_a_nested_answer_hashes_stably_at_every_level():
    assert output_hash({"t": [{"idea": "x", "why": "y"}]}) == output_hash(
        {"t": [{"why": "y", "idea": "x"}]}
    )


def test_non_ascii_output_hashes_without_escaping_drift():
    """`ensure_ascii=False` is fixed, so an em dash hashes as itself rather than `\\u2014`."""
    expected = hashlib.sha256(
        json.dumps({"body": "one — two"}, sort_keys=True, separators=(",", ":"),
                   ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    assert output_hash({"body": "one — two"}) == expected


# --- a call that failed ---------------------------------------------------------------------


def test_a_failing_call_is_still_recorded(session):
    """The call worth having a record of. It was attempted, it took time, it cost money."""
    with pytest.raises(RuntimeError):
        traced_call(session, FakeLLM(raises=RuntimeError("boom")), PROMPT, "u", correlation_id="c")
    session.flush()

    (trace,) = traces(session)
    assert trace.error == "RuntimeError: boom"
    assert trace.latency_ms is not None


def test_a_failing_call_records_no_output_hash(session):
    """NULL because there was no output — which `error` alongside it explains."""
    with pytest.raises(RuntimeError):
        traced_call(session, FakeLLM(raises=RuntimeError("boom")), PROMPT, "u", correlation_id="c")
    session.flush()

    (trace,) = traces(session)
    assert trace.output_hash is None


def test_a_successful_call_records_no_error(session):
    traced_call(session, FakeLLM(), PROMPT, "u", correlation_id="c")
    session.flush()

    (trace,) = traces(session)
    assert trace.error is None


def test_the_callers_own_exception_reaches_the_caller(session):
    """Tracing must not change behaviour: `run_autonomous` counts a failed topic on this
    exception and `_draw_visual` keeps the words on it. A wrapped error breaks both."""
    original = ValueError("the model said no")
    with pytest.raises(ValueError) as caught:
        traced_call(session, FakeLLM(raises=original), PROMPT, "u", correlation_id="c")

    assert caught.value is original


# --- the row exists at all ------------------------------------------------------------------


def test_the_model_is_registered_where_alembic_looks_for_it():
    """An unimported model is invisible to autogenerate, and the table never gets created.

    Asserted through `app.models` rather than through `SQLModel.metadata` alone: importing
    `app.prompts.tracing` pulls in `app.models.generation_trace` directly, which registers the
    table on the metadata by itself. A metadata-only check would therefore stay green with the
    export removed from `app/models/__init__.py` — passing while `alembic revision
    --autogenerate` quietly stopped seeing the table.
    """
    import app.models

    assert app.models.GenerationTrace is GenerationTrace
    assert "GenerationTrace" in app.models.__all__
    assert "generation_trace" in SQLModel.metadata.tables


def test_the_row_survives_a_round_trip_through_the_database(session):
    """The columns exist in the migrated schema, not only on the model."""
    traced_call(
        session,
        FakeLLM(),
        PROMPT,
        "u",
        correlation_id="c",
        input_artifact_ids={"draft": "17"},
        model_deployment="gpt-5-chat",
    )
    session.flush()
    (trace,) = traces(session)
    session.expire(trace)

    assert trace.prompt_name == "t.example"
    assert trace.input_artifact_ids == {"draft": "17"}
    assert trace.created_at is not None


def test_the_timestamp_reads_back_naive_and_compares_through_db_utc(session):
    """`created_at` is written aware and stored in a `timestamp without time zone`.

    So it comes back naive, and comparing it to anything aware *raises* — the hazard CLAUDE.md
    names and `test_publish_detection.utc_naive` documents from the other side. Asserted rather
    than assumed, because "is not None" passes either way and the failure only shows up in
    whatever first tries to ask how old a trace is.
    """
    traced_call(session, FakeLLM(), PROMPT, "u", correlation_id="c")
    session.flush()
    (trace,) = traces(session)
    session.expire(trace)

    assert trace.created_at.tzinfo is None
    with pytest.raises(TypeError):
        assert trace.created_at <= datetime.now(UTC)
    assert utc(trace.created_at) <= datetime.now(UTC)


# --- no model call this application makes is untraced ---------------------------------------


def test_no_model_call_bypasses_tracing():
    """The audit, as a test rather than as a `grep` somebody has to remember to run.

    `llm.complete_json` is the one method that spends money, and every call to it should come
    from `traced_call`. Two bypasses survived the first tracing wave — `extraction.py`'s three
    prompts and `autonomous.propose_topics` — and both were invisible from anywhere but the
    source, because a bypassed call works perfectly and simply writes no row.

    Source text, not behaviour, which is the honest limitation: this catches a *new* call
    site, not a call routed through something clever. That is the failure that has actually
    happened here twice, and the one a reviewer is most likely to miss.
    """
    root = Path(__file__).resolve().parents[1] / "app"
    offenders = {
        path.relative_to(root).as_posix(): line.strip()
        for path in root.rglob("*.py")
        for line in path.read_text().splitlines()
        if ".complete_json(" in line and not line.lstrip().startswith("#")
    }
    # `tracing.py` is the only place that calls it. `llm.py` merely defines it, which this
    # match does not catch — `def complete_json(` carries no dot.
    assert set(offenders) == {"prompts/tracing.py"}, offenders
