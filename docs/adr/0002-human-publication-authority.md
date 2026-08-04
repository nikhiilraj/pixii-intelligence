# ADR 0002 — Publishing from Pixii, with localhost as the authorization boundary

**Date:** 2026-08-05
**Status:** Accepted
**Supersedes:** the `Never publish` rule in `CLAUDE.md` and the corresponding statements in
`README.md`
**Context:** [Platform Blueprint §4](../architecture/2026-08-04-pixii-platform-blueprint.md),
[implementation versions v2](../architecture/2026-08-05-implementation-versions.md)

## Context

`CLAUDE.md` said:

> **Never publish.** A draft reaches Zernio as a draft. A human publishes it there, by hand.
> Nothing in this codebase may change that.

That rule was right for what it was protecting: an application that generates text
unattended must not be able to put that text in front of an audience on its own. Making the
publish step happen in a different product, by hand, guaranteed it.

It also cost something. Every publication required opening Zernio, which meant the review
that mattered — sources, lineage, which template version produced this — happened in one
place and the decision happened in another. And Pixii could not observe the outcome of its
own recommendation without a human relaying it.

The blueprint proposes a narrower rule that keeps the guarantee and removes the cost:
**never publish without an explicit authorized human command in Pixii.**

## Decision

Build schedule, publish-now, and cancel in Pixii. Treat **localhost with a single operator**
as the authorization boundary, and do not build Entra ID, organizations, or roles yet.

Blueprint §4 forbids shipping publish without confirmation, audit, idempotency,
stale-revision rejection, and a kill switch **together**. All five ship in the same slice:

- **Confirmation** — the review screen shows account, action, local time, timezone, and
  resolved UTC before the command fires.
- **Audit** — every command is a `publication` row: action, the exact draft revision the
  reviewer confirmed against, all three forms of the time, outcome, and the provider's own
  refusal text. There is no separate `audit_event` table; with one operator, an `actor`
  column that always holds the same person is ceremony.
- **Idempotency** — the key is derived from `(draft, revision, action, resolved time)` and
  carries a **unique index**. The uniqueness is the guarantee; a read-then-insert would race
  with itself under the double-click it exists to defend against.
- **Stale-revision rejection** — `Draft.revision` counts human-visible changes only. A
  command confirmed against an older revision is refused with 409 and the current number.
- **Kill switch** — `PUBLISHING_ENABLED`, **off by default**. It stops external commands and
  nothing else: generation, review, and pushing drafts to Zernio all continue.

## Consequences

**What the guarantee still is.** Automation prepares; a human commands. Nothing in the
daily run, the scheduler, or the autonomous path can reach a publish command — the only
callers are HTTP routes a person hits, and `run_autonomous`'s docstring still promises it
never reaches Zernio at all.

**What now depends on the operator.** With no authentication, anyone who can reach the API
can publish. On localhost that is the person at the keyboard, which is why the boundary
holds. It is a real boundary, not an absent one — but it is a *deployment* boundary, and
deployments change.

**The condition that voids this ADR.** The day Pixii runs anywhere but localhost, **or** a
second person uses it, authentication and a publisher role are required *before*
`PUBLISHING_ENABLED` may be true. The kill switch is what buys the time to build them: turn
it off, and the capability is gone without a code rollback.

**The seven-day media trap this created.** `push_draft` recorded a measurement: Zernio's
presigned uploads land in `/temp/` and expire after seven days, and the file is copied to
permanent storage only when a post **publishes**. That was unfixable and harmless while
publishing was an unbounded human act in Zernio. Making Pixii the publisher makes it
reachable — a schedule two weeks out would publish text with a dead image. Every schedule
and publish command therefore re-uploads the visual unconditionally.

## Alternatives considered

**Build authentication first (blueprint Phase 1 → 2).** Correct for a team product, and the
path this returns to the moment there are two people. Rejected for now because it adds
weeks before one lap of the circuit closes, and none of it is needed by one person on a
laptop. Deferring it is only defensible because the condition that ends the deferral is
written down here.

**Keep `Never publish`.** Zero new security surface, and the guarantee is unarguable.
Rejected because the cost is real and the narrower rule keeps what the original was actually
protecting: that no machine decision reaches an audience without a person saying so.
