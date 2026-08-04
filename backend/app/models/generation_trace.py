from datetime import UTC, datetime

from sqlalchemy import Column, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class GenerationTrace(SQLModel, table=True):
    """One model call: which prompt said it, what went in, what came back, what it cost.

    A draft already records which *templates* produced it. Nothing recorded which *prompt*
    did, so "this draft reads differently from last week's — did the prompt change?" was
    unanswerable, and a prompt change could not be evaluated against a baseline because there
    was no record of which calls ran under which version. That is what this row is for.

    **Every column that might not be observed is nullable, and NULL means "nobody measured
    it".** `0` tokens is a claim that a call consumed none, which is not a thing that happens;
    it is the same `—` versus `0` rule the rest of this app prints by, applied where the
    absence is easiest to paper over. Today `llm.LLM` returns a parsed dict and nothing else,
    so token counts and the model deployment are structurally unmeasurable from the tracing
    layer and are NULL on every row this app writes.

    **Not a spend counter.** `autonomous.SpendMeter` counts paid calls, increments *before*
    the call so a completion that raises is still counted, and is owned by the request handler
    so the number survives a run that dies. This row is written *after* the attempt and records
    what happened. The two can legitimately disagree — a meter of 4 against 3 trace rows means
    one call did not get as far as being recorded — and neither is the other's replacement.

    ponytail: one flat row per call, no parent/child span tree. `correlation_id` groups the
    calls of one logical operation, which is what "show me everything that made this draft"
    needs. Spans arrive with a tracing backend to view them in; the ceiling is OpenTelemetry,
    which the version plan defers until a failure takes more than one `grep` to explain.
    """

    __tablename__ = "generation_trace"

    id: int | None = Field(default=None, primary_key=True)

    # Groups every call belonging to one logical operation — the suggest and the write behind
    # a single `generate_draft` share one. Not a foreign key to `draft`: the calls that fail
    # produce no draft at all, and those are the ones worth being able to look up.
    correlation_id: str = Field(index=True)

    # The prompt as the registry addresses it. Both columns, never just the name: a name alone
    # would make a trace unreproducible the first time the prompt was revised, which is the
    # `(family_id, version)` rule that governs template lineage, one layer up.
    prompt_name: str = Field(index=True)
    prompt_version: str

    # Which deployment answered. NULL means the caller did not say — not "the default one".
    #
    # ponytail: the tracing layer cannot find this out for itself. `llm.AzureChat` knows its
    # deployment and `complete_json` does not return it, so reading
    # `settings.azure_openai_chat_deployment` here would record configuration as observation
    # and be wrong on the first call that went anywhere else. Upgrade path: have `LLM` return
    # the deployment and usage alongside the parsed dict, and fill this and the token columns
    # from the response.
    model_deployment: str | None = None

    # What this call was about, as {label: id} — `{"hook": "abc123/1", "draft": "17"}`.
    # Free-form because the artifacts differ per prompt and a column per kind would be a
    # migration every time a stage is added. `nullable=False` with a `{}` default, following
    # `draft.asset_values`: no reader should have to guard for `None` on a dict it merges.
    input_artifact_ids: dict = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False))

    # sha256 of the model's parsed output, canonicalised. NULL when the call produced no
    # output at all — which is what `error` explains. See `tracing.output_hash` for why the
    # hash is over the canonicalised parse rather than the raw bytes.
    output_hash: str | None = None

    # NULL always, today, and deliberately: see `model_deployment`. Split into two columns
    # rather than one total because a prompt change moves the input side and a verbosity
    # change moves the output side, and a single number cannot tell an evaluator which.
    prompt_tokens: int | None = None
    completion_tokens: int | None = None

    # Attempts *beyond the first*, so `0` is a measurement — the call was made once and
    # needed no repeat — and NULL is a caller that did not track it. Nothing in this app
    # retries a completion yet, so every row it writes today carries `0`.
    retries: int | None = None

    # Wall clock around the attempt, recorded for a call that raised too: the time a failing
    # call spent is real, and it is usually the interesting number.
    latency_ms: int | None = None

    # The exception, as `TypeName: message`. NULL on a call that returned. Present alongside a
    # NULL `output_hash`, which is the pair that distinguishes "failed" from "returned
    # something empty".
    error: str | None = None

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True)),
    )
