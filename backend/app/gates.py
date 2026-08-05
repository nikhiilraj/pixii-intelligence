"""Deterministic hard gates over one generated candidate.

No language model, no database, no network. Everything here is a pure function of its
arguments, so a finding is reproducible from the arguments alone and a test needs no
session — which is the whole reason this module is separable from `generation`.

**A gate never ranks.** It says *this failed and here is why*; it does not score a
candidate, compare two candidates, or order templates against each other. `Finding`
carries no number by design — see `GATES` and the `Finding` docstring. The 100-point
craft rubric is a separate, model-driven slice; nothing in this file anticipates it.

**Every gate runs, on every candidate.** `check` returns the empty list when all passed,
so a candidate that fails schema validity must still flow through the remaining gates
without raising. The coercions in `check` exist for exactly that: a model that answered
`{"hook": 5, "body": None}` gets findings, not a `TypeError` in the caller's request.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from app.models.template import Template

# Every gate name this module can emit. A caller routing on `finding.gate` — and the
# tests — key on these, so a typo in one of the five gate functions is a name that is not
# in this tuple rather than a branch that silently never fires.
GATES = (
    "schema",
    "length",
    "visual_slot_type",
    "visual_slot_missing",
    "near_duplicate",
    "forbidden_claim",
)

# LinkedIn refuses a post longer than this. Checked here, on a candidate, so the refusal
# costs nothing — by the time `publishing.push_draft` sends it the media is already
# uploaded and the URL already stored.
POST_MAX_CHARS = 3000

# Below this, a completion was truncated or refused — not written short. Deliberately far
# under anything real: the posts this corpus was extracted from average ~818 characters
# (see `extraction`), so no genuine draft comes near it. Judging whether a post is *thin*
# is craft, and craft belongs to the rubric slice, not to a deterministic gate.
POST_MIN_CHARS = 80

# How alike two posts have to read before the candidate counts as a restatement.
#
# Measured on post-length prose rather than picked. Against the fixtures in
# `tests/test_gates.py`: a restatement with two phrases swapped scores 0.95, the same
# argument rewritten in different words scores 0.49, and an unrelated post of the same
# length scores 0.23. 0.80 sits in the empty band between the first two, and both ends are
# pinned by tests — moving this number breaks one of them in whichever direction it moves.
#
# The 0.49 case is the interesting one and it is deliberately *not* a duplicate: writing a
# second post on a subject is not restating the first, and a threshold low enough to catch
# it would block honest drafts on the topics this account writes about most.
#
# ponytail: a character-level ratio over whole posts. Ceiling: it will miss a candidate
# that restates one section of a much longer post, because the surrounding difference
# drowns the overlap. The upgrade is shingles or embeddings, and it needs a corpus big
# enough to tune against — there are zero posts with recorded lineage.
NEAR_DUPLICATE_RATIO = 0.80

# The slot types a model may write prose into.
#
# **Restated from `generation.WRITABLE_SLOT_TYPES` rather than imported**, and only
# because importing `generation` executes `prompts.get(...)` at import time — this module
# would then need the prompt registry on disk to check a candidate's length. Keep the two
# sets identical; widening one alone means a slot this gate calls complete is one the
# generator never filled.
_WRITABLE_SLOT_TYPES = frozenset({"text", "number"})

# Claim *shapes*, not words. Blueprint §11: an editorial grade means ready for review,
# never likely to perform, so a candidate asserting the product can predict engagement
# contradicts the one thing the whole evaluation stage promises not to do.
#
# Each pattern is confined within one *clause* — `_CLAUSE` excludes the sentence enders
# and the comma/semicolon. Both exclusions were earned. Stopping at `.` keeps "This will
# help. Engagement is hard." from reading as a promise; stopping at `,` keeps "We will get
# you an answer by Friday, whatever the reach of this post" from doing the same, which the
# first draft of this gate flagged and `test_ordinary_prose_about_engagement_is_not_a_
# finding` caught. A gate that fires on innocent writing gets switched off, which is worse
# than not having it.
#
# ponytail: shape matching, so a claim phrased around a comma escapes it. Ceiling is the
# model-driven rubric slice; this is the deterministic floor, not the whole answer.
_CLAUSE = r"[^.!?\n,;]"

_FORBIDDEN_CLAIMS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            rf"\b(will|guaranteed to|going to)\b{_CLAUSE}{{0,60}}?"
            rf"\b(get|earn|drive|generate|bring|deliver|boost|double|triple)\b{_CLAUSE}{{0,40}}?"
            r"\b(engagement|impressions|views|likes|comments|followers|reach|leads)\b",
            re.IGNORECASE,
        ),
        "promises a future engagement outcome",
    ),
    (
        re.compile(
            rf"\b(will|guaranteed to|going to)\b{_CLAUSE}{{0,60}}?\bgo viral\b",
            re.IGNORECASE,
        ),
        "promises virality",
    ),
    (
        re.compile(
            rf"\b\d[\d.,]*\s*(x|%)\s*(more|higher|better|increase in)\b{_CLAUSE}{{0,20}}?"
            r"\b(engagement|impressions|views|likes|followers|reach)\b",
            re.IGNORECASE,
        ),
        "promises a quantified engagement lift",
    ),
    (
        re.compile(
            rf"\bthis (post|draft|hook|template)\b{_CLAUSE}{{0,40}}?\bwill\b{_CLAUSE}{{0,40}}?"
            r"\b(perform|outperform|convert|engagement|reach)\b",
            re.IGNORECASE,
        ),
        "predicts this post's own performance",
    ),
)


@dataclass(frozen=True)
class Finding:
    """One reason a candidate did not pass, named so a caller can act on it.

    Two fields, and there is deliberately no third. A `score`, `severity` or `weight`
    here would make findings sortable, and a sorted list of findings is a ranking — the
    thing `CLAUDE.md` forbids, arriving through the back door of a review screen that
    shows the "worst" problems first. Evidence goes in `detail`, as words.
    """

    gate: str  # one of `GATES`; stable, machine-readable
    detail: str  # what is wrong, specifically enough to fix


def check(
    candidate: Mapping[str, Any],
    *,
    template: Template,
    recent_posts: Sequence[str],
    asset_values: Mapping[str, str] | None = None,
) -> list[Finding]:
    """Every hard gate over one candidate. An empty list means all of them passed.

    `candidate` is the structured object the model returned — what `generation` calls
    `written`: `hook`, `body`, and `visual_values` for the slots it was allowed to write.
    Gated *before* a `Draft` exists, which is why this takes the raw mapping: by the time
    the values are on a row they have already been coerced to strings and filtered, so the
    schema gate would have nothing left to catch.

    `template` is the VISUAL template the candidate was written against — the only one of
    the three that declares slots.

    `asset_values` is slot name -> asset id, the image slots' side of the picture. The
    model never writes those (see `generation.WRITABLE_SLOT_TYPES`), so completeness is
    judged against this dict, and against each slot's own `default_asset_id` where this
    dict is silent — exactly as `generation.chosen_assets` settles them. Reading the
    default here is what lets an unattended run, which picks no assets at all, pass.

    `template` and `recent_posts` take no default, deliberately, for the reason
    `generation.lesson_lines` states about `lessons`: a default lets a caller quietly stop
    passing them with nothing failing. A caller with no recent corpus passes `[]` and says
    so at the call site.
    """
    assets = asset_values or {}

    # A model that answered with a list, or with a bare string, is a schema finding — not
    # an `AttributeError` on `.get` before any gate has run. `_schema` reports it; every
    # gate after this reads the empty mapping and reports its own absence.
    data: Mapping[str, Any] = candidate if isinstance(candidate, Mapping) else {}

    # Coerced once, here, exactly as `generation.generate_draft` coerces the same keys
    # onto a `Draft`. Two reasons, and both matter: the gates below then measure the
    # string that would actually publish, and none of them can raise on a model that
    # answered with the wrong type — the schema gate has already reported that, and a
    # `TypeError` from a later gate would hide it behind a 500.
    hook = str(data.get("hook") or "").strip()
    body = str(data.get("body") or "").strip()
    supplied = data.get("visual_values")
    written: Mapping[str, Any] = supplied if isinstance(supplied, Mapping) else {}

    # Identical to `Draft.full_text`, which is the string `publishing.push_draft` sends as
    # the post's `content`. If those two ever drift, this gate measures text that never
    # publishes and passes candidates the platform rejects.
    full_text = f"{hook}\n\n{body}".strip()

    return [
        *_schema(candidate, template),
        *_length(full_text),
        *_visual_slots(template, written, assets),
        *_near_duplicate(full_text, recent_posts),
        *_forbidden_claims(full_text, written),
    ]


def _schema(candidate: Mapping[str, Any], template: Template) -> list[Finding]:
    """The keys the template needs, of the right types.

    Extra keys are not a finding. A model volunteers a value for every slot it can see,
    including image URLs it cannot know, and `generation._written_values` drops those —
    reporting them here would fail a candidate for something already handled correctly.
    """
    findings = []

    if not isinstance(candidate, Mapping):
        # A non-object answer. Reported rather than raised so the rest still runs; the
        # coercions in `check` have already turned it into empty text for them.
        return [Finding("schema", f"candidate is not an object: {type(candidate).__name__}")]

    for key in ("hook", "body"):
        value = candidate.get(key)
        if value is None:
            findings.append(Finding("schema", f"missing key {key!r}"))
        elif not isinstance(value, str):
            findings.append(
                Finding("schema", f"{key!r} must be a string, got {type(value).__name__}")
            )
        elif not value.strip():
            findings.append(Finding("schema", f"{key!r} is empty"))

    supplied = candidate.get("visual_values")
    writable = [
        str(slot.get("name") or "")
        for slot in template.slots
        # `.get("type")`, never `slot["type"]` — VISUAL rows authored before `type`
        # existed carry no key at all, and `_visual_slots` reports those separately.
        if str(slot.get("type") or "") in _WRITABLE_SLOT_TYPES
    ]
    if supplied is None:
        if writable:
            findings.append(
                Finding(
                    "schema",
                    f"missing key 'visual_values'; {template.name} declares writable "
                    f"slots {sorted(writable)}",
                )
            )
    elif not isinstance(supplied, Mapping):
        findings.append(
            Finding(
                "schema",
                f"'visual_values' must be an object, got {type(supplied).__name__}",
            )
        )
    else:
        for name, value in supplied.items():
            # A slot renders as text. A dict or a list would reach the markup as
            # `{'a': 1}`, which renders successfully and reads as debris.
            if not isinstance(value, str | int | float) or isinstance(value, bool):
                findings.append(
                    Finding(
                        "schema",
                        f"visual_values[{name!r}] must be text or a number, "
                        f"got {type(value).__name__}",
                    )
                )
    return findings


def _length(full_text: str) -> list[Finding]:
    """What LinkedIn will accept, phrased the way `main` phrases the verdict-note cap."""
    size = len(full_text)
    if size > POST_MAX_CHARS:
        return [
            Finding(
                "length",
                f"post is {size} characters; LinkedIn's cap is {POST_MAX_CHARS}",
            )
        ]
    if size < POST_MIN_CHARS:
        return [
            Finding(
                "length",
                f"post is {size} characters; below {POST_MIN_CHARS} the completion was "
                "truncated or refused rather than written short",
            )
        ]
    return []


def _visual_slots(
    template: Template, written: Mapping[str, Any], assets: Mapping[str, str]
) -> list[Finding]:
    """Every slot the visual declares has a value — read off the declared `type`.

    **Never off the slot's name.** `left_image_url` reads as an asset and `subject`,
    `logo` and `hero` do not, so a name heuristic looks for the value in the wrong dict
    and reports a filled slot as empty (or, worse, an empty one as filled). This is a
    `CLAUDE.md` rule because it has already cost this project a draft rendered with empty
    boxes that reported success.

    **An untyped slot is its own finding, not a default.** Treating absent as writable is
    exactly how `stat-hero` v1 — approved with four untyped slots, two of them an
    `<img src>` — produced that empty-box draft. Absent means unknown, and unknown here
    cannot be judged complete or incomplete, so it is reported as what it is.
    """
    findings = []
    for slot in template.slots:
        name = str(slot.get("name") or "")
        declared = str(slot.get("type") or "").strip()

        if not declared:
            findings.append(
                Finding(
                    "visual_slot_type",
                    f"{template.name} slot {name!r} declares no type, so whether it is "
                    "filled cannot be established — give it a type",
                )
            )
            continue

        if declared == "image_url":
            # Settled the way `generation.chosen_assets` settles it: the pick, else the
            # slot's own `default_asset_id`. Reading the default here is what lets an
            # unattended run — which supplies no picks at all — pass this gate, and it
            # also means a caller passing raw picks rather than settled values gets the
            # same answer as one passing settled values.
            default = slot.get("default_asset_id")
            value = (
                str(assets.get(name) or "").strip()
                or str(default if default is not None else "").strip()
            )
        elif declared in _WRITABLE_SLOT_TYPES:
            value = str(written.get(name) or "").strip()
        else:
            # A type nothing in this codebase declares. Which side fills it is unknown, so
            # a value from either counts — the alternative is failing every template that
            # adds a type before this module learns about it.
            value = str(written.get(name) or assets.get(name) or "").strip()

        if not value:
            findings.append(
                Finding(
                    "visual_slot_missing",
                    f"{template.name} slot {name!r} (type {declared!r}) has no value",
                )
            )
    return findings


def _near_duplicate(full_text: str, recent_posts: Sequence[str]) -> list[Finding]:
    """The candidate restates something recently posted.

    `recent_posts` is text, and the caller chooses the window — this module does not query
    the database, so "recent" is a decision made where the query lives rather than a
    constant buried here.

    The first post over the threshold is reported and the scan stops. Reporting *the
    closest* match would mean taking a maximum over similarity scores, which is a ranking
    dressed as a detail line; the finding only has to be true and actionable, and the
    first restatement found is both.
    """
    candidate = _normalised(full_text)
    if not candidate:
        # Not a tidy early return. `SequenceMatcher` scores two empty strings **1.0**, so a
        # candidate the schema gate has already rejected as empty would be reported as a
        # perfect duplicate of any empty string in the recent window — a second, false
        # finding on top of the true one. The mirror guard (skipping an empty *post*) is
        # not needed: an empty post against real text scores 0.0 and fails the threshold on
        # its own, and a redundant second guard is one no test could ever distinguish.
        return []
    for post in recent_posts:
        other = _normalised(post)
        # `autojunk=False` is deliberate. The default marks any character appearing in
        # more than 1% of a sequence longer than 200 as junk, which for prose means the
        # ratio depends on the letter frequencies of whichever post it is compared
        # *against* — the same candidate scores differently against two posts for reasons
        # that have nothing to do with how alike they read. That is not something a fixed
        # threshold can be calibrated on, whichever way the number happens to move.
        ratio = SequenceMatcher(None, candidate, other, autojunk=False).ratio()
        if ratio >= NEAR_DUPLICATE_RATIO:
            excerpt = post.strip()[:60]
            return [
                Finding(
                    "near_duplicate",
                    f"reads {ratio:.2f} alike to a recent post ({NEAR_DUPLICATE_RATIO} is "
                    f'the limit): "{excerpt}…"',
                )
            ]
    return []


def _normalised(text: str) -> str:
    """Lowercased, whitespace collapsed — so a reflow is not mistaken for a rewrite."""
    return " ".join(text.lower().split())


def _forbidden_claims(full_text: str, written: Mapping[str, Any]) -> list[Finding]:
    """Claim shapes the product may not make.

    The visual's written values are scanned as well as the post body. Slot text is
    rendered into the image and published with the post, so a `big_number` slot reading
    "10x more engagement" is the same claim reaching the same audience — and it is the one
    a reviewer skims past, because it is a picture.
    """
    findings = []
    sources: list[tuple[str, str]] = [("post text", full_text)]
    sources.extend(
        (f"visual slot {name!r}", value)
        for name, value in written.items()
        if isinstance(value, str)
    )

    for where, text in sources:
        for pattern, why in _FORBIDDEN_CLAIMS:
            match = pattern.search(text)
            if match:
                findings.append(
                    Finding(
                        "forbidden_claim",
                        f'{where} {why}: "{match.group(0).strip()}" — this product '
                        "cannot predict engagement and may not say it can",
                    )
                )
    return findings
