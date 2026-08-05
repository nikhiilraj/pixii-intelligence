"""The deterministic gates. No database, no model, no fixtures — see `app.gates`.

Every test here constructs its `Template` in memory. That is not a shortcut: the gates are
pure by design, and a test that needed a session would be evidence one of them had reached
for something it must not have.
"""

import dataclasses

import pytest

from app.gates import (
    GATES,
    NEAR_DUPLICATE_RATIO,
    POST_MAX_CHARS,
    POST_MIN_CHARS,
    Finding,
    check,
)
from app.models.template import Template, TemplateKind

# A real-length post. The gates measure `hook + "\n\n" + body`, so anything shorter than
# `POST_MIN_CHARS` would trip the length gate in tests that are about something else.
BODY = (
    "We rebuilt the intake form last quarter and the thing that moved the needle was not "
    "the copy. It was removing two fields nobody in support had ever read. Three weeks of "
    "argument about button colour, and the fix was deletion. If you are staring at a form "
    "that converts badly, count the fields you cannot name a reader for. That number is "
    "usually the answer, and it is usually larger than anyone wants to admit out loud."
)


def a_visual(slots: list[dict], name: str = "stat-hero") -> Template:
    """A VISUAL template with the given slots, never written to a database."""
    return Template(
        family_id="fam-1", version=1, kind=TemplateKind.VISUAL, name=name, slots=slots
    )


def a_candidate(**overrides) -> dict:
    candidate: dict = {"hook": "Deletion beat design.", "body": BODY, "visual_values": {}}
    candidate.update(overrides)
    return candidate


def gates_of(findings: list[Finding]) -> list[str]:
    return [f.gate for f in findings]


def test_a_clean_candidate_produces_no_findings():
    visual = a_visual([{"name": "headline", "type": "text"}])
    assert (
        check(
            a_candidate(visual_values={"headline": "Deletion"}),
            template=visual,
            recent_posts=[],
        )
        == []
    )


# --- the interface itself -------------------------------------------------------------


def test_finding_carries_no_score_and_cannot_be_mutated():
    """A third field would make findings sortable, and sorted findings are a ranking.

    `CLAUDE.md`: never rank. This is the assertion that fails the moment someone adds a
    `score`, `severity` or `weight` to make a review screen show the worst problems first.
    """
    assert {f.name for f in dataclasses.fields(Finding)} == {"gate", "detail"}
    with pytest.raises(dataclasses.FrozenInstanceError):
        Finding("schema", "x").gate = "length"  # type: ignore[misc]


def test_every_gate_a_candidate_can_trip_is_named_in_GATES():
    """A gate whose name is not in `GATES` is one no caller can route on."""
    visual = a_visual(
        [
            {"name": "headline", "type": "text"},
            {"name": "untyped"},
            {"name": "logo", "type": "image_url"},
        ]
    )
    findings = check(
        {"hook": "", "body": "This post will drive engagement.", "visual_values": []},
        template=visual,
        recent_posts=["This post will drive engagement."],
    )
    assert findings, "the everything-wrong candidate should trip gates"
    assert {f.gate for f in findings} <= set(GATES)
    assert all(f.detail.strip() for f in findings), "a finding with no detail is not actionable"


def test_the_same_arguments_give_the_same_findings():
    visual = a_visual([{"name": "headline", "type": "text"}])
    args = dict(template=visual, recent_posts=[BODY])
    assert check(a_candidate(), **args) == check(a_candidate(), **args)


# --- schema validity ------------------------------------------------------------------


def test_missing_and_empty_text_keys_are_schema_findings():
    visual = a_visual([])
    assert "schema" in gates_of(check({"body": BODY}, template=visual, recent_posts=[]))
    assert "schema" in gates_of(
        check({"hook": "  ", "body": BODY}, template=visual, recent_posts=[])
    )


def test_a_key_of_the_wrong_type_is_a_schema_finding():
    visual = a_visual([])
    findings = check({"hook": 5, "body": BODY}, template=visual, recent_posts=[])
    assert "schema" in gates_of(findings)
    assert any("string" in f.detail for f in findings if f.gate == "schema")


def test_visual_values_must_be_an_object_and_hold_renderable_values():
    visual = a_visual([{"name": "headline", "type": "text"}])
    assert "schema" in gates_of(
        check(a_candidate(visual_values="headline"), template=visual, recent_posts=[])
    )
    findings = check(
        a_candidate(visual_values={"headline": {"text": "x"}}), template=visual, recent_posts=[]
    )
    assert any(f.gate == "schema" and "headline" in f.detail for f in findings)


def test_visual_values_is_required_when_the_template_declares_writable_slots():
    with_slot = a_visual([{"name": "headline", "type": "text"}])
    findings = check(
        {"hook": "Deletion beat design.", "body": BODY}, template=with_slot, recent_posts=[]
    )
    assert any(f.gate == "schema" and "visual_values" in f.detail for f in findings)

    # ...and not when it declares none. A template with nothing to write must not demand
    # the key, or every image-only visual fails a gate about text it has no slot for.
    image_only = a_visual([{"name": "logo", "type": "image_url", "default_asset_id": 7}])
    assert (
        check(
            {"hook": "Deletion beat design.", "body": BODY},
            template=image_only,
            recent_posts=[],
        )
        == []
    )


def test_extra_keys_the_model_volunteered_are_not_findings():
    """`generation._written_values` already drops these. Failing here fails a candidate
    for something the pipeline handles correctly."""
    visual = a_visual([{"name": "headline", "type": "text"}])
    findings = check(
        a_candidate(visual_values={"headline": "Deletion"}, confidence="high"),
        template=visual,
        recent_posts=[],
    )
    assert findings == []


@pytest.mark.parametrize(
    "candidate",
    [
        {},
        {"hook": 5, "body": None},
        {"hook": "Deletion beat design.", "body": BODY, "visual_values": "nope"},
        {"hook": "Deletion beat design.", "body": BODY, "visual_values": ["headline"]},
        ["hook", "body"],
        "just a string",
    ],
)
def test_a_malformed_candidate_produces_findings_rather_than_raising(candidate):
    """Every gate runs on every candidate, so schema failure must not stop the rest.

    A `TypeError` escaping here reaches the caller as a 500 and hides the schema finding
    that explains it.
    """
    visual = a_visual([{"name": "headline", "type": "text"}, {"name": "logo", "type": "image_url"}])
    findings = check(candidate, template=visual, recent_posts=[BODY])
    assert "schema" in gates_of(findings)
    assert "visual_slot_missing" in gates_of(findings)


# --- length ---------------------------------------------------------------------------


def test_a_post_over_linkedins_cap_is_a_finding():
    visual = a_visual([])
    findings = check(
        a_candidate(body="word " * (POST_MAX_CHARS // 2)), template=visual, recent_posts=[]
    )
    assert "length" in gates_of(findings)
    assert any(str(POST_MAX_CHARS) in f.detail for f in findings if f.gate == "length")


def test_a_post_at_the_cap_passes():
    """The boundary, so a `>` that should be `>=` — or the reverse — is visible."""
    visual = a_visual([])
    hook = "Deletion."
    body = "x" * (POST_MAX_CHARS - len(hook) - 2)
    findings = check(
        {"hook": hook, "body": body, "visual_values": {}}, template=visual, recent_posts=[]
    )
    assert findings == []


def test_a_truncated_completion_is_a_finding():
    visual = a_visual([])
    findings = check(
        {"hook": "We rebuilt the", "body": "intake", "visual_values": {}},
        template=visual,
        recent_posts=[],
    )
    assert "length" in gates_of(findings)
    assert any(str(POST_MIN_CHARS) in f.detail for f in findings if f.gate == "length")


def test_length_measures_hook_and_body_together():
    """`Draft.full_text` is what `publishing.push_draft` sends as the post content. A gate
    measuring only the body would pass a candidate the platform rejects."""
    visual = a_visual([])
    half = "x" * (POST_MAX_CHARS - 100)
    findings = check(
        {"hook": half, "body": half, "visual_values": {}}, template=visual, recent_posts=[]
    )
    assert "length" in gates_of(findings)


# --- visual-slot completeness ---------------------------------------------------------


def test_slots_are_read_by_declared_type_not_by_name():
    """Both directions in one template, because either alone is escapable.

    `left_image_url` is declared `text` and filled by the model; `hero` is declared
    `image_url` and filled by an asset. A name heuristic looks in the wrong dict for both
    and reports two filled slots as empty.
    """
    visual = a_visual(
        [
            {"name": "left_image_url", "type": "text"},
            {"name": "hero", "type": "image_url"},
        ]
    )
    findings = check(
        a_candidate(visual_values={"left_image_url": "Deletion beat design"}),
        template=visual,
        recent_posts=[],
        asset_values={"hero": "12"},
    )
    assert findings == []


def test_a_writable_slot_with_no_written_value_is_a_finding():
    visual = a_visual(
        [{"name": "headline", "type": "text"}, {"name": "big_number", "type": "number"}]
    )
    findings = check(
        a_candidate(visual_values={"headline": "Deletion"}), template=visual, recent_posts=[]
    )
    assert [f.gate for f in findings] == ["visual_slot_missing"]
    assert "big_number" in findings[0].detail


def test_an_image_slot_with_no_asset_is_a_finding():
    visual = a_visual([{"name": "hero", "type": "image_url"}])
    findings = check(a_candidate(), template=visual, recent_posts=[])
    assert [f.gate for f in findings] == ["visual_slot_missing"]
    assert "hero" in findings[0].detail


def test_an_image_slot_falls_back_to_its_default_asset():
    """What lets an unattended run — which picks no assets at all — pass this gate.
    `generation.chosen_assets` settles the same way; a gate that did not would fail every
    autonomous draft."""
    visual = a_visual([{"name": "logo", "type": "image_url", "default_asset_id": 7}])
    assert check(a_candidate(), template=visual, recent_posts=[]) == []


def test_a_picked_asset_wins_over_the_default():
    visual = a_visual([{"name": "logo", "type": "image_url", "default_asset_id": 7}])
    assert check(a_candidate(), template=visual, recent_posts=[], asset_values={"logo": "9"}) == []


def test_written_prose_does_not_complete_an_image_slot():
    """The empty-box failure. A model volunteers a sentence for `hero`; that sentence
    renders as an `<img src>` of prose, reports success, and publishes a blank box."""
    visual = a_visual([{"name": "hero", "type": "image_url"}])
    findings = check(
        a_candidate(visual_values={"hero": "a photo of a laptop"}),
        template=visual,
        recent_posts=[],
    )
    assert "visual_slot_missing" in gates_of(findings)


def test_an_untyped_slot_is_reported_rather_than_assumed():
    """`stat-hero` v1 was approved with four untyped slots, two of them an `<img src>`.

    Absent means unknown. This must not crash — `slot["type"]` instead of `.get("type")`
    raises `KeyError` here — and must not quietly treat the slot as writable.
    """
    visual = a_visual([{"name": "hero", "example": "a chart"}])
    findings = check(
        a_candidate(visual_values={"hero": "anything at all"}), template=visual, recent_posts=[]
    )
    assert [f.gate for f in findings] == ["visual_slot_type"]
    assert "hero" in findings[0].detail


def test_a_slot_typed_with_an_empty_string_is_also_untyped():
    visual = a_visual([{"name": "hero", "type": ""}])
    assert gates_of(check(a_candidate(), template=visual, recent_posts=[])) == ["visual_slot_type"]


def test_a_type_this_module_does_not_know_accepts_a_value_from_either_side():
    visual = a_visual([{"name": "published_on", "type": "date"}])
    assert check(
        a_candidate(visual_values={"published_on": "2026-08-05"}), template=visual, recent_posts=[]
    ) == []
    assert gates_of(check(a_candidate(), template=visual, recent_posts=[])) == [
        "visual_slot_missing"
    ]


def test_a_blank_written_value_does_not_complete_a_slot():
    visual = a_visual([{"name": "headline", "type": "text"}])
    findings = check(
        a_candidate(visual_values={"headline": "   "}), template=visual, recent_posts=[]
    )
    assert gates_of(findings) == ["visual_slot_missing"]


# --- near-duplicate -------------------------------------------------------------------

# The same argument as `BODY`, rewritten. It scores 0.49 and must NOT trip the gate:
# writing a second post about a subject is not restating the first, and a threshold low
# enough to catch this blocks every honest draft on the topics this account writes about
# most. This fixture is what makes `NEAR_DUPLICATE_RATIO` a calibrated number rather than
# one that only has to clear unrelated prose.
SAME_TOPIC = (
    "Nobody rewrote a single line of copy on that intake form. We deleted two fields the "
    "support team had never once looked at, and conversion moved. The month of debate "
    "about button colour produced nothing. Count the fields on your form that you cannot "
    "name a reader for; that count is almost always bigger than the team believes, and it "
    "is where to start."
)

# Two posts on unrelated subjects, both real length. This scores 0.23 — the easy end.
UNRELATED = (
    "Our warehouse team ran a stopwatch on the picking route for a week. The walking, not "
    "the scanning, was where the hours went. We moved eleven SKUs closer to the door and "
    "the shift got forty minutes shorter without anyone working faster. Measure the walk "
    "before you buy the software, because the software will not shorten the walk for you."
)


def test_a_restatement_of_a_recent_post_is_a_finding():
    visual = a_visual([])
    restated = BODY.replace("last quarter", "in the spring").replace("Three weeks", "Two weeks")
    findings = check(
        a_candidate(body=restated), template=visual, recent_posts=[UNRELATED, BODY]
    )
    assert "near_duplicate" in gates_of(findings)


def test_an_unrelated_post_of_the_same_length_is_not_a_duplicate():
    """The false-positive end. Without this, a threshold low enough to catch anything
    would pass its own test while blocking every real draft."""
    visual = a_visual([])
    findings = check(a_candidate(), template=visual, recent_posts=[UNRELATED])
    assert findings == []


def test_the_same_argument_written_differently_is_not_a_duplicate():
    """The near end of the band, at 0.49 — the case that actually constrains the
    threshold. A second post on a subject is not a restatement of the first."""
    visual = a_visual([])
    findings = check(a_candidate(body=SAME_TOPIC), template=visual, recent_posts=[BODY])
    assert findings == []


def test_reflowed_whitespace_and_case_do_not_hide_a_duplicate():
    visual = a_visual([])
    reflowed = "\n\n".join(BODY.upper().split(". "))
    findings = check(a_candidate(body=reflowed), template=visual, recent_posts=[BODY])
    assert "near_duplicate" in gates_of(findings)


def test_the_duplicate_finding_quotes_the_post_it_matched():
    visual = a_visual([])
    findings = check(a_candidate(), template=visual, recent_posts=[BODY])
    duplicate = next(f for f in findings if f.gate == "near_duplicate")
    assert BODY[:40] in duplicate.detail
    assert str(NEAR_DUPLICATE_RATIO) in duplicate.detail


def test_an_empty_recent_corpus_is_not_a_duplicate():
    visual = a_visual([])
    assert check(a_candidate(), template=visual, recent_posts=[]) == []
    assert check(a_candidate(), template=visual, recent_posts=["", "   "]) == []


# --- forbidden claims -----------------------------------------------------------------


@pytest.mark.parametrize(
    "sentence",
    [
        "Post this on a Tuesday and it will get you three times the engagement.",
        "This hook is guaranteed to drive impressions for any B2B account.",
        "Use this opener and your next post is going to go viral.",
        "Teams see 40% more engagement within a month of switching.",
        "This post will outperform anything else in your feed.",
    ],
)
def test_a_performance_promise_is_a_finding(sentence):
    """Blueprint §11: an editorial grade means ready for review, never likely to perform.
    A draft asserting the opposite contradicts the one promise the evaluation stage makes.
    """
    visual = a_visual([])
    findings = check(a_candidate(body=f"{BODY} {sentence}"), template=visual, recent_posts=[])
    assert "forbidden_claim" in gates_of(findings)


@pytest.mark.parametrize(
    "sentence",
    [
        "This will help. Engagement is a lagging indicator anyway.",
        "We will get you an answer by Friday, whatever the reach of this post.",
        "Views on the recording doubled after we cut the intro.",
        "The best part of the redesign was deleting two fields.",
    ],
)
def test_ordinary_prose_about_engagement_is_not_a_finding(sentence):
    """The false-positive end. A gate that fires on innocent writing gets switched off,
    which is worse than not having it — so the patterns stay inside one sentence."""
    visual = a_visual([])
    findings = check(a_candidate(body=f"{BODY} {sentence}"), template=visual, recent_posts=[])
    assert findings == []


def test_a_claim_inside_a_visual_slot_is_a_finding():
    """Slot text is rendered into the image and published with the post. It is also the
    text a reviewer skims past, because it is a picture."""
    visual = a_visual([{"name": "headline", "type": "text"}])
    findings = check(
        a_candidate(visual_values={"headline": "Guaranteed to drive 3x more engagement"}),
        template=visual,
        recent_posts=[],
    )
    assert "forbidden_claim" in gates_of(findings)
    assert any("headline" in f.detail for f in findings if f.gate == "forbidden_claim")
