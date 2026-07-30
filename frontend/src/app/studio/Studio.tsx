"use client";

import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { assetSrc, postJson, type Asset, type Draft, type Template } from "@/lib/api";

type Picked = { hook: number | null; structure: number | null; visual: number | null };

/* Radix rejects `Select.Item value=""` — it reserves the empty string for "nothing is
 * selected". Every control on this page has an unset state ("let it suggest" on the three
 * template selects, "pick an asset" on each image slot), so all of them need a stand-in item
 * value, and that value must never leave the component.
 *
 * The two leaks are not equally loud, which is why both translations live here as functions a
 * test can call. `hook_id: "__none__"` in the JSON body is a 422 — annoying, but it announces
 * itself. `asset_values: {left_image_url: "__none__"}` is the silent one: `assetPayload` only
 * dropped `""`, so a sentinel would pass straight through, the backend would fail to resolve
 * an asset by that id, and the draft would be created with the image simply missing. Nothing
 * on screen would say so.
 */
export const NONE = "__none__";

/** The sentinel on the way back out, for a value that is a string when set. */
export const noneOf = (value: string) => (value === NONE ? "" : value);

/** The sentinel on the way back out, for a template id — `null` is what the API reads as
 *  "choose one for me", and it is what `picked` holds. */
export const templateId = (value: string) => (value === NONE ? null : Number(value));

/** One `image_url` slot of a visual template, and the asset the template prefers for it. */
export type ImageSlot = { name: string; defaultAssetId: string };

/** The slots an asset has to be picked for, read off the template's declared `type`.
 *
 *  Never guessed from the name: `left_image_url` reads as an asset and `subject`, `logo` and
 *  `hero` do not, so a name heuristic would ask for an asset where the model writes prose and
 *  vice versa. This mirrors `generation.asset_slots` — the backend fills exactly these slots
 *  from `asset_values` and ignores any other key, so a picker that offered a different set
 *  would send values that are silently dropped.
 *
 *  `default_asset_id` is a 4th optional key in the slots JSONB, so it arrives as the number 7
 *  or the string "7". Both are one reference and are compared as text everywhere they are read.
 */
export function imageSlots(visual: Template | undefined): ImageSlot[] {
  if (!visual) return [];
  return visual.slots
    .filter((slot) => slot.type === "image_url")
    .map((slot) => ({
      name: String(slot.name ?? ""),
      defaultAssetId: slot.default_asset_id == null ? "" : String(slot.default_asset_id),
    }));
}

/** What the picker starts on: the template's own defaults, so a visual that can complete
 *  unattended arrives pre-selected rather than looking like it needs a decision. */
export function defaultAssetValues(visual: Template | undefined): Record<string, string> {
  return Object.fromEntries(
    imageSlots(visual)
      .filter((slot) => slot.defaultAssetId)
      .map((slot) => [slot.name, slot.defaultAssetId]),
  );
}

/** The `asset_values` to send, narrowed to this visual's image slots.
 *
 *  Narrowed rather than sent whole because the picker's state outlives a change of visual: a
 *  slot name from the previously selected template would otherwise ride along, and the backend
 *  dropping it silently is not a reason to send it. An unpicked slot is omitted, not sent as
 *  `""` — the backend reads an absent slot as "fall back to the template default".
 *
 *  `noneOf` runs here as well as at the Select's own props: an unpicked slot reaches this
 *  function as `""` today, but the sentinel and `""` mean the same thing and the failure if one
 *  ever arrives is invisible. This is the one place that can make it impossible. */
export function assetPayload(
  visual: Template | undefined,
  chosen: Record<string, string>,
): Record<string, string> {
  return Object.fromEntries(
    imageSlots(visual)
      .map((slot) => [slot.name, noneOf((chosen[slot.name] ?? "").trim())])
      .filter(([, value]) => value !== ""),
  );
}

/** The `POST /drafts` body. Lifted out of the component when the five controls moved onto Radix
 *  Select, so that "the default state asks for nothing" is a property of a function a test can
 *  call rather than a claim about a component tree. */
export function draftPayload(
  idea: string,
  picked: Picked,
  visual: Template | undefined,
  assetValues: Record<string, string>,
) {
  return {
    idea,
    hook_id: picked.hook,
    structure_id: picked.structure,
    visual_id: picked.visual,
    asset_values: assetPayload(visual, assetValues),
  };
}

/** One row of the drafts list — enough to recognise a draft and open it, and nothing else.
 *
 *  A deliberate narrowing of `Draft` rather than the thing itself: `GET /drafts` answers with
 *  the full `DraftOut` of every draft, base64 PNG included, which is 530KB over seven rows
 *  today. All of that would be serialized into this client component's payload to render a
 *  list of seven links. `page.tsx` maps it down to these three fields before it crosses.
 *
 *  Local rather than in `lib/api.ts` because it is this page's projection, not the API's
 *  shape — the API's shape is `Draft`, which is already there. */
export type DraftSummary = { id: number; idea: string; zernio_post_id: string | null };

/** The drafts that exist, as links that open them.
 *
 *  `null` means the list could not be read, which is not an empty list — same distinction as
 *  `assets`, and it matters more here: "no drafts yet" on a failed read would be this page
 *  claiming the database is empty while six drafts sit in it, which is the exact conflation
 *  US-012 exists to remove.
 *
 *  ponytail: a plain list, unfiltered, unsorted here and uncapped below the backend's own
 *  `limit=100`. Ordered newest-first by the API and rendered in the order it gave. No search,
 *  no thumbnails, no status filter, no pagination. Ceiling: any of those once the list is long
 *  enough to scroll past — at seven drafts a filter would be furniture. */
function Drafts({ drafts, current }: { drafts: DraftSummary[] | null; current: number | null }) {
  return (
    <div className="space-y-2 rounded-lg border border-black/10 p-3 dark:border-white/15">
      <div className="text-xs font-medium uppercase tracking-widest opacity-50">Drafts</div>

      {drafts === null ? (
        <p className="text-sm text-amber-700 dark:text-amber-400">
          The draft list could not be read. This is a failed request, not an empty database —
          drafts may exist that this list cannot show.
        </p>
      ) : drafts.length === 0 ? (
        <p className="text-sm text-muted">
          No draft has been generated yet. Every draft written here stays listed until it is
          deleted, whether or not it has reached Zernio.
        </p>
      ) : (
        <ul>
          {drafts.map((d) => (
            <li key={d.id}>
              <Link
                href={`/studio?draft=${d.id}`}
                aria-current={d.id === current ? "page" : undefined}
                /* `min-w-0` + `truncate` on the label, and it is load-bearing at 390px: the
                   left column is a grid child whose implicit `min-width: auto` is min-content,
                   so one idea carrying a long unbroken token — a URL — would grow the track
                   past the viewport instead of being cut. Latent today (every idea in the
                   database happens to wrap) for the same reason the right column's `min-w-0`
                   is, which is why it is pinned rather than left to luck. */
                className="flex items-baseline justify-between gap-3 rounded-md px-2 py-1.5 text-sm transition-colors hover:bg-surface-2 aria-[current]:bg-surface-2 aria-[current]:font-medium"
              >
                <span className="min-w-0 truncate">{d.idea || `draft #${d.id}`}</span>
                <span className="shrink-0 text-xs text-muted">
                  {d.zernio_post_id ? "in Zernio" : `#${d.id}`}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Lineage({ draft, assets }: { draft: Draft; assets: Asset[] | null }) {
  const rows = [
    ["Hook", draft.lineage.hook],
    ["Structure", draft.lineage.structure],
    ["Visual", draft.lineage.visual],
  ] as const;

  // Which asset filled which slot. Shown for the same reason the template versions are: the
  // picture is a flattened PNG, so this is the only place a reviewer can see that the logo in
  // it came from asset 7 rather than asset 9. Falls back to the bare id when the library is
  // unreadable — the id is still the truth, the label is only a convenience.
  const picked = Object.entries(draft.asset_values);

  return (
    <div className="rounded-lg border border-black/10 p-3 text-xs dark:border-white/15">
      <div className="mb-2 font-medium uppercase tracking-widest opacity-50">Lineage</div>
      {rows.map(([label, entry]) => (
        <div key={label} className="flex justify-between gap-3 py-0.5">
          <span className="opacity-60">{label}</span>
          <span>{entry ? `${entry.name} v${entry.version}` : "—"}</span>
        </div>
      ))}
      {picked.map(([slot, id]) => {
        const asset = assets?.find((a) => String(a.id) === id);
        return (
          <div key={slot} className="flex justify-between gap-3 py-0.5">
            <span className="opacity-60">{slot}</span>
            <span>{asset ? `${asset.label || asset.kind} (#${id})` : `asset #${id}`}</span>
          </div>
        );
      })}
    </div>
  );
}

export default function Studio({
  templates,
  // `null` means the library could not be read, which is not the same as an empty library. The
  // picker says which, rather than telling someone to upload an asset they already have.
  assets,
  // The drafts that already exist, and the one `?draft=` asked for. `initialDraft` seeds the
  // state the session's own generated draft lands in, so a loaded draft and a just-written one
  // are the same value and every action on this page treats them identically — which is what
  // makes "pushing a loaded draft behaves exactly like pushing a fresh one" true by
  // construction rather than by a second code path that has to agree with the first.
  //
  // Seeded through `useState`'s initial value and NOT synced by an effect: React 19 forbids
  // syncing derived state that way and the compiler is on. `page.tsx` gives this component a
  // `key` of the requested id, so navigating from one draft to another remounts it — see the
  // comment there.
  drafts,
  initialDraft = null,
  // Why the requested draft is not here. Distinct from `initialDraft === null`, which is a
  // fresh session with nothing asked for.
  missing = null,
}: {
  templates: Template[];
  assets: Asset[] | null;
  drafts: DraftSummary[] | null;
  initialDraft?: Draft | null;
  missing?: string | null;
}) {
  const [idea, setIdea] = useState("");
  const [picked, setPicked] = useState<Picked>({ hook: null, structure: null, visual: null });
  const [reason, setReason] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft | null>(initialDraft);
  const [busy, setBusy] = useState<string | null>(null);
  const [assetValues, setAssetValues] = useState<Record<string, string>>({});

  const approved = templates.filter((t) => t.status === "approved");
  const of = (kind: Template["kind"]) => approved.filter((t) => t.kind === kind);

  const visual = templates.find((t) => t.id === picked.visual);
  const slots = imageSlots(visual);

  /** Choosing a visual resets the picker to that template's defaults.
   *
   *  In the handler, not an effect: React 19 forbids syncing derived state through one, and
   *  the choice arrives from exactly two places — the select and Suggest — both of which are
   *  events. Reset rather than merged, because a slot name is only meaningful against the
   *  template that declares it. */
  function chooseVisual(id: number | null) {
    setPicked((current) => ({ ...current, visual: id }));
    setAssetValues(defaultAssetValues(templates.find((t) => t.id === id)));
  }

  // `busy` holds the path so a button can label its own in-flight state. Failures go to a
  // toast: this component fires four different mutations and they all reported into one
  // shared paragraph, so which call had failed was already only inferable from the message.
  async function call<T>(path: string, body?: unknown): Promise<T | null> {
    setBusy(path);
    const result = await postJson<T>(path, body);
    setBusy(null);
    if (result.ok) return result.data;
    // The message alone, with no title. `call` is shared by five different mutations, so any
    // title it could compose would either be a URL path — which is not something to show a
    // reader — or invented. The API's `detail` strings are written for a person (US-003), and
    // the paragraph this replaces showed exactly the message and nothing else.
    toast.error(result.message);
    return null;
  }

  const payload = draftPayload(idea, picked, visual, assetValues);

  // Dresses the textarea only, now that the five selects on this page draw their own border from
  // the same tokens. It was `border-black/15 rounded-md text-sm`, which is what made the old
  // native selects look like a different app from the Buttons under them — retokenising the one
  // control left behind is what stops this slice from creating a fresh mismatch on its way out.
  const field =
    "w-full rounded-input border border-border bg-transparent px-3 py-2 text-meta";

  return (
    <div className="mt-8 grid gap-10 lg:grid-cols-[22rem_1fr]">
      {/* `min-w-0` for the same reason the draft column carries it, and here it is the one that
          can actually move: at 390px this grid is a single column, so this section is the `1fr`
          track and a draft idea in the list below is the longest string on the page. */}
      <section className="min-w-0 space-y-3">
        <textarea
          value={idea}
          onChange={(e) => setIdea(e.target.value)}
          rows={5}
          placeholder="An idea, a finding, a link — what is this post about?"
          className={field}
        />

        {(["hook", "structure", "visual"] as const).map((kind) => (
          <Select
            key={kind}
            value={picked[kind] === null ? NONE : String(picked[kind])}
            onValueChange={(value) => {
              const id = templateId(value);
              if (kind === "visual") chooseVisual(id);
              else setPicked({ ...picked, [kind]: id });
            }}
          >
            {/* `aria-label` on the trigger, not a first option that reads as one. The native
                select carried the control's name only inside "hook — let it suggest", which
                stops being the accessible name the moment something else is chosen. */}
            <SelectTrigger aria-label={kind} className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={NONE}>{kind} — let it suggest</SelectItem>
              {of(kind).map((t) => (
                <SelectItem key={t.id} value={String(t.id)}>
                  {t.name} (v{t.version})
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        ))}

        {slots.length > 0 && (
          /* The asset picker. Only appears once a visual with image slots is chosen — the
             slots are read off that template, so there is nothing to ask about before then,
             and a visual left to be suggested is filled from the template's own defaults on
             the server instead.

             ponytail: one `Select` per slot with a thumbnail beside it, matching the three
             template selects above it — which is the whole reason this moved in the same slice
             they did. Still no search, no recent-assets memory, no drag-into-slot, no
             grid-of-thumbnails dialog. Ceiling: US-015 replaces this with a picker dialog when
             the library outgrows a dropdown; a thumbnail-per-row list is fine at 2 assets and
             unusable at 200. */
          <div className="space-y-3 rounded-lg border border-black/10 p-3 dark:border-white/15">
            <div className="text-xs font-medium uppercase tracking-widest opacity-50">
              Images — {slots.length} slot{slots.length === 1 ? "" : "s"}
            </div>

            {assets === null ? (
              <p className="text-sm text-amber-700 dark:text-amber-400">
                The asset library could not be read, so there is nothing to pick from. This is a
                failed request, not an empty library — the visual will not render until it is
                readable.
              </p>
            ) : assets.length === 0 ? (
              <p className="text-sm text-amber-700 dark:text-amber-400">
                Nothing in the library yet. This template has an image slot, so it cannot render
                until an asset is uploaded on the Assets page.
              </p>
            ) : (
              slots.map((slot) => {
                const chosen = assets.find((a) => String(a.id) === assetValues[slot.name]);
                return (
                  <div key={slot.name} className="flex items-center gap-2">
                    {chosen ? (
                      // A plain <img>: served by the backend at an arbitrary path, the case
                      // next/image is wrong for. The directive has to sit immediately above the
                      // tag — anything between them makes it an unused directive.
                      // eslint-disable-next-line @next/next/no-img-element
                      <img
                        src={assetSrc(chosen)}
                        alt={`${slot.name}: ${chosen.label || chosen.kind}`}
                        className="h-10 w-10 shrink-0 rounded-md border border-black/10 object-contain dark:border-white/15"
                      />
                    ) : (
                      <span
                        aria-hidden
                        className="h-10 w-10 shrink-0 rounded-md border border-dashed border-black/15 dark:border-white/20"
                      />
                    )}
                    <Select
                      value={assetValues[slot.name] || NONE}
                      onValueChange={(value) =>
                        setAssetValues({ ...assetValues, [slot.name]: noneOf(value) })
                      }
                    >
                      {/* `flex-1 min-w-0`, not `w-full`: this row is a flex line with a 40px
                          thumbnail in it, so a full-width trigger would push itself past the
                          column. `min-w-0` is what lets the label truncate instead. */}
                      <SelectTrigger aria-label={slot.name} className="min-w-0 flex-1">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value={NONE}>{slot.name} — pick an asset</SelectItem>
                        {assets.map((a) => (
                          <SelectItem key={a.id} value={String(a.id)}>
                            {a.label || `untitled ${a.kind}`} ({a.kind})
                            {String(a.id) === slot.defaultAssetId ? " — template default" : ""}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                );
              })
            )}
          </div>
        )}

        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            disabled={!idea.trim() || busy !== null}
            onClick={async () => {
              const s = await call<{
                hook: { id: number };
                structure: { id: number };
                visual: { id: number };
                reason: string;
              }>("/drafts/suggest", payload);
              if (s) {
                setPicked({ hook: s.hook.id, structure: s.structure.id, visual: s.visual.id });
                // A suggested visual brings its own image slots, so the picker has to be reset
                // to that template's defaults exactly as choosing one by hand does.
                setAssetValues(defaultAssetValues(templates.find((t) => t.id === s.visual.id)));
                setReason(s.reason);
              }
            }}
          >
            Suggest templates
          </Button>
          <Button
            disabled={!idea.trim() || busy !== null}
            onClick={async () => {
              const d = await call<Draft>("/drafts", payload);
              if (d) setDraft(d);
            }}
          >
            {busy === "/drafts" ? "Writing…" : "Generate draft"}
          </Button>
        </div>

        {reason && <p className="text-xs opacity-60">{reason}</p>}
        {approved.length === 0 && (
          <p className="text-sm text-amber-700 dark:text-amber-400">
            Nothing approved yet. Approve a hook, a structure and a visual first.
          </p>
        )}

        {/* The list is what the page was read with, so it does not gain the draft the button
            above just wrote — this is a server-rendered list on a `force-dynamic` page, and a
            reload is what refreshes it. Named rather than fixed: making the list live would
            mean either a client-side fetch or a router refresh after every generate, and
            neither is worth it for a list whose newest entry is already on screen in full. */}
        {/* `draft`, not `initialDraft`: generating something else on a `?draft=424` URL replaces
            what the right column is showing, and a row still marked as open would be pointing
            at a draft that is no longer on screen. A freshly generated draft is not in this
            list, so nothing is marked — which is the truth. */}
        <Drafts drafts={drafts} current={draft?.id ?? null} />
      </section>

      {/* `min-w-0` for the same reason as TemplateManager's: the `1fr` track's implicit
          `min-width: auto` is min-content, so a single long unbroken string in a draft would grow
          the track past the viewport instead of scrolling inside it. Latent here rather than
          live — today's drafts happen to wrap — which is exactly why it is worth pinning. */}
      <section className="min-w-0 space-y-4">
        {/* A draft in hand wins over both notices, and the order is load-bearing rather than
            arbitrary. `missing` is a prop and never clears, but the left column keeps working
            on that route: landing on `?draft=999` from a stale link and clicking Generate
            writes a real row, and with the alert tested first the column would go on reporting
            a 404 for a draft that now exists on screen. A write that renders as a failure is
            the same conflation as an error that renders as an empty state. */}
        {draft ? (
          <>
            <Lineage draft={draft} assets={assets} />

            {draft.zernio_post_id && (
              <p className="text-xs opacity-60">
                In Zernio as a draft ({draft.zernio_post_id}). Publishing stays a human act.
              </p>
            )}

            <article className="whitespace-pre-wrap rounded-lg border border-black/10 p-4 text-[15px] leading-relaxed dark:border-white/15">
              {draft.full_text}
            </article>

            {draft.visual_png ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={`data:image/png;base64,${draft.visual_png}`}
                alt="Generated visual"
                className="w-full max-w-sm rounded-lg border border-black/10 dark:border-white/15"
              />
            ) : (
              <p className="text-sm text-amber-700 dark:text-amber-400">
                Visual not produced: {draft.visual_error ?? "unknown"} — the text is unaffected.
              </p>
            )}

            <div className="flex gap-2">
              <Button
                variant="outline"
                disabled={busy !== null}
                onClick={async () => {
                  const d = await call<Draft>(`/drafts/${draft.id}/regenerate-text`);
                  if (d) setDraft(d);
                }}
              >
                Rewrite text
              </Button>
              <Button
                variant="outline"
                disabled={busy !== null}
                onClick={async () => {
                  const d = await call<Draft>(`/drafts/${draft.id}/regenerate-visual`);
                  if (d) setDraft(d);
                }}
              >
                Redraw visual
              </Button>
              {/* Identical for a loaded draft and a just-written one, because they are the same
                  value in the same state — `initialDraft` seeds it. Disabled once
                  `zernio_post_id` is set: pushing creates a Zernio DRAFT and nothing here ever
                  publishes. */}
              <Button
                disabled={busy !== null || draft.zernio_post_id !== null}
                onClick={async () => {
                  const d = await call<Draft>(`/drafts/${draft.id}/push`);
                  if (d) setDraft(d);
                }}
              >
                {draft.zernio_post_id ? "In Zernio" : "Push to Zernio as draft"}
              </Button>
            </div>
          </>
        ) : missing ? (
          /* "We looked for that draft and it is not there" — never the empty state below.
             Those two are different claims and rendering one as the other is what US-012 came
             to fix at the Inbox end of the same link: an empty column that means "nothing
             asked for" would read as "this draft is blank" to whoever followed a link to it. */
          <Card role="alert" className="border-danger/40 bg-danger/10 text-body">
            <p className="font-medium">That draft could not be opened.</p>
            <p className="mt-1 text-muted">{missing}</p>
            <p className="mt-1 text-muted">
              This is a report about the draft that was asked for, not an empty session — the
              drafts that do exist are listed on the left.
            </p>
          </Card>
        ) : (
          /* The empty state of a fresh session, and now only that: a page opened with no
             `?draft=` and nothing generated yet. It is still not a claim about the database —
             the list on the left is what speaks for the database — so it says what fills this
             column, and repeats the line that is never negotiable here. `opacity-50` before
             this — which the PRD names as an AA failure — is now --text-muted. */
          <Card className="bg-surface-2 text-body">
            <p className="font-medium">No draft yet.</p>
            <p className="mt-1 text-muted">
              Write an idea, choose a hook, a structure and a visual — or let Suggest choose them
              — and Generate writes the post and renders its picture here, stamped with the
              templates that produced it. Or open one of the drafts listed on the left. Nothing
              publishes from here: a draft reaches Zernio only when you push it, and goes live
              only when a human publishes it there.
            </p>
          </Card>
        )}
      </section>
    </div>
  );
}
