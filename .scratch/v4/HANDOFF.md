# Handoff — 2026-08-05, ~02:20 IST

Session ended mid-v3. Read `docs/architecture/2026-08-05-implementation-versions.md` first;
it is the plan and it is kept accurate, including where implementation diverged from it.

## Where the work stands

| | State |
|---|---|
| **v0** data safety | Done. Purge live on all branches. **One action outstanding — see below.** |
| **v1** daily slot + Teams | Shipped. **Never delivered a card to a real channel.** |
| **v2** schedule/publish | 6 of 7 slices shipped. **Nothing ever sent to the live account.** |
| **v3** research brain | Wave 1 partial: prompt registry shipped, fetcher in flight. |

## Three things only a human can do, in the order I would do them

1. **The base-URL probe.** v2 slice 7 is blocked on it and it is two minutes. The question is
   *not* "does zernio.com answer" — it is whether the same key returns the **same account**:

   ```
   ! curl -s -H "Authorization: Bearer $(grep '^ZERNIO_API_KEY=' .env | cut -d= -f2-)" 'https://getlate.dev/api/v1/posts?limit=1&page=1' | python3 -c 'import sys,json; b=json.load(sys.stdin); print(b.get("pagination",{}).get("total"), (b.get("posts") or [{}])[0].get("_id"))'
   ! curl -s -H "Authorization: Bearer $(grep '^ZERNIO_API_KEY=' .env | cut -d= -f2-)" 'https://zernio.com/api/v1/posts?limit=1&page=1' | python3 -c 'import sys,json; b=json.load(sys.stdin); print(b.get("pagination",{}).get("total"), (b.get("posts") or [{}])[0].get("_id"))'
   ```

   Differing totals or ids mean it is not a URL swap and is a separate project.

2. **A Teams Workflow HTTP trigger.** `TEAMS_WEBHOOK_URL` is almost certainly a retired
   Office 365 connector — Microsoft disabled those 2026-05-18–22. Until a tenant-owned Power
   Automate workflow exists, v1's daily card is undeliverable by construction. Restrict the
   trigger to one identity; the URL is a bearer credential in URL clothing.

3. **A GitHub Support request to garbage-collect unreferenced objects.** The purge is live and
   every branch 404s, but the creator images are still fetchable at old commit SHAs — measured,
   747,768 bytes at `c4c920c`. No owner-side command fixes this. See ADR 0001.

## The one thing that has not happened

**The lap counter still reads 0.** Everything in v1 and v2 exists to close one lap and none of
it has touched reality. Before building more v3, consider: flip `PUBLISHING_ENABLED`, take one
draft, and run push → schedule → publish → metrics sync → verdict.

Schedule it **about an hour out, not days** — quick feedback, and it sidesteps the seven-day
`/temp/` media expiry while everything else is being exercised for the first time. That run is
where the two things nobody could verify would surface: the `scheduledFor` format chosen in
`distribution._payload`, and the response shape `reconcile._outcome` reads.

## Known-unverified, stated plainly

- No Zernio command has ever been sent. `PUBLISHING_ENABLED` is `false` and should stay so
  until a human has looked at the confirmation screen themselves.
- `reconcile._outcome`'s reading of Zernio's status field is inferred from documentation, not
  observed. It resolves anything it cannot read to "still unknown", which is the safe failure.
- No Teams card has reached a real channel.

## Operational finding

**The claude-in-chrome bridge is dead on this machine.** `list_connected_browsers` answers
instantly with valid JSON; every per-tab call (`tabs_context_mcp`, `tabs_create_mcp`,
`navigate`) times out, and `select_browser`/`switch_browser` does not revive it. Looks like a
pending grant in the extension side panel. The workaround that worked: driving Chrome directly
over the DevTools Protocol with Node 22's built-in WebSocket, no extension. Worth fixing —
this project's rule is that anything visual gets looked at in a browser, and right now that
costs every agent the same detour.

## Decisions taken tonight, with their records

- `docs/adr/0001-public-corpus-material.md` — why a delete commit was insufficient, and what
  the force-push did **not** achieve.
- `docs/adr/0002-human-publication-authority.md` — `Never publish` narrowed to *never publish
  without an explicit human command in Pixii*, with localhost as the authorization boundary
  **and the conditions that void it**: a second user, or anything but localhost. Read this
  before touching publishing.

## Open, recorded, not urgent

- The confirmation screen names the destination as an opaque Zernio id
  (`linkedin · 69719547c955c6705a96f1ce`). Verified in a browser: enough to confirm two
  commands went to the same place, **not** enough to recognise whose account it is. What makes
  it tolerable is that exactly one account is configured — the reviewer is trusting a fact
  rather than reading the screen. Build `ZernioClient.accounts()` the day there are two; the
  `ponytail:` note in `app/api_publishing.py` is the home for it.
- v3 wave 2 — research dossier, editorial brief, gates and rubric, research UI. Wave order and
  the fetcher's interface contract are in the plan file.
