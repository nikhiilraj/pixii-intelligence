# ADR 0001 — Raw corpus material must not be public

**Date:** 2026-08-05
**Status:** Accepted
**Context:** [Platform Blueprint §2](../architecture/2026-08-04-pixii-platform-blueprint.md),
[implementation versions v0](../architecture/2026-08-05-implementation-versions.md)

## Context

`github.com/nikhiilraj/pixii-intelligence` is a **public** repository (verified
2026-08-05). It tracked 156 files under `.scratch/corpus-widening/`:

- `creator-images/` — 12 PNGs downloaded from other people's LinkedIn posts.
- `creator-inspiration.json` — those posts' text and metadata.
- `monte-linkedin-2024-2026.json` — the voice account's own post history.

The normalized database and the `media/` cache were already outside Git. This scratch
material was not, and had been public since it was committed.

The creator images are the load-bearing problem. **A publicly viewable post is not a
licence to redistribute its media.** Pixii collected them as reference material for
template extraction, which is a defensible internal use; republishing them under this
project's licence is a different act that nobody agreed to.

Checking what else lived under `.scratch/` turned up a second, quieter case:
`.scratch/v3/shots/` — 130 app screenshots kept as review evidence. Most are harmless
layout captures, but the Corpus and Scoreboard shots render the post table in full: 69
real post titles with their engaged-action counts, impressions, and engagement rate. That
is Monte's own content, so it is not the rights problem above. It is Pixii's business
performance data, and a public repository is not where it belongs.

## Decision

Remove `.scratch/corpus-widening/` **and** `.scratch/v3/shots/` from **all** Git history
with `git filter-repo`, force-push every branch, and add both paths to `.gitignore`.

The `.scratch/v1`, `v2`, and `v3` working notes stay tracked. They are specs, handoffs, and
progress logs — design records, not data.

A normal `git rm` commit was considered and rejected: it leaves every byte reachable at the
prior commit, so the material stays published to anyone who clones.

## Consequences

**What this achieves.** The files are unreachable from any ref on the origin. A fresh clone
does not contain them. The working copy keeps its local `.scratch/corpus-widening/`, which
extraction still reads — this changes what is *published*, not what is *available locally*.

**What it does not achieve, stated plainly:**

- Existing clones and forks retain the objects. A rewrite cannot reach them.
- **GitHub still serves the purged files at their old commit SHAs.** Measured immediately
  after the force-push of 2026-08-05, not assumed: `.scratch/corpus-widening/creator-images/
  post-01_img-1_image2.png` at pre-rewrite commit `c4c920c` returned 747,768 bytes through
  the contents API, while every branch returned 404. The force-push unreferences the
  objects; it does not delete them.

  What limits the exposure is discoverability: the repository's public events API listed no
  push events, so the old SHAs cannot be enumerated from outside. Reaching the files
  requires already holding a SHA — from a clone, a fork, or a link that quoted one.

  **Outstanding action:** open a GitHub Support request asking them to garbage-collect
  unreferenced objects for this repository. Nothing available to a repository owner does
  this; the force-push alone does not, and neither does any `git` command run locally.
- Every commit SHA on every branch changed. Anyone with a clone must re-clone or reset;
  merging an old clone would reintroduce the files.

**Going forward.** Blueprint §18 requires source URL, author, capture method, rights note,
and retention policy on captured material. Until that record exists, raw third-party
material stays out of Git entirely rather than relying on a reviewer noticing.

## Alternatives considered

**Make the repository private.** One command, no rewrite, no force-push, and the exposure
closes immediately. Rejected because the material stays in history, so the decision returns
the first time anyone wants the repo public again — and it would return as a surprise.

**Approve it as public.** Defensible for Monte's own posts. Not defensible for twelve other
people's images, which is what settles it.
