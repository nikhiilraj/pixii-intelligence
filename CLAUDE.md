# Working in this repo

Read `README.md` first. It explains what the tool is, the circuit it runs, and — most
importantly — what it *refuses* to do and why. Those refusals are decisions, not gaps.
Removing one is undoing a decision, so raise it before you do.

`frontend/AGENTS.md` applies inside `frontend/`: this Next.js version has breaking changes,
so check `node_modules/next/dist/docs/` rather than your training data.

## Commands

```bash
make up      # Postgres on :5433
make api     # migrations + FastAPI on :8000
make web     # Next.js on :3000
make check   # ruff, mypy, eslint, tsc, pytest, vitest — the full gate
```

Single tests: `cd backend && .venv/bin/pytest tests/test_x.py -q` · `cd frontend && pnpm test X`

## Rules that are not style preferences

**Never publish without an explicit human command in Pixii.** This narrows the older
`Never publish` rule and keeps what it protected: no machine decision reaches an audience
unless a person said so. Automation prepares — the daily run, the scheduler and
`run_autonomous` cannot reach a publish command, and must not learn how. Only an HTTP route
a human hits can, only when `PUBLISHING_ENABLED` is on, and only against the exact draft
revision the reviewer confirmed. See `docs/adr/0002-human-publication-authority.md`; it also
names the condition that voids the arrangement (a second user, or anything but localhost).

**Never rank.** No "best", "top" or "recommended", and no sort-by-performance control. Each
template has ~3 samples across a 12.7× engagement spread; any ranking is noise wearing a
confident face. The threshold is ~300 posts with recorded lineage. There are zero.

**Print `—`, never `0`, where data was never collected.** Showing `0` presents an absence as
a measurement. Where the database cannot tell the two apart, say so rather than guess.

**Lineage resolves through `(family_id, version)`, never "latest".** Editing a template writes
a new row. Looking one up by id, or by newest version, silently credits the wrong template.
Several bugs here were exactly this.

**Read slot behaviour off the declared `type`, never the slot's name.** `left_image_url` reads
as an asset and `hero` does not. Use `.get("type")`, never `slot["type"]` — VISUAL rows
authored before `type` existed carry no key at all.

**`.env` is a symlink to the vault's `.env`.** Never copy a secret value into this repo, and
never add one to `.env.example` with a value.

**Every datetime column is `timestamp without time zone`.** An aware value reads back naive,
and comparing the two *raises*. Normalise through `db.utc`.

## Testing

A green suite is not evidence. `make check` did not run the frontend suite at all until v3;
an audit of 204 deliberate mutations found six assertions checking for a `404` that passed
against routes **that did not exist**, because the framework 404s any unrouted path.

**The standard is: break it on purpose and check a test fails.** If nothing fails, the test
was decoration.

Three of the worst defects this project has had — a page 10,384px wide with its buttons
off-screen, every page misaligned with the nav, and a control invisible in dark mode — were
all found by **rendering the page and looking at it**, with a fully green suite throughout.
jsdom lies about focus and renders Recharts at 0×0, so anything visual gets looked at in a
browser, at 390px and 1440px, in light and dark.

## Style

Match what is there. Comments in this codebase explain *why*, and specifically why the
obvious alternative is wrong — several are the only record of a bug that cost someone hours.
Do not strip them, and write new ones the same way.

Mark deliberate simplifications with `ponytail:`, naming the ceiling and the upgrade path:
`# ponytail: global lock, per-account locks if throughput matters`.

## Where things are

`backend/app/` — `extraction` (posts → templates) · `generation` (templates → drafts) ·
`rendering` (visuals → images) · `assets` (the image library) · `publishing`/`zernio` (out) ·
`metrics` (back in) · `corpus` (what exists) · `autonomous`/`scheduler` (unattended runs).

`docs/superpowers/specs/` and `docs/superpowers/plans/` — designs and their implementation
plans. `.scratch/vN/` — per-version working notes; `progress.txt` is where the bodies are
buried in detail.
