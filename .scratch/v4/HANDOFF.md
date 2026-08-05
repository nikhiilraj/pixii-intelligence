# Handoff — 2026-08-05, 12:25 IST

Read `docs/architecture/2026-08-05-implementation-versions.md` first. It is the plan, it is
kept accurate, and it records where implementation diverged from it and why.

**Gate at handoff: `make check` green — 1014 backend, 326 frontend, single migration head
`d4e9a17c3b02`.** Run it first anyway; see the warning immediately below.

---

## 1. The one thing to fix first

**`backend/app/revision.py` is 642 lines with ZERO tests.** `tests/test_revision.py` was
never written — the agent building v3 slice 6 stopped before committing and I secured its
files (`e21dfeb`).

The suite is green *because nothing exercises this module*, not because it works. Nobody has
run it. Its prompt is registered and reachable. Do not wire it into `generation` until it has
tests and a mutation battery.

What it was told to guarantee, so you know what to test for:

- A hard iteration ceiling **and** a hard spend ceiling, both server-side. A loop whose exit
  depends on the model deciding it is done is not bounded.
- Terminates on **no progress**, not only on success. A revision that fixes nothing, or
  trades one finding for another, must not buy another round forever.
- Re-runs the gates after revising and reports what still fails. Stopping at the ceiling with
  findings outstanding is `needs_attention`, never silent acceptance.
- Lineage does not move across a revision — same `(family_id, version)`.
- No ranking: findings are not scored, ordered by severity, or triaged "worst first".

---

## 2. What was built

| | Slices | State |
|---|---|---|
| **v0** data safety | 1/1 | Purge live on all branches. One human action left (§4). |
| **v1** daily slot + Teams | 3/3 | Shipped. **No card has ever reached a real channel.** |
| **v2** schedule/publish | 6/7 | Slice 7 blocked on a 2-minute probe (§4). |
| **v3** research brain | 7/7 | Slice 6 has no tests — see §1. |

**v3, slice by slice:** prompt registry + traces · hardened fetcher (SSRF) · research dossier
(jobs, sources, claims, citations) · deterministic hard gates · editorial brief + angle/claim
plan · editorial-readiness rubric · research API + UI · targeted revision loop *(untested)*.

Nine prompts are registered in `app/prompts/library.py`, each addressable only by exact
`(name, version)` — there is deliberately no "latest".

---

## 3. Verified vs merely committed

Everything below was independently mutation-checked by the lead, not taken from an agent's
report. **Three of those checks found a *test* wrong rather than code**, which is why the
practice is worth continuing:

- **Fetcher (SSRF).** Whole address check removed → 19 tests fail. Redirect re-validation,
  streaming body ceiling, script stripping, IPv4-mapped/6to4 unwrapping all caught.
  *Found:* a comment claiming `is_global` alone decides every case. It does not —
  `ip_address('224.0.0.1').is_global` is **True**, and `is_multicast` is the sole thing
  refusing multicast. The comment invited deleting the check doing the work. Fixed `76f2895`.
- **Dossier.** Evidence-fence neutralisation (44 tests), uncited-claim state (12), citation
  spans validated against the fetched excerpt (6), research-mode floor, all caught.
- **Gates.** Every gate neutered individually — all five have discriminating tests. Slot
  read by name instead of declared `type` → 9 fail.
  *Found:* a redundant guard **I** added, which no mutation could distinguish —
  `_near_duplicate`'s own comment had already written down why. Removed `93e0fd9`.
- **Rubric.** Deduction-without-evidence unconstructible, A-threshold, version recording,
  weight sum — all caught.
  *Found:* `assert DISCLAIMER in report.summary` passes vacuously when `DISCLAIMER` is `""`.
  Blanking it left 80 tests green while every summary rendered a bare grade with nothing
  saying it is not a performance prediction. Fixed `c847d85`.
- **Research API + UI.** Uncited claim rendered as ordinary, `—` replaced by `0`, the
  `value || "—"` falsy-zero trap, contradictions/unknowns dropped, source URL — all caught.
- **Prompt aggregation.** *Found:* dropping a module from `library.ALL` failed no test, since
  each module resolves from its own tuple. Now walked by package, so a *future* unaggregated
  module fails too. Fixed `b3d746e`.

**Not verified by anyone:** `app/revision.py`.

---

## 4. Three things only a human can do

1. **The base-URL probe** — unblocks v2 slice 7, takes two minutes. The question is *not*
   "does zernio.com answer" but whether the same key returns the **same account**:

   ```
   ! curl -s -H "Authorization: Bearer $(grep '^ZERNIO_API_KEY=' .env | cut -d= -f2-)" 'https://getlate.dev/api/v1/posts?limit=1&page=1' | python3 -c 'import sys,json; b=json.load(sys.stdin); print(b.get("pagination",{}).get("total"), (b.get("posts") or [{}])[0].get("_id"))'
   ! curl -s -H "Authorization: Bearer $(grep '^ZERNIO_API_KEY=' .env | cut -d= -f2-)" 'https://zernio.com/api/v1/posts?limit=1&page=1' | python3 -c 'import sys,json; b=json.load(sys.stdin); print(b.get("pagination",{}).get("total"), (b.get("posts") or [{}])[0].get("_id"))'
   ```

   Differing totals or ids mean it is not a URL swap and is a separate project.

2. **A Teams Workflow HTTP trigger.** `TEAMS_WEBHOOK_URL` is almost certainly a retired
   Office 365 connector (Microsoft disabled those 2026-05-18–22). Until a tenant-owned Power
   Automate workflow exists, v1's daily card is undeliverable by construction. Restrict the
   trigger to one identity — the URL is a bearer credential in URL clothing.

3. **A GitHub Support request to garbage-collect unreferenced objects.** The purge is live and
   every branch 404s, but the creator images remain fetchable at old commit SHAs — measured,
   747,768 bytes at `c4c920c`. No owner-side command fixes this. See ADR 0001.

---

## 5. The number that has not moved

**The lap counter still reads 0.** Every slice of v1 and v2 exists to close one lap, and not
one request has reached the live account, nor one card a real channel.

Before building further, consider proving it: flip `PUBLISHING_ENABLED`, take one draft,
run push → schedule → publish → metrics sync → verdict. Schedule it **about an hour out, not
days** — fast feedback, and it sidesteps the seven-day `/temp/` media expiry while everything
else is exercised for the first time.

That run is where the two things nobody could verify would surface: the `scheduledFor` format
chosen in `distribution._payload`, and the response shape `reconcile._outcome` reads.

---

## 6. Standing rules for whoever picks this up

- **`make check` green is not evidence.** Break the behaviour on purpose and confirm a test
  fails. If nothing fails, the test was decoration. Three of tonight's findings were tests,
  not code.
- **Two mutation traps, both hit tonight.** Removing one term of an overlapping `or` chain
  proves nothing — mutate the whole condition. And `assert CONST in text` passes vacuously
  when `CONST` is empty — assert substance, not presence.
- **Anything visual gets looked at in a browser**, 390px and 1440px, light and dark. jsdom
  lies. **The claude-in-chrome bridge is broken on this machine** — `list_connected_browsers`
  answers but every per-tab call times out; drive Chrome over the DevTools Protocol instead
  (Node 22 has a built-in WebSocket).
- **Parallel agents:** one migration owner per wave, or you get two Alembic heads. Split by
  file — `app/prompts/library.py` is aggregated by the lead precisely because three slices
  needed it at once and a shared file is last-writer-wins. Do not run two test suites against
  the one database concurrently.
- Read `CLAUDE.md`. Its rules are decisions, not preferences. The ones that bit hardest
  tonight: unknown is not zero, never rank, lineage through `(family_id, version)`, and slot
  behaviour off the declared `type` and never the name.

## 7. Decision records

- `docs/adr/0001-public-corpus-material.md` — why a delete commit was insufficient, and what
  the force-push did **not** achieve.
- `docs/adr/0002-human-publication-authority.md` — `Never publish` narrowed to *never publish
  without an explicit human command in Pixii*, localhost as the authorization boundary, **and
  the conditions that void it**: a second user, or anything but localhost. Read before
  touching publishing.

## 8. Open, recorded, not urgent

- The confirmation screen names the destination as an opaque Zernio id
  (`linkedin · 69719547c955c6705a96f1ce`). Verified in a browser: enough to confirm two
  commands went to the same place, **not** enough to know whose account it is. Tolerable only
  because exactly one account is configured. Build `ZernioClient.accounts()` the day there are
  two — the `ponytail:` note in `app/api_publishing.py` is its home.
- No draft→research link exists. Deliberate: the honest foreign key is probably
  `editorial_brief.research_job_id`, not `Draft.research_job_id`, and that was not settled.
  `ResearchPanel` takes the dossier as a prop and does not care, so it is a one-line change.
- `extraction.py`'s three prompts are still module constants. The move into the registry is
  mechanical and was left for a separate owner.
