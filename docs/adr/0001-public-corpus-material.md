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

## Decision

Remove `.scratch/corpus-widening/` from **all** Git history with `git filter-repo`, force-push
every branch, and add the path to `.gitignore`.

A normal `git rm` commit was considered and rejected: it leaves every byte reachable at the
prior commit, so the material stays published to anyone who clones.

## Consequences

**What this achieves.** The files are unreachable from any ref on the origin. A fresh clone
does not contain them. The working copy keeps its local `.scratch/corpus-widening/`, which
extraction still reads — this changes what is *published*, not what is *available locally*.

**What it does not achieve, stated plainly:**

- Existing clones and forks retain the objects. A rewrite cannot reach them.
- GitHub keeps unreferenced blobs accessible by SHA until it garbage-collects. If the
  material must be genuinely unreachable, open a GitHub Support request to force GC —
  the force-push alone does not do it.
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
