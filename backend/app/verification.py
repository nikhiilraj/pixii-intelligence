"""Does the finished post assert anything it cannot back up?

This runs on the **written candidate**, after the write and its revision loop, and it is a
different question from the one `generation._research_findings` answers. That function
compares the *plan* against the dossier: every claim research was asked to settle, checked
for a supported counterpart. It is a real check and it stays. But it can only ever see
claims somebody planned, so the two holes it leaves are the two this module exists to close:

- **A sentence the model invented while writing was never in the plan**, so nothing looked at
  it. An invented statistic sailed through every gate.
- **`none` mode had no evidence gate at all.** `_research_findings` opens with
  `if dossier is None: return [], []`, so a draft written with no research — the mode whose
  entire definition is "make no external factual claims" — was checked against nothing.

**Deciding whether a sentence is a checkable fact is model judgement, and it is done with a
registered, versioned prompt.** A regex over numbers would call "we cut it to three days" a
statistic and "Acme acquired Foo" not one, which is the wrong answer in both directions.

**Resolution, though, is not model judgement.** The model names its evidence and this module
decides whether that evidence exists:

- A dossier claim is named by its run-local label, `C1`/`C2`, and resolved against the claims
  of *this job*. Its status was derived by `research._status` from `Citation` rows, and every
  one of those rows was written only after its span was checked against a page this job
  actually fetched — so the row's existence is the proof, and there is deliberately no second
  evidence store here. A label nobody was shown resolves to nothing.
- The idea is cited by quoting it, character for character, exactly as `research` demands of a
  citation. A fact the operator supplied is evidence; a fact the model remembers is not.

**Nothing else is evidence.** The voice exemplars in particular are never shown to this
prompt: `generation._editorial_context` hands whole exemplar post bodies to the *writer* with
a prompt instruction as the only thing stopping it lifting their facts, and one hop later a
borrowed fact would arrive with its own citation. That is structural rather than a rule — and
`tests/test_verification.py` still asserts it, because an untested structural property is one
a refactor removes without noticing.

**What blocks is decided by `gates.check`, not here.** `Review.unsupported` and
`Review.contradicted` are plain claim texts, fed to the two parameters that gate already
takes; `generate_reviewed_draft` already treats `uncited_claim`/`contradicted_claim` as an
evidence failure and deliberately keeps them away from the wording revision loop. Building a
second blocking path would mean two places deciding what stops a post.
"""

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlmodel import Session

from app.llm import LLM
from app.models.research import DISPUTED, REFUTED, SUPPORTED
from app.output_schema import validate as validate_output
from app.prompts.registry import index
from app.prompts.tracing import traced_call
from app.prompts.verification import PROMPTS

log = logging.getLogger("pixii.verification")

# The exact prompt this module sends, pinned on one greppable line and resolved out of
# `PROMPTS` by `(name, version)` — never imported as a bare symbol, which is a "latest"
# lookup wearing an import statement. `app.rubric` says the same thing at greater length.
_VERIFY = index(PROMPTS)[("verification.claims", "1.0.0")]

# How the model addresses one of this run's claims. Run-local, ours, and never a database id:
# an id is a number the model could compose one of, and a label it was not shown resolves to
# nothing at all. Same construction as `research.evidence_block`'s `S1`/`S2` source labels,
# and deliberately a different letter so a claim label and a source label cannot be confused
# in a persisted review a person reads six weeks later.
CLAIM_LABEL = "C{index}"

# Recorded on a review when the operator's own idea is what carries an assertion.
IDEA_LABEL = "IDEA"

# Shorter than this is not a quotation.
#
# The degenerate case is the whole reason for the constant: `"" in anything` is True, so an
# empty span passes a substring check and every assertion in the post becomes "supported by
# the idea". `research.SPAN_MIN_CHARS` is the same number for the same reason, and it is
# restated rather than imported — importing `app.research` for one integer would put this
# module behind `fetching`, `autonomous` and the search provider seam at import time, which
# is the reason `rubric.py` restates its fence rather than borrowing one.
QUOTE_MIN_CHARS = 12

# **Restated from `gates._echoed_claims`, and the two must stay identical.**
#
# That gate compares each claim text against the whole candidate *and* against each of its
# sentences, split with exactly this pattern over exactly the same `full_text`. What this
# module hands it is one of those sentences, so the comparison scores 1.0 and the gate fires.
# Split differently here — on `re.split(r"[.]")`, say, or over `hook` and `body` separately —
# and the string handed over is no longer a member of the gate's comparison set: an embedded
# clause against its long parent sentence scores about 0.49 against a threshold of 0.72, so
# the finding would silently never be raised and this whole stage would be decoration.
# `tests/test_verification.py` pins the join by feeding a resolved sentence straight into
# `gates.check` and asserting the finding comes back.
_SENTENCES = re.compile(r"[.!?\n]+")

# What resolution concluded about one assertion. Four of these are `models.research`'s claim
# statuses by design — an assertion resolved through a dossier claim inherits that claim's
# verdict, and inventing a parallel vocabulary would mean two names for one state on a screen
# that shows both.
NO_CITATION_NEEDED = "no_citation_needed"  # an opinion or a first-party statement
NOT_IN_POST = "not_in_post"  # quoted text that is not in the candidate — never blocking
UNSUPPORTED = "unsupported"  # factual, and nothing shown to the model carries it


class VerificationOutputError(RuntimeError):
    """The verifier answered, but not with assertions this module can use.

    Raised rather than absorbed. A verifier whose output could not be read has not verified
    anything, and `generate_reviewed_draft` records that as a visible `failed_review` — the
    fail-closed direction, because the alternative is a broken verifier silently switching the
    evidence gate off while every draft still reaches `ready`.

    ponytail: no repair attempt, where `rubric.evaluate` spends one. A repair is a second
    billed call, and this failure is recoverable from Studio with the retry that already
    exists. Ceiling: if malformed verifier output turns out to be common rather than rare,
    the upgrade is `rubric`'s single repair statement, copied — not a loop.
    """


@dataclass(frozen=True)
class Assertion:
    """One thing the post says, and what the evidence did to it."""

    text: str  # the post's own sentence, as it will be handed to `gates.check`
    kind: str  # factual | opinion | first_party, as the model classified it
    verdict: str  # a claim status, or `NO_CITATION_NEEDED` / `NOT_IN_POST` / `UNSUPPORTED`
    evidence: str  # `IDEA`, a claim label, or "" for an assertion nothing carries

    @property
    def blocks(self) -> bool:
        """Whether this assertion should stop the draft reaching readiness.

        Three verdicts and no others. `NOT_IN_POST` is deliberately not among them: an
        assertion the model composed rather than quoted is not something the post actually
        says, so blocking on it would refuse a draft over a sentence nobody wrote. That is
        the false-positive direction `gates.CLAIM_ECHO_RATIO` documents for the neighbouring
        check, and it is the direction that costs a person an argument with the tool.
        """
        return self.verdict in {UNSUPPORTED, REFUTED, DISPUTED}


@dataclass(frozen=True)
class Review:
    """Every assertion in one candidate, resolved against what the run was allowed to use.

    Frozen with a tuple, like `research.ResearchDossier` and for its reason: this is the
    record a reviewer reads to find out why wording passed or failed, and it must not be a
    structure a later stage can append to.
    """

    prompt_name: str
    prompt_version: str
    # `dossier` when research ran and its claims were the comparison set, `idea` when the
    # operator's idea was the whole of it. Recorded because "nothing blocked" means something
    # different in each case, and a reader cannot tell them apart from the assertions alone.
    basis: str
    assertions: tuple[Assertion, ...]

    @property
    def unsupported(self) -> list[str]:
        """Claim texts for `gates.check(unsupported_claims=...)`.

        `REFUTED` appears in both this and `contradicted`, exactly as
        `generation._research_findings` splits the dossier's own statuses: sources that
        contradict a claim both fail to support it and disagree with it, and a reviewer
        should see it said twice rather than have to know which list it was filed under.
        """
        return _distinct(a.text for a in self.assertions if a.verdict in {UNSUPPORTED, REFUTED})

    @property
    def contradicted(self) -> list[str]:
        return _distinct(a.text for a in self.assertions if a.verdict in {DISPUTED, REFUTED})

    @property
    def summary(self) -> str:
        """One line for a person, naming what was checked against what."""
        against = "the research dossier" if self.basis == "dossier" else "the idea alone"
        blocking = sum(1 for a in self.assertions if a.blocks)
        return (
            f"{len(self.assertions)} assertion(s) checked against {against}; "
            f"{blocking} could not be supported."
        )

    def as_dict(self) -> dict[str, Any]:
        """The JSONB shape persisted on `Draft.verification_result`.

        `assertions` is written even when it is empty, and that is the honest record of the
        one thing this stage cannot rule out: a verifier that reported no assertions at all
        for a fact-laden post passes everything, and the only way to see that from the
        outside is an empty list sitting next to a post full of numbers.

        ponytail: no second opinion and no floor on how many assertions a post of a given
        length must yield. Ceiling: an under-reporting extractor is invisible to this
        module. The upgrade is a sampled human check of persisted reviews, which needs
        reviews to exist first — there are zero.
        """
        return {
            "prompt_name": self.prompt_name,
            "prompt_version": self.prompt_version,
            "basis": self.basis,
            "summary": self.summary,
            "assertions": [
                {
                    "text": item.text,
                    "kind": item.kind,
                    "verdict": item.verdict,
                    "evidence": item.evidence,
                    "blocks": item.blocks,
                }
                for item in self.assertions
            ],
        }


def verify(
    session: Session,
    llm: LLM,
    *,
    candidate: Mapping[str, Any],
    idea: str,
    dossier: Any | None,
    correlation_id: str,
) -> Review:
    """Extract what the candidate asserts, and resolve each assertion against the evidence.

    `candidate` is the structured object the model returned — `hook`, `body`,
    `visual_values` — and not a `Draft`, for the reason `gates.check` takes the same mapping:
    verification happens before the words are on a row, and a function taking the row could
    not be called where it actually runs.

    `dossier` is typed `Any` rather than `research.ResearchDossier` on purpose. Importing
    `app.research` for the annotation would pull `fetching`, `autonomous` and the search
    provider seam into every import of this module — the same reason `generation` imports it
    inside the function body. Only `.claims` is read, and only `.text`, `.status` and
    `.supporting_citation_ids` off each one.

    `None` for `dossier` is `none` mode: the comparison set is then the operator's idea and
    nothing else, which is precisely what `none` mode promises. It is not a special case in
    the code — the idea is evidence in both modes (the write prompt already says "present in
    the original idea or supported by the dossier"), so `none` mode is the same path with a
    smaller evidence set.

    `correlation_id` is the caller's, shared with the write that produced the candidate, so
    the two are one operation in the trace table.

    Raises `VerificationOutputError` when the verifier's answer cannot be used.
    """
    full_text = _full_text(candidate)
    claims = _labelled(dossier)

    answer = traced_call(
        session,
        llm,
        _VERIFY,
        _user_message(full_text, idea, claims, researched=dossier is not None),
        correlation_id=correlation_id,
        input_artifact_ids={
            "research_job": getattr(dossier, "job_id", None),
            "dossier_claims": len(claims),
        },
    )
    try:
        validate_output(answer, _VERIFY.output_schema)
    except Exception as exc:
        raise VerificationOutputError(f"the verifier returned unusable output: {exc}") from exc

    assertions = [
        _resolved(item, full_text=full_text, idea=idea, claims=claims)
        for item in answer["assertions"]
    ]
    return Review(
        # Read off the prompt object rather than restated, so the persisted review names the
        # words that were actually sent even if `_VERIFY` is ever repointed.
        prompt_name=_VERIFY.name,
        prompt_version=_VERIFY.version,
        basis="dossier" if dossier is not None else "idea",
        assertions=tuple(assertions),
    )


def _full_text(candidate: Mapping[str, Any]) -> str:
    """The string that would publish — identical to `gates.check` and `Draft.full_text`.

    Not "hook and body, verified separately". The gate this module feeds compares against a
    joined text and its sentences; verifying a differently-joined string would resolve
    assertions to sentences that are not in the gate's comparison set at all.
    """
    hook = str(candidate.get("hook") or "").strip()
    body = str(candidate.get("body") or "").strip()
    return f"{hook}\n\n{body}".strip()


def _labelled(dossier: Any | None) -> dict[str, Any]:
    """This run's claims, keyed by the label the model is allowed to cite them by.

    Order is the dossier's own — `research.dossier` orders by id, which is claim order — and
    is not sorted by status. Sorting supported claims to the top would present the list as a
    ranking of evidence, which is the reason that function says it orders by nothing else.
    """
    if dossier is None:
        return {}
    return {
        CLAIM_LABEL.format(index=position): claim
        for position, claim in enumerate(dossier.claims, 1)
    }


def _resolved(
    item: Mapping[str, Any], *, full_text: str, idea: str, claims: Mapping[str, Any]
) -> Assertion:
    """One reported assertion, checked against the post and then against the evidence."""
    quoted = str(item.get("text") or "")
    kind = str(item.get("kind") or "")

    stated = _stated_sentence(full_text, quoted)
    if stated is None:
        # The model composed this rather than quoting it, so the post does not say it. Kept
        # in the record — an extractor that mostly invents is worth being able to see — and
        # not blocking, because refusing a draft over a sentence nobody wrote is the false
        # positive that gets a check switched off.
        log.warning("verification: assertion is not a sentence of the candidate: %r", quoted[:80])
        return Assertion(text=_collapse(quoted), kind=kind, verdict=NOT_IN_POST, evidence="")

    if kind != "factual":
        # An opinion or a statement about the author's own work. It needs no citation and it
        # may not block: this is the direction that costs a person an argument with the tool,
        # so a non-factual assertion is settled here and never reaches the evidence checks.
        return Assertion(text=stated, kind=kind, verdict=NO_CITATION_NEEDED, evidence="")

    label = str(item.get("claim") or "").strip()
    claim = claims.get(label)
    if claim is not None:
        status = str(getattr(claim, "status", ""))
        cited = bool(getattr(claim, "supporting_citation_ids", ()))
        if status == SUPPORTED and cited:
            # Both halves, though `research._status` already guarantees the second: a claim is
            # `SUPPORTED` only because supporting `Citation` rows exist, and each of those was
            # written only after its span was found in a page this job fetched. Saying it here
            # too keeps the property readable at the point that depends on it.
            return Assertion(text=stated, kind=kind, verdict=SUPPORTED, evidence=label)
        if status in {REFUTED, DISPUTED}:
            # The sources disagreed. That outcome wins over the idea below: an operator
            # asserting something the research contradicts is exactly what a reviewer needs
            # told, not a reason to wave it through.
            return Assertion(text=stated, kind=kind, verdict=status, evidence=label)
        # An `UNSUPPORTED` claim falls through to the idea, which may still carry it.
    elif label:
        # A label nobody was shown. The fabricated citation, and the reason the model cites by
        # run-local label at all — it cannot invent a `C7` that resolves when only C1–C3 exist.
        log.warning("verification: assertion cites unknown claim label %r", label)

    if _quotes(idea, str(item.get("idea_span") or "")):
        return Assertion(text=stated, kind=kind, verdict=SUPPORTED, evidence=IDEA_LABEL)

    return Assertion(text=stated, kind=kind, verdict=UNSUPPORTED, evidence="")


def _stated_sentence(full_text: str, quoted: str) -> str | None:
    """The candidate's own sentence carrying this quotation, or `None` if it has none.

    **The returned sentence, not the quotation, is what goes to the gate**, and that is the
    whole reason this function exists. `gates._echoed_claims` compares a claim against the
    candidate's sentences; a clause quoted out of a long sentence — "Acme removed one checkout
    field" out of a sentence that goes on to say why it matters — scores around 0.49 against
    that parent, under a threshold of 0.72, so the finding would never be raised. Resolving to
    the sentence makes the comparison exact, and it also gives the reviewer the line that has
    to change rather than a fragment of it.

    `None` for a quotation that is in no sentence, including one that runs across a sentence
    boundary. ponytail: a quotation spanning two sentences is recorded as `NOT_IN_POST` and
    does not block. Ceiling: the prompt asks for one whole sentence, so this is a model that
    did not do as asked rather than a shape the check cannot express; the upgrade is matching
    a window of consecutive sentences, and it should wait for evidence that it happens.
    """
    # The terminator comes off first. A model asked for "the whole sentence" hands back the
    # full stop with it, and `_SENTENCES` splits *on* that character — so the sentence in the
    # candidate ends one character earlier than the quotation does and a literal containment
    # test misses every correctly-quoted sentence. This is not a loosening of the check: the
    # characters removed are exactly the ones the split has already consumed.
    wanted = _collapse(quoted).strip(".!?").strip().casefold()
    if len(wanted) < QUOTE_MIN_CHARS:
        # `"" in anything` is True. Without this, an empty quotation resolves to the first
        # sentence of the post and every assertion is attributed to a line at random.
        return None
    for sentence in _SENTENCES.split(full_text):
        collapsed = _collapse(sentence)
        if collapsed and wanted in collapsed.casefold():
            return collapsed
    return None


def _quotes(source: str, span: str) -> bool:
    """Whether `span` is really text out of `source`.

    Collapsed and case-folded on both sides, exactly as `research._spans` matches a citation
    against the page it names, and for its reasons: a model quoting across a line break hands
    back a space where the source has a newline, and one that title-cases a quotation has
    still quoted it. A literal `in` test would reject honest citations and invite somebody to
    loosen this check later, which is how a check stops checking.
    """
    wanted = _collapse(span)
    if len(wanted) < QUOTE_MIN_CHARS:
        return False
    return wanted.casefold() in _collapse(source).casefold()


def _collapse(value: str) -> str:
    return " ".join(value.split())


def _distinct(texts: Any) -> list[str]:
    """Unique, in first-seen order — `dict.fromkeys`, as `_research_findings` dedupes."""
    return list(dict.fromkeys(texts))


# --- what the verifier is shown ---------------------------------------------------------------

# The fence, same construction as `research._OPEN` and `rubric._OPEN`: a marker that could
# plausibly occur in prose is not a marker. Written out here rather than imported for the
# reason `rubric` gives — importing `app.research` to borrow four characters would put
# verification behind `fetching` and the search provider seam at import time.
_OPEN = "<<<PIXII-{label}>>>"
_CLOSE = "<<<END PIXII-{label}>>>"

# Rewritten wherever they occur inside a block, so text inside a block can neither close it
# nor open another.
_FORGERY = (("<<<", "‹‹‹"), (">>>", "›››"))


def _fenced(label: str, text: str) -> str:
    """One block of quoted material, unable to address the model from inside itself."""
    for forged, replacement in _FORGERY:
        text = text.replace(forged, replacement)
    return f"{_OPEN.format(label=label)}\n{text}\n{_CLOSE.format(label=label)}"


def _user_message(
    full_text: str, idea: str, claims: Mapping[str, Any], *, researched: bool
) -> str:
    """The post, the idea, and this run's claims. **Nothing else** — see the module docstring.

    The idea is fenced along with the rest. It is first-party in the sense that an operator
    typed it, but it is still quoted material being handed to a model that is about to be
    asked a question about it, and blueprint invariant 7 does not stop applying because the
    author is on our side.

    **`researched` is passed rather than inferred from an empty `claims`**, and that is the
    `0`-versus-NULL rule the migration for this column is written about. A `light` run whose
    claim pass settled nothing hands over the same empty mapping as a `none`-mode run that
    never searched — and telling the model "no research was run" about the first is a false
    statement in a prompt. It changes no verdict either way, since an assertion with nothing
    to cite is `UNSUPPORTED` in both cases, which is exactly why it would never have been
    noticed.
    """
    parts = [
        _fenced("POST", full_text),
        "The idea this post was written from:",
        _fenced("IDEA", idea.strip() or "(none supplied)"),
    ]
    if claims:
        lines = "\n".join(
            f"[{label}] ({getattr(claim, 'status', 'unknown')}) {getattr(claim, 'text', '')}"
            for label, claim in claims.items()
        )
        parts.extend(
            [
                "Research claims, with the status the evidence gave each:",
                _fenced("CLAIMS", lines),
            ]
        )
    elif researched:
        parts.append(
            "Research ran for this post and settled no claim you can cite. The idea above is "
            "the only material this post was allowed to draw a fact from."
        )
    else:
        # Said out loud rather than left out. A silently absent claim list reads to the model
        # as "research was run and found nothing", which is the sentence above and a different
        # answer from this one.
        parts.append(
            "No research was run for this post. The idea above is the only material it was "
            "allowed to draw a fact from."
        )
    return "\n\n".join(parts)


__all__ = [
    "CLAIM_LABEL",
    "IDEA_LABEL",
    "NOT_IN_POST",
    "NO_CITATION_NEEDED",
    "QUOTE_MIN_CHARS",
    "UNSUPPORTED",
    "Assertion",
    "Review",
    "VerificationOutputError",
    "verify",
]
