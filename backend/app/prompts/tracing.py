"""Making a model call and writing down that it happened, in one place.

The recording lives here rather than inside `llm.py` because what is worth recording is the
*prompt* — its name and version — and `llm.AzureChat` is handed a string and cannot know
which registered prompt it came from.
"""

import hashlib
import json
import time
import uuid
from collections.abc import Sequence
from typing import Any

from sqlmodel import Session

from app.llm import LLM
from app.models.generation_trace import GenerationTrace
from app.prompts.registry import Prompt


def new_correlation_id() -> str:
    """An id for one logical operation, to be shared by every call it makes.

    Minted by the caller, once, and passed down — `generate_draft` suggests templates and
    then writes, and the pair is the interesting unit. A per-call id would answer "what did
    this call cost" and lose "what did this draft cost", which is the question.
    """
    return uuid.uuid4().hex


def output_hash(parsed: dict) -> str:
    """A stable fingerprint of what the model returned.

    Hashes the **canonicalised parse**, not the raw response, because the raw response is not
    available here — `LLM.complete_json` returns a dict and discards the text, the markdown
    fence it may have arrived in, and its whitespace. That is a real limit and worth stating:
    two responses differing only in formatting hash the same. It is the right side to err on
    anyway, since the question a trace answers is "did the model say something different",
    not "did it indent differently".

    `sort_keys` is load-bearing. Python preserves insertion order, so without it the same
    answer arriving with its keys in a different order hashes differently and the column
    stops meaning anything at all — the failure would look like the model being unstable.
    """
    canonical = json.dumps(parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    # `surrogatepass`, and it is load-bearing rather than defensive. `"\ud800"` is a legal JSON
    # escape, so `complete_json` can hand back a perfectly ordinary dict holding a lone
    # surrogate — and a plain `.encode("utf-8")` raises on it. That raise happens *inside*
    # tracing, so it would turn a response the caller was about to reject cleanly into a
    # `UnicodeEncodeError` from a layer that promises to change no behaviour: extraction's
    # `_unstorable` exists precisely to survive this input, and one bad proposal would have
    # cost every sibling in the batch its slot. Found the day extraction started being traced.
    #
    # Byte-identical to the old encoding for every input without a surrogate, so no hash this
    # column already holds moves.
    return hashlib.sha256(canonical.encode("utf-8", "surrogatepass")).hexdigest()


def traced_call(
    session: Session,
    llm: LLM,
    prompt: Prompt,
    user: str,
    *,
    correlation_id: str,
    input_artifact_ids: dict[str, Any] | None = None,
    model_deployment: str | None = None,
    images: Sequence[bytes] = (),
) -> dict:
    """Send `prompt` with `user`, record what happened, return what the model said.

    A function taking the `Prompt` rather than a wrapper around the `LLM`, and the reason is
    `deps.get_llm`: one adapter is injected into everything, `generate_draft` sends two
    different prompts through it, and `extraction.py`'s prompts are not in the registry yet.
    A wrapper installed at the injection point would therefore have to guess which prompt each
    call carried — from the system text, or not at all — and a trace row naming the wrong
    prompt is worse than no row. Naming the prompt at the call site makes that unwriteable.

    **The row is written whether or not the call succeeds, and the exception is re-raised
    unchanged.** A call that raised is precisely the one whose record is worth having, and
    every caller's existing error handling — `run_autonomous` counting a failed topic,
    `_draw_visual` keeping the words — must see the same exception it sees today. Tracing that
    swallowed or wrapped an error would change behaviour, which is the one thing this must not
    do.

    ponytail: `session.add` and no commit. The row joins the caller's transaction and lands or
    rolls back with the work it describes. That means a rolled-back request leaves no trace of
    the calls it paid for — `SpendMeter`, which the caller owns and no transaction can undo,
    is what answers spend. Ceiling: a second session for traces, the day a trace matters more
    than the work it belongs to.
    """
    started = time.monotonic()
    trace = GenerationTrace(
        correlation_id=correlation_id,
        prompt_name=prompt.name,
        prompt_version=prompt.version,
        model_deployment=model_deployment,
        input_artifact_ids=input_artifact_ids or {},
        # One attempt, made here, and it is not repeated. Measured rather than assumed — the
        # day something wraps this in a retry policy, that policy sets the number.
        retries=0,
        # prompt_tokens and completion_tokens are left NULL, not zeroed: `complete_json`
        # returns the parsed dict and drops the response's `usage` block, so nothing at this
        # layer has seen a token count. `0` would be a claim the call was free.
    )
    try:
        parsed = llm.complete_json(prompt.text, user, images)
    except Exception as exc:
        trace.error = f"{type(exc).__name__}: {exc}"
        raise
    else:
        trace.output_hash = output_hash(parsed)
        return parsed
    finally:
        trace.latency_ms = int((time.monotonic() - started) * 1000)
        session.add(trace)
