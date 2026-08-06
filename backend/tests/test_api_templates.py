from datetime import datetime

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.config import settings
from app.db import get_session
from app.main import app


def client_with(session: Session) -> TestClient:
    app.dependency_overrides[get_session] = lambda: session
    return TestClient(app)


HOOK = {
    "kind": "hook",
    "name": "transformation",
    "body": {"pattern": "{small_value} turned into {large_value} {unit}"},
    "slots": [{"name": "small_value", "example": "$450"}],
    "provenance": ["6a26f1402b2567671a2daa62"],
}


def test_authoring_a_template_returns_it_awaiting_approval(session):
    body = client_with(session).post("/templates", json=HOOK).json()

    assert body["version"] == 1
    assert body["status"] == "proposed"
    assert body["kind"] == "hook"
    app.dependency_overrides.clear()


def test_revising_returns_a_new_version_and_keeps_the_old_one(session):
    client = client_with(session)
    original = client.post("/templates", json=HOOK).json()

    revised = client.put(f"/templates/{original['id']}", json={"name": "renamed"}).json()

    assert revised["version"] == 2
    assert revised["name"] == "renamed"
    versions = client.get(f"/templates/{revised['id']}/versions").json()
    assert [v["version"] for v in versions] == [1, 2]
    assert versions[0]["name"] == "transformation"
    app.dependency_overrides.clear()


def test_approving_makes_a_template_usable_for_generation(session):
    client = client_with(session)
    created = client.post("/templates", json=HOOK).json()

    assert client.get("/templates?kind=hook&usable_only=true").json() == []

    client.post(f"/templates/{created['id']}/approve")

    usable = client.get("/templates?kind=hook&usable_only=true").json()
    assert [t["name"] for t in usable] == ["transformation"]
    app.dependency_overrides.clear()


def test_retiring_removes_it_from_use_but_not_from_the_record(session):
    client = client_with(session)
    created = client.post("/templates", json=HOOK).json()
    client.post(f"/templates/{created['id']}/approve")

    client.post(f"/templates/{created['id']}/retire")

    assert client.get("/templates?kind=hook&usable_only=true").json() == []
    assert client.get(f"/templates/{created['id']}/versions").json()[0]["status"] == "retired"
    app.dependency_overrides.clear()


def test_revising_a_retired_template_is_rejected(session):
    client = client_with(session)
    created = client.post("/templates", json=HOOK).json()
    client.post(f"/templates/{created['id']}/retire")

    response = client.put(f"/templates/{created['id']}", json={"name": "resurrect"})

    assert response.status_code == 409
    app.dependency_overrides.clear()


def test_an_unknown_template_is_not_found(session):
    response = client_with(session).get("/templates/999999/versions")

    assert response.status_code == 404
    # Not the status alone: an unrouted path is a 404 as well, so this passed with the route
    # renamed away. The detail is what says `_load` refused it.
    assert response.json()["detail"] == "no template 999999"
    app.dependency_overrides.clear()


def test_listing_shows_only_the_current_version_of_each_family(session):
    client = client_with(session)
    created = client.post("/templates", json=HOOK).json()
    client.put(f"/templates/{created['id']}", json={"name": "v2"})

    listed = client.get("/templates?kind=hook").json()

    assert [t["name"] for t in listed] == ["v2"]
    app.dependency_overrides.clear()


def test_extraction_endpoint_creates_proposals_awaiting_approval(session):
    from app.deps import get_llm
    from app.models.post import Post

    session.add(
        Post(
            zernio_id="win-1",
            platform="linkedin",
            content="A hook.",
            engaged_actions=185,
            account_username=settings.voice_account,
            published_at=datetime(2026, 6, 1),
        )
    )
    session.flush()

    app.dependency_overrides[get_llm] = lambda: _FakeLLM(
        {
            "hooks": [
                {
                    "name": "transformation",
                    "pattern": "{a} turned into {b}",
                    "tone": "plain",
                    "source_post_ids": ["win-1"],
                }
            ]
        }
    )
    client = client_with(session)

    body = client.post("/templates/extract/hooks").json()

    assert [t["status"] for t in body] == ["proposed"]
    assert body[0]["provenance"] == ["win-1"]
    assert body[0]["body"]["cohort"] == "voice"
    assert client.get("/templates?kind=hook&usable_only=true").json() == []
    app.dependency_overrides.clear()


def test_the_extraction_endpoint_can_be_pointed_at_the_inspiration_cohort(session):
    from app.deps import get_llm
    from app.models.post import Post

    session.add(
        Post(
            zernio_id="theirs",
            platform="linkedin",
            content="Someone else's hook.",
            engaged_actions=1240,
            account_username=settings.inspiration_account,
            published_at=datetime(2026, 6, 1),
        )
    )
    session.flush()

    app.dependency_overrides[get_llm] = lambda: _FakeLLM(
        {
            "hooks": [
                {
                    "name": "borrowed",
                    "pattern": "{a} turned into {b}",
                    "source_post_ids": ["theirs"],
                }
            ]
        }
    )
    client = client_with(session)

    body = client.post("/templates/extract/hooks?cohort=inspiration").json()

    assert [t["body"]["cohort"] for t in body] == ["inspiration"]
    app.dependency_overrides.clear()


def test_a_model_that_returns_the_wrong_shape_is_reported_not_silently_ignored(session):
    from app.deps import get_llm
    from app.models.post import Post

    session.add(
        Post(
            zernio_id="win-1",
            platform="linkedin",
            content="A hook.",
            engaged_actions=1,
            account_username=settings.voice_account,
            published_at=datetime(2026, 6, 1),
        )
    )
    session.flush()
    app.dependency_overrides[get_llm] = lambda: _FakeLLM({"nope": []})

    response = client_with(session).post("/templates/extract/hooks")

    assert response.status_code == 502
    app.dependency_overrides.clear()


def test_one_unusable_proposal_does_not_turn_the_whole_batch_into_a_502(session):
    """The 502 above is the *batch* contract; a single bad proposal is not a batch failure.

    Route-level rather than unit-level because that is where the difference was paid: the
    extract route maps `ExtractionError` to 502, so a per-proposal error raised as one lost
    every sibling and returned nothing to the reviewer. Hooks only — the route's error
    handling is identical for structures, and the per-kind logic is covered in
    `test_extraction.py` and `test_structures.py`.
    """
    from app.deps import get_llm
    from app.models.post import Post

    session.add(
        Post(
            zernio_id="win-1",
            platform="linkedin",
            content="A hook.",
            engaged_actions=185,
            account_username=settings.voice_account,
            published_at=datetime(2026, 6, 1),
        )
    )
    session.flush()
    app.dependency_overrides[get_llm] = lambda: _FakeLLM(
        {
            "hooks": [
                {"name": "patternless"},
                {
                    "name": "transformation",
                    "pattern": "{a} turned into {b}",
                    "source_post_ids": ["win-1"],
                },
            ]
        }
    )

    response = client_with(session).post("/templates/extract/hooks")

    assert response.status_code == 201
    assert [t["name"] for t in response.json()] == ["transformation"]
    app.dependency_overrides.clear()


def test_the_structures_route_offers_focus_and_no_sample_size():
    """`sample_size` was deleted, and the contract is the only place a deletion is visible.

    FastAPI ignores an unknown query parameter rather than refusing it, so a caller still
    sending `?sample_size=27` gets a 201 and no hint that the number meant nothing — which is
    exactly the silent no-op the parameter was deleted for. The published contract is what
    tells them, so it is what this pins; re-adding the parameter fails here.

    `focus` is asserted in the same breath because the two are easy to delete together: it is
    a real request the sample size was only ever standing in for, and `test_structures.py`
    covers that it reaches the prompt.
    """
    parameters = app.openapi()["paths"]["/templates/extract/structures"]["post"]["parameters"]
    names = {p["name"] for p in parameters}

    assert "sample_size" not in names
    assert "focus" in names


class _FakeLLM:
    def __init__(self, response: dict):
        self.response = response

    def complete_json(self, system: str, user: str, images=()) -> dict:
        return self.response


def test_a_failing_render_service_is_reported_as_upstream_not_as_our_crash(session):
    """A 429 from the renderer must not surface as an opaque 500."""
    import httpx

    from app.deps import get_html_renderer
    from app.models.template import Template, TemplateKind

    template = Template(
        family_id="f",
        kind=TemplateKind.VISUAL,
        name="stat-hero",
        body={"renderer": "html", "html": "<b>{headline}</b>"},
    )
    session.add(template)
    session.flush()

    class RateLimited:
        def screenshot(self, html: str, width: int, height: int) -> bytes:
            raise httpx.HTTPStatusError(
                "429", request=httpx.Request("POST", "https://x.test"),
                response=httpx.Response(429),
            )

    app.dependency_overrides[get_html_renderer] = lambda: RateLimited()

    response = client_with(session).post(
        f"/templates/{template.id}/preview", json={"headline": "hi"}
    )

    assert response.status_code == 502
    assert "429" in response.json()["detail"]
    app.dependency_overrides.clear()


# `GET /templates/{id}/compatible-hooks` — the handler, not the function underneath it.
#
# `test_structures.py` covers `extraction.compatible_hooks` four times and never calls the
# route, which is the gap the README's 204-mutation audit found twice ("two routes had no HTTP
# coverage whatsoever"): a handler nothing exercises is a handler nobody has proven answers,
# and its 400 branch below was unreachable by any test in the suite. Every assertion here reads
# the status *and* the body — an unrouted path is a 404 as well, which is how six assertions in
# that audit passed against routes that did not exist.


def _paired(session, *, families: list[str]):
    """A structure declaring `families`, approved, the way extraction leaves one."""
    from app.models.template import TemplateKind
    from app.templates import approve, create_template

    structure = create_template(
        session,
        kind=TemplateKind.STRUCTURE,
        name="offer-reward",
        body={"sections": [{"name": "hook"}], "compatible_hook_families": families},
    )
    return approve(session, structure)


def _hook(session, name: str, *, approved: bool = True):
    from app.models.template import TemplateKind
    from app.templates import approve, create_template

    hook = create_template(session, kind=TemplateKind.HOOK, name=name, body={"pattern": "{a}"})
    return approve(session, hook) if approved else hook


def test_a_structures_pairings_resolve_to_the_newest_approved_version(session):
    """The route answers with `(family, newest approved version)` — never every version.

    The family is edited to v2 before the read, so a handler that resolved the pairing to the
    row it was recorded against would answer with v1, and one that resolved it to the family
    would answer with both. Lineage in this system is `(family_id, version)`; a pairing names
    only the family, so which version it means is the route's decision and it is worth pinning.
    """
    from app.templates import edit_template

    hook = _hook(session, "ai-time-value-equation")
    edit_template(session, hook, name="ai-time-value-equation-v2")
    structure = _paired(session, families=[hook.family_id])

    body = client_with(session).get(f"/templates/{structure.id}/compatible-hooks").json()

    assert [(t["name"], t["version"]) for t in body] == [("ai-time-value-equation-v2", 2)]
    app.dependency_overrides.clear()


def test_a_pairing_naming_only_unapproved_hooks_answers_with_an_empty_list(session):
    """200 and `[]` — a real state, and not the same answer as a failed read.

    This is the stale pairing the picker has to survive: extraction recorded a family, a human
    has since retired every version of it, and the structure still names it. The route drops
    it rather than offering generation a hook nobody approved.
    """
    from app.templates import retire

    retired = _hook(session, "transformation")
    retire(session, retired)
    proposed = _hook(session, "not-reviewed-yet", approved=False)
    structure = _paired(session, families=[retired.family_id, proposed.family_id])

    response = client_with(session).get(f"/templates/{structure.id}/compatible-hooks")

    assert response.status_code == 200
    assert response.json() == []
    # And the hooks themselves are still there — this is a pairing that resolves to nothing,
    # not a library that is empty.
    assert len(client_with(session).get("/templates?kind=hook").json()) == 2
    app.dependency_overrides.clear()


def test_a_structure_recording_no_pairing_answers_with_an_empty_list(session):
    structure = _paired(session, families=[])

    response = client_with(session).get(f"/templates/{structure.id}/compatible-hooks")

    assert response.status_code == 200
    assert response.json() == []
    app.dependency_overrides.clear()


def test_asking_a_hook_what_it_pairs_with_is_refused(session):
    """The 400 branch, which nothing exercised until this test.

    Only a structure carries `compatible_hook_families`; a hook or a visual would answer with
    an empty list forever, which reads as "nothing pairs with this" rather than as "that is not
    a question about this kind of template". The detail is asserted because the status alone is
    what a missing route also returns.
    """
    hook = _hook(session, "transformation")

    response = client_with(session).get(f"/templates/{hook.id}/compatible-hooks")

    assert response.status_code == 400
    assert response.json()["detail"] == "only a structure declares hook pairings"
    app.dependency_overrides.clear()


def test_asking_about_a_template_that_does_not_exist_is_not_found(session):
    response = client_with(session).get("/templates/999999/compatible-hooks")

    assert response.status_code == 404
    assert response.json()["detail"] == "no template 999999"
    app.dependency_overrides.clear()


def _voice_post(session, zid: str, content: str = "A hook.") -> None:
    from app.models.post import Post

    session.add(
        Post(
            zernio_id=zid,
            platform="linkedin",
            content=content,
            engaged_actions=100,
            account_username=settings.voice_account,
            published_at=datetime(2026, 6, 1),
        )
    )
    session.flush()


def test_the_uncovered_count_is_reported_per_cohort_and_per_kind(session):
    """The number beside the extract controls, and the only place it is answerable.

    144 of 236 posts were cited by nothing after the first corpus-wide run, and reading that
    meant writing SQL. Asserted as the whole object: a route that answered one cohort, or that
    reported the hook count under `structure`, would pass any looser check.
    """
    client = client_with(session)
    _voice_post(session, "covered")
    _voice_post(session, "orphan")
    hook = client.post(
        "/templates", json={**HOOK, "provenance": ["covered"]}
    ).json()
    assert hook["kind"] == "hook"

    body = client.get("/templates/uncovered").json()

    assert body == {
        "voice": {"hook": 1, "structure": 2},
        "inspiration": {"hook": 0, "structure": 0},
    }
    app.dependency_overrides.clear()


def test_a_zero_uncovered_count_is_reported_as_zero(session):
    """A measured zero: the loop finished. Not the `—` case, which is a value never collected.

    Stated on the wire because the page renders what arrives — an endpoint that omitted the
    key, or answered `null`, would make the dash the only thing a client could print.
    """
    client = client_with(session)

    assert client.get("/templates/uncovered").json()["voice"]["hook"] == 0
    app.dependency_overrides.clear()


def test_the_visual_kind_has_no_uncovered_count(session):
    """Visual extraction reads the strongest five posts that carry an image, so a corpus-wide
    uncovered count would describe a set that route never looks at."""
    client = client_with(session)

    assert "visual" not in client.get("/templates/uncovered").json()["voice"]
    app.dependency_overrides.clear()


def test_both_extract_routes_offer_the_uncovered_flag():
    """The contract, for the reason the `sample_size` test above gives: FastAPI ignores an
    unknown query parameter, so a client sending `uncovered_only=true` at a route that lost it
    would get a 201 and a full-corpus run with no hint that the flag meant nothing."""
    paths = app.openapi()["paths"]

    for route in ("/templates/extract/hooks", "/templates/extract/structures"):
        names = {p["name"] for p in paths[route]["post"]["parameters"]}
        assert "uncovered_only" in names, route
    # And never on visuals: that sample is five images, not the corpus.
    visuals = {p["name"] for p in paths["/templates/extract/visuals"]["post"]["parameters"]}
    assert "uncovered_only" not in visuals


def test_extracting_over_an_uncovered_set_reads_only_what_nothing_covers(session):
    from app.deps import get_llm

    client = client_with(session)
    _voice_post(session, "covered", content="Already spoken for.")
    _voice_post(session, "orphan", content="Nothing describes this.")
    client.post("/templates", json={**HOOK, "provenance": ["covered"]})

    llm = _RecordingLLM(
        {"hooks": [{"name": "leftover", "pattern": "{a} to {b}", "source_post_ids": ["orphan"]}]}
    )
    app.dependency_overrides[get_llm] = lambda: llm

    body = client.post("/templates/extract/hooks?uncovered_only=true").json()

    assert [t["provenance"] for t in body] == [["orphan"]]
    assert "Nothing describes this." in llm.user
    assert "Already spoken for." not in llm.user
    app.dependency_overrides.clear()


def test_an_uncovered_run_with_nothing_left_is_not_an_error(session):
    """The state the operator is running towards. `[]` and a 201, never a 502."""
    from app.deps import get_llm

    client = client_with(session)
    _voice_post(session, "covered")
    client.post("/templates", json={**HOOK, "provenance": ["covered"]})

    llm = _RecordingLLM({"hooks": []})
    app.dependency_overrides[get_llm] = lambda: llm

    response = client.post("/templates/extract/hooks?uncovered_only=true")

    assert response.status_code == 201
    assert response.json() == []
    # And it never reached the model: an empty sample is an answer, not a completion to buy.
    assert llm.user is None
    app.dependency_overrides.clear()


class _RecordingLLM(_FakeLLM):
    """`_FakeLLM`, keeping the prompt it was handed — which is where a narrowed sample shows."""

    user: str | None = None

    def complete_json(self, system: str, user: str, images=()) -> dict:
        self.user = user
        return self.response
