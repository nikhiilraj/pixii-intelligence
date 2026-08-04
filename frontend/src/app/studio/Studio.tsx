"use client";

import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  API_BASE,
  assetSrc,
  calls,
  postForm,
  postJson,
  type Asset,
  type AssetKind,
  type Draft,
  type Spend,
  type Template,
} from "@/lib/api";

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

/** Which assets the picker's search box leaves on screen — label, kind and tags, folded to
 *  lower case and matched as substrings.
 *
 *  Not `filename`: it is a sha256 digest, so nobody can mean it and a hex query would hit rows
 *  at random. Not `source_post_id` either — a number typed into a search box is a label or a
 *  size, never a foreign key.
 *
 *  A pure function because it is the half of the dialog a jsdom test can hold honestly. The
 *  grid itself lives in a Radix portal; "typing narrows what is offered" is a claim about this
 *  list, and this is where it can be made without asserting anything Radix owns.
 *
 *  ponytail: substring, not fuzzy, not ranked, not tokenised. Ceiling: a real matcher the day
 *  the library is long enough that "cool" failing to find "Cooler comparison" is plausible —
 *  substring already covers that one. */
export function matches(assets: Asset[], query: string): Asset[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return assets;
  return assets.filter((asset) =>
    [asset.label, asset.kind, ...asset.tags].some((field) =>
      field.toLowerCase().includes(needle),
    ),
  );
}

/** Where an arrow key moves focus in the grid, or `null` for a key the grid does not handle.
 *
 *  Linear rather than two-dimensional on purpose: the grid is `auto-fill`, so its column count
 *  is a function of the rendered width and is not knowable here — a hard-coded row length would
 *  be wrong at every breakpoint but one. Up/Left step back, Down/Right step forward, and both
 *  clamp rather than wrap: wrapping from the last tile to the first reads as a jump when
 *  nothing on screen says the list ended.
 *
 *  `index` is `-1` while focus is still in the search field, and a forward key from there lands
 *  on the first tile — which is what makes type-then-arrow work without a second handler. */
export function nextIndex(key: string, index: number, count: number): number | null {
  if (count === 0) return null;
  if (key === "ArrowRight" || key === "ArrowDown") return Math.min(index + 1, count - 1);
  if (key === "ArrowLeft" || key === "ArrowUp") return Math.max(index - 1, 0);
  if (key === "Home") return 0;
  if (key === "End") return count - 1;
  return null;
}

/* ponytail: the five kinds re-declared rather than imported from `assets/AssetLibrary`, for the
 * reason `assetSrc` was lifted into `lib/api` instead — importing that module here would pull a
 * whole other client component into this page's bundle for one array. */
const KINDS: AssetKind[] = ["logo", "product", "screenshot", "brand", "photo"];

/** The picker dialog for one slot: a searchable grid of the library, plus an upload that does
 *  not leave this page.
 *
 *  Mounted only while a slot is being picked for, so its search box and its upload form start
 *  clean on every open without an effect resetting them — the same reason `chooseVisual` is a
 *  handler.
 *
 *  ponytail: no pagination, no kind filter, no sort, no multi-select, no drag-into-slot, no
 *  crop. Search plus a grid is the spec; the Assets page is still where the library is managed,
 *  and this upload deliberately carries no label or tag fields — the API defaults the label to
 *  the file name. Ceiling: a kind filter beside the search box once the library is long enough
 *  that searching by kind through the same box is not enough. */
function AssetPicker({
  slot,
  assets,
  chosen,
  onPick,
  onUploaded,
}: {
  slot: ImageSlot;
  assets: Asset[];
  chosen: string;
  onPick: (assetId: string) => void;
  onUploaded: (asset: Asset) => void;
}) {
  const [query, setQuery] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [kind, setKind] = useState<AssetKind>("logo");
  const [uploading, setUploading] = useState(false);

  const shown = matches(assets, query);
  const fallback = assets.find((a) => String(a.id) === slot.defaultAssetId);

  /** Arrow keys across the tiles. The tiles are read out of the DOM rather than tracked in
   *  state: they are already the rendered order, `document.activeElement` already says where
   *  focus is, and a roving-tabindex state would have to be kept in step with a list the search
   *  box rewrites on every keystroke.
   *
   *  A text field owns Left/Right/Home/End while it has focus — stealing those would break the
   *  caret in the search box, which is the one control someone is guaranteed to be typing in. */
  function move(event: React.KeyboardEvent<HTMLDivElement>) {
    const typing = (event.target as HTMLElement).tagName === "INPUT";
    if (typing && event.key !== "ArrowDown" && event.key !== "ArrowUp") return;

    const tiles = Array.from(
      event.currentTarget.querySelectorAll<HTMLButtonElement>("[data-tile]"),
    );
    const at = nextIndex(event.key, tiles.indexOf(document.activeElement as HTMLButtonElement), tiles.length);
    if (at === null) return;
    event.preventDefault();
    tiles[at]?.focus();
  }

  async function upload(event: React.FormEvent) {
    event.preventDefault();
    if (!file) return;

    const body = new FormData();
    body.append("file", file);
    body.append("kind", kind);

    setUploading(true);
    const result = await postForm<Asset>("/assets", body);
    setUploading(false);

    if (!result.ok) {
      // The API's own words — "not a readable image", "not a format this library serves", and
      // the 422 listing the five kinds are all written for a reader. The form keeps the file it
      // has, so a refusal is something to retry rather than something to redo.
      toast.error("Upload failed", { description: result.message });
      return;
    }

    // Dedupe is on the bytes, so re-adding a file the library already holds hands back the row
    // it already has. Both outcomes fill the slot; only this says which happened.
    toast.success(assets.some((a) => a.id === result.data.id) ? "Already in the library" : "Added to the library", {
      description: `${result.data.label} — ${result.data.width}×${result.data.height}`,
    });
    onUploaded(result.data);
  }

  return (
    <DialogContent
      aria-describedby="picker-fallback"
      className="max-h-[85vh] max-w-2xl overflow-y-auto"
    >
      <DialogTitle>Pick an image for {slot.name}</DialogTitle>
      <DialogDescription id="picker-fallback">
        {fallback
          ? `Picking nothing leaves this slot on the template's own default, “${fallback.label || fallback.kind}”.`
          : slot.defaultAssetId
            ? `This template names asset #${slot.defaultAssetId} as the default for this slot, and it is not in the library — pick one, or the visual will not render.`
            : "This template has no default for this slot, so the visual will not render until something is picked here."}
      </DialogDescription>

      {/* The search box and the grid under one keydown handler, so ArrowDown out of the field
          lands on the first result. */}
      <div className="mt-4" onKeyDown={move}>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="search by label, kind or tag"
          aria-label="search the library"
          autoFocus
          className="min-h-8 w-full rounded-input border border-border bg-transparent px-2 py-1.5 text-meta"
        />

        {assets.length === 0 ? (
          <p className="mt-3 text-meta text-muted">
            Nothing in the library yet. Upload one below and it fills this slot without leaving
            the page.
          </p>
        ) : shown.length === 0 ? (
          <p className="mt-3 text-meta text-muted">
            Nothing in the library matches “{query}”. {assets.length} asset
            {assets.length === 1 ? " is" : "s are"} stored.
          </p>
        ) : (
          /* `minmax(0,…)` and `min-w-0` on every tile, not `minmax(11rem,1fr)` copied off the
             Assets page: a `1fr` track's implicit `min-width: auto` is min-content, and a tile
             holding one long unbroken label is exactly the shape that blew `/templates` out to
             10384px. `wrap-anywhere` on the label is the other half and is not the same fix —
             `break-words` contributes no soft-wrap opportunity to min-content sizing, so it
             would let a 320-character token size the track it sits in. */
          <ul className="mt-3 grid grid-cols-[repeat(auto-fill,minmax(min(8rem,100%),1fr))] gap-2">
            {shown.map((asset) => (
              <li key={asset.id} className="min-w-0">
                <button
                  type="button"
                  data-tile
                  aria-pressed={String(asset.id) === chosen}
                  onClick={() => onPick(String(asset.id))}
                  className="flex w-full min-w-0 flex-col gap-1 rounded-input border border-border p-2 text-left transition-colors hover:bg-surface-2 aria-pressed:border-accent aria-pressed:bg-surface-2"
                >
                  {/* alt="" — the label is right underneath as real text, so the button already
                      has an accessible name and repeating it in the alt reads it twice.
                      A plain <img>: served by the backend at an arbitrary path, the case
                      next/image is wrong for. The directive must sit immediately above the tag. */}
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={assetSrc(asset)}
                    alt=""
                    className="h-20 w-full rounded-input bg-surface-2 object-contain"
                  />
                  <span className="text-caption font-medium wrap-anywhere">
                    {asset.label || `untitled ${asset.kind}`}
                  </span>
                  <span className="text-caption text-muted">
                    {asset.kind}
                    {String(asset.id) === slot.defaultAssetId ? " — template default" : ""}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}

        {/* Only where there is a default to fall back to. A slot without one has no third state:
            an omitted slot with no default is what `chosen_assets` leaves absent and `fill()`
            raises `MissingSlotValue` for, so a "no image" tile would promise a render that
            cannot happen. */}
        {slot.defaultAssetId && chosen !== "" && (
          <Button variant="outline" className="mt-3" onClick={() => onPick("")}>
            Use the template&apos;s default
          </Button>
        )}
      </div>

      {/* Upload-in-place. ponytail: one file input and the kind, no dropzone, no label or
          tag fields, no progress — the API downscales anything over 1600px and defaults the
          label to the file name, and the Assets page is where a library gets curated. `kind` is
          the one field with no honest default: it is required by `POST /assets` and filing a
          logo as a photo is wrong data, not a cosmetic choice. */}
      <form onSubmit={upload} className="mt-5 flex flex-wrap items-center gap-2 border-t border-border pt-4">
        {/* `sr-only` on the input, with the `<label>` showing the chosen file — AssetLibrary's
            finding, and it applies here for the reason this whole slice exists: the native
            widget's own chrome (a grey OS button reading "Choose file" beside "No file chosen")
            would be the one unstyled control left in this dialog, sitting between a tokenised
            Select and a tokenised Button. Every behaviour survives — a label click opens the
            file picker and the input keeps keyboard focus. `has-[:focus-visible]` is not
            decoration: the input is clipped to 1px, so the global ring would draw around nothing
            and a keyboard user would see nothing move. */}
        <label className="flex min-h-8 min-w-0 flex-1 cursor-pointer items-center rounded-input border border-dashed border-border px-2 py-1.5 text-meta transition-colors hover:bg-surface-2 has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-accent-text">
          <span className="min-w-0 truncate">{file ? file.name : "Choose an image to add"}</span>
          <input
            type="file"
            accept="image/png,image/jpeg,image/gif,image/webp"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            aria-label="image file"
            className="sr-only"
          />
        </label>
        <Select value={kind} onValueChange={(v) => setKind(v as AssetKind)}>
          <SelectTrigger aria-label="kind">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {KINDS.map((k) => (
              <SelectItem key={k} value={k}>
                {k}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button type="submit" disabled={uploading || !file}>
          {uploading ? "Uploading…" : "Upload and use"}
        </Button>
      </form>
    </DialogContent>
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
      <div className="text-xs font-medium uppercase tracking-widest text-muted">Drafts</div>

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
      <div className="mb-2 font-medium uppercase tracking-widest text-muted">Lineage</div>
      {rows.map(([label, entry]) => (
        <div key={label} className="flex justify-between gap-3 py-0.5">
          <span className="text-muted">{label}</span>
          <span>{entry ? `${entry.name} v${entry.version}` : "—"}</span>
        </div>
      ))}
      {picked.map(([slot, id]) => {
        const asset = assets?.find((a) => String(a.id) === id);
        return (
          <div key={slot} className="flex justify-between gap-3 py-0.5">
            <span className="text-muted">{slot}</span>
            <span>{asset ? `${asset.label || asset.kind} (#${id})` : `asset #${id}`}</span>
          </div>
        );
      })}
    </div>
  );
}

/** `VariantsOut` (api_drafts.py:318) — one idea written several ways, and what the whole batch
 *  cost. **There is no fourth field and that is the slice**: no score, no confidence, no
 *  recommendation, and no order but the one the drafts were written in.
 *
 *  The spend pair is `Spend` in `lib/api`, shared with `RetopicResult`; the list of variants is
 *  this page's alone and stays here. */
type Batch = { variants: Draft[] } & Spend;

/** The batch, side by side, with a Keep on each.
 *
 *  Rendered in the order the response listed them, which is generation order, and sorted by
 *  nothing. There is no "best", no score, no confidence and no sort control, and that is a
 *  decision rather than an omission: engagement spans 12.7x across ~3 samples per template here,
 *  so any aggregate this could show would be noise wearing a number. Ranking is a threshold
 *  (~300 lineage-tagged posts, currently zero), not a feature. What a human gets instead is the
 *  three concrete drafts and the lineage each was written through — which is judgement, and
 *  judgement is available today.
 *
 *  ponytail: a plain grid of cards. No diff view, no side-by-side text alignment, no per-variant
 *  rewrite or redraw, no re-roll of one slot. Ceiling: any of those once someone has used this
 *  more than a handful of times — all of them are additions to a card, not changes to this shape.
 */
function Variants({
  batch,
  assets,
  busy,
  onKeep,
}: {
  batch: Batch;
  assets: Asset[] | null;
  busy: string | null;
  onKeep: (kept: Draft) => void;
}) {
  return (
    <>
      <Card className="bg-surface-2 text-body">
        <p className="font-medium">{batch.variants.length} drafts of one idea.</p>
        <p className="mt-1 text-muted">
          In the order they were written, and in no other order — nothing here is scored, ranked
          or recommended, because ~3 posts per template cannot support a ranking. Compare the
          three and choose. This batch spent {calls(batch.llm_calls, "chat completion")} and{" "}
          {calls(batch.image_calls, "image render")}. Keeping one <strong>deletes</strong> the
          rest, so they do not sit in the Inbox as work nobody is waiting on; the kept one is
          still only a draft, and nothing publishes from here.
        </p>
      </Card>

      {/* `min-w-0` on every cell as well as on the section: the outer `[22rem_1fr]` is an
          arbitrary track and gets no implicit minimum for free, and a variant is the most likely
          place on this page for a long unbroken string — a URL in the idea — to arrive.
          `wrap-anywhere` on the text is the other half and is not the same fix: `break-words`
          does not contribute soft-wrap opportunities to min-content sizing, so it would let a
          320-character token size the track it is sitting in. Measured in Chrome at 1440 and 390
          with exactly that string. */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {batch.variants.map((v) => (
          <div
            key={v.id}
            className="min-w-0 space-y-3 rounded-lg border border-black/10 p-3 dark:border-white/15"
          >
            {/* The draft's own id, not "Variant 1". An ordinal beside three things a human is
                choosing between reads as a placing, and the id is what the Keep request carries
                anyway. */}
            <h3 className="text-sm font-medium">Draft {v.id}</h3>

            <Lineage draft={v} assets={assets} />

            <article className="whitespace-pre-wrap wrap-anywhere text-sm leading-relaxed">
              {v.full_text}
            </article>

            {v.visual_png ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={`data:image/png;base64,${v.visual_png}`}
                alt={`Visual for draft ${v.id}`}
                className="w-full rounded-md border border-black/10 dark:border-white/15"
              />
            ) : (
              <p className="text-xs text-amber-700 dark:text-amber-400">
                Visual not produced: {v.visual_error ?? "unknown"} — the text is unaffected.
              </p>
            )}

            <Button variant="outline" disabled={busy !== null} onClick={() => onKeep(v)}>
              Keep draft {v.id}
            </Button>
          </div>
        ))}
      </div>
    </>
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
  // `settings.variants_max` off `/health` — the ceiling `POST /drafts/variants` clamps to, and
  // the only honest source for what one press of Write variants will spend. `null` means
  // `/health` could not be read, and the text then says nothing about the count: falling back
  // to 3 would restore the hardcoded number this slice exists to remove, and a wrong number
  // stated confidently is worse than no number. The client never sends a `count` — reading the
  // ceiling is to *state* it; applying it stays the server's job.
  variantsMax = null,
}: {
  templates: Template[];
  assets: Asset[] | null;
  drafts: DraftSummary[] | null;
  initialDraft?: Draft | null;
  missing?: string | null;
  variantsMax?: number | null;
}) {
  const [idea, setIdea] = useState("");
  const [picked, setPicked] = useState<Picked>({ hook: null, structure: null, visual: null });
  const [reason, setReason] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft | null>(initialDraft);
  // The previous-image URL is stable while its bytes are not: redraw and restore both replace
  // what that endpoint serves. A small revision makes the browser re-read it after either
  // mutation instead of showing a cached comparison from one action ago.
  const [visualRevision, setVisualRevision] = useState(0);
  // The batch of variants being chosen between, or `null`. It and `draft` share the right column
  // and are kept mutually exclusive by every handler that sets either: a batch left standing over
  // a draft that was just written would hide a real row, which is this page's recurring bug —
  // something on screen that is not what happened.
  const [batch, setBatch] = useState<Batch | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [assetValues, setAssetValues] = useState<Record<string, string>>({});
  // The library, seeded from the prop and grown by an upload made from inside the picker. State
  // rather than the prop directly, because an asset uploaded here has to be pickable, and
  // nameable in the Lineage under the draft, without a round trip through the server component.
  //
  // `null` still means the read failed and is still not the same as an empty library — which is
  // why the upload lives only inside the picker, and the picker only opens when this is a list:
  // adding a row to a library that could not be read would turn a failed request into the claim
  // that the library holds exactly one thing.
  const [library, setLibrary] = useState<Asset[] | null>(assets);
  // The slot a picker dialog is open for, or `null`. One Dialog for the whole form rather than
  // one per slot — AssetLibrary's reason: N dialogs is N overlays and N focus traps for one
  // decision.
  //
  // Nulling it on close is what unmounts `AssetPicker`, which is what resets the search box and
  // the upload form for the next open. Radix's own Presence does not do that for us here: it
  // unmounts the portalled content, but `AssetPicker` is our element and its hooks live one
  // fiber above the boundary, so holding the slot across a close keeps the previous search
  // string on screen the next time the dialog opens. Measured, not assumed.
  const [picking, setPicking] = useState<ImageSlot | null>(null);

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
    <div className="mt-8">
      <div className="-mx-6 flex flex-wrap items-center gap-x-6 gap-y-2 border-y border-border bg-surface px-6 py-3 font-mono text-caption text-muted">
        <span><strong className="font-medium text-text">{approved.length}</strong> approved templates</span>
        <span><strong className="font-medium text-text">{drafts?.length ?? "—"}</strong> drafts</span>
        <span>{draft ? `draft #${draft.id} open` : "new composition"}</span>
        <span className="ml-auto">Nothing publishes from here</span>
      </div>

      <div className="mt-10 grid gap-10 lg:grid-cols-[22rem_minmax(0,1fr)]">
      {/* `min-w-0` for the same reason the draft column carries it, and here it is the one that
          can actually move: at 390px this grid is a single column, so this section is the `1fr`
          track and a draft idea in the list below is the longest string on the page. */}
      <section className="min-w-0 space-y-3">
        <div className="border-b border-text pb-3">
          <p className="font-mono text-caption uppercase tracking-[0.12em] text-muted">Compose</p>
          <h2 className="mt-1 text-head font-medium">Start with the point</h2>
        </div>
        <textarea
          value={idea}
          onChange={(e) => setIdea(e.target.value)}
          rows={7}
          placeholder="An idea, a finding, a link — what is this post about?"
          className={field}
        />

        <div className="rounded-card border border-border bg-surface p-3">
          <p className="mb-3 font-mono text-caption uppercase tracking-[0.12em] text-muted">Template path</p>
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
            <SelectTrigger aria-label={kind} className="mb-2 w-full last:mb-0">
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
        </div>

        {slots.length > 0 && (
          /* The asset picker. Only appears once a visual with image slots is chosen — the
             slots are read off that template, so there is nothing to ask about before then,
             and a visual left to be suggested is filled from the template's own defaults on
             the server instead.

             One button per slot, opening a searchable grid — US-015, and it replaces the
             `Select` per slot that shipped alongside the three template selects. A dropdown of
             text rows is fine at 2 assets and unusable at 200: the thing being chosen is a
             picture, so the control has to show pictures.

             ponytail: the button is a button, not a Radix anything. It opens one dialog and
             shows what is currently picked; there is no combobox behaviour to inherit. */
          <div className="space-y-3 rounded-lg border border-black/10 p-3 dark:border-white/15">
            <div className="text-xs font-medium uppercase tracking-widest text-muted">
              Images — {slots.length} slot{slots.length === 1 ? "" : "s"}
            </div>

            {library === null ? (
              <p className="text-sm text-amber-700 dark:text-amber-400">
                The asset library could not be read, so there is nothing to pick from. This is a
                failed request, not an empty library — the visual will not render until it is
                readable.
              </p>
            ) : (
              <>
                {library.length === 0 && (
                  <p className="text-sm text-amber-700 dark:text-amber-400">
                    Nothing in the library yet. This template has an image slot, so it cannot
                    render until an asset is added — the picker below uploads one without
                    leaving this page.
                  </p>
                )}

                {slots.map((slot) => {
                  const chosen = library.find((a) => String(a.id) === assetValues[slot.name]);
                  return (
                    /* `aria-label={slot.name}` on the button and the asset's label as its text:
                       the same two properties the Select trigger carried, so what a reader —
                       and a test — anchors on does not change with the control. */
                    <button
                      key={slot.name}
                      type="button"
                      aria-label={slot.name}
                      onClick={() => setPicking(slot)}
                      className="flex min-h-8 w-full min-w-0 items-center gap-2 rounded-input border border-border px-2 py-1.5 text-left text-meta transition-colors hover:bg-surface-2"
                    >
                      {chosen ? (
                        // A plain <img>: served by the backend at an arbitrary path, the case
                        // next/image is wrong for. The directive has to sit immediately above
                        // the tag — anything between them makes it an unused directive.
                        // eslint-disable-next-line @next/next/no-img-element
                        <img
                          src={assetSrc(chosen)}
                          alt=""
                          className="h-10 w-10 shrink-0 rounded-md border border-black/10 object-contain dark:border-white/15"
                        />
                      ) : (
                        <span
                          aria-hidden
                          className="h-10 w-10 shrink-0 rounded-md border border-dashed border-black/15 dark:border-white/20"
                        />
                      )}
                      {/* `min-w-0` + `truncate`: this row is a flex line inside the `1fr`
                          column, so a long unbroken label has to be cut rather than allowed to
                          size the track. */}
                      <span className="min-w-0 flex-1 truncate">
                        {chosen
                          ? `${chosen.label || `untitled ${chosen.kind}`} (${chosen.kind})`
                          : `${slot.name} — pick an asset`}
                      </span>
                    </button>
                  );
                })}
              </>
            )}
          </div>
        )}

        {/* Mounted only while a slot is being picked for, so the search box and the upload form
            are fresh on every open with no effect resetting them, and only one picker's state
            exists at a time. `Dialog.Root` renders no element of its own, so this sits inside
            the column's `space-y-3` without adding a gap. */}
        <Dialog open={picking !== null} onOpenChange={(open) => !open && setPicking(null)}>
          {picking && (
            <AssetPicker
              slot={picking}
              assets={library ?? []}
              chosen={assetValues[picking.name] ?? ""}
              onPick={(assetId) => {
                // `noneOf` for the same reason `assetPayload` still runs it: `""` is what an
                // unpicked slot is, and one spelling of "nothing here" is one fewer way for a
                // slot to be sent as a pick of nothing that shadows the template default.
                setAssetValues({ ...assetValues, [picking.name]: noneOf(assetId) });
                setPicking(null);
              }}
              onUploaded={(asset) => {
                // Prepended, and de-duplicated by id: `POST /assets` answers with the row it
                // already had when the bytes match, and a second copy in this list would be a
                // library this page invented.
                setLibrary([asset, ...(library ?? []).filter((a) => a.id !== asset.id)]);
                setAssetValues({ ...assetValues, [picking.name]: String(asset.id) });
                setPicking(null);
              }}
            />
          )}
        </Dialog>

        <div className="flex flex-wrap gap-2 border-t border-border pt-4">
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
                // The visual is deliberately NOT set here — `chooseVisual` below owns it.
                // Writing it in both places is the duplicate that hid a mutation: with this
                // literal also carrying `visual`, `chooseVisual`'s own `setPicked` had zero
                // coverage, because the Select is the only other path into it and a Radix
                // trigger cannot be driven in jsdom. One writer per field.
                setPicked((current) => ({
                  ...current,
                  hook: s.hook.id,
                  structure: s.structure.id,
                }));
                // A suggested visual brings its own image slots, so the picker has to be reset
                // to that template's defaults exactly as choosing one by hand does — through
                // `chooseVisual` itself rather than through a second copy of its body. This was
                // two copies of the same two lines, and the copies are not equally covered: a
                // Radix trigger cannot be driven in jsdom, so the Select path is reachable in a
                // test *only* through here. A mutation that broke `chooseVisual` was caught by
                // nothing while the duplicate stood.
                chooseVisual(s.visual.id);
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
              if (d) {
                setDraft(d);
                setBatch(null);
              }
            }}
          >
            {busy === "/drafts" ? "Writing…" : "Generate draft"}
          </Button>
          {/* Its own button, and the label says what it does rather than "Generate": one press
              here is several completions and several renders, so it must be chosen, never
              arrived at. Disabled without an idea and while anything is in flight, for the
              reason every spending button on this page is — a double click is a second batch.
              `{ idea }` alone: the route varies the templates itself and ignores a hook_id
              silently, so sending `payload` would be this page claiming an influence it does not
              have. No `count` either — the ceiling is `settings.variants_max` and the server
              applies it; `/health` reports it so the text below can say what a press costs,
              which is a different thing from asking for a number.
              ponytail: no confirm dialog. Ceiling: one if a batch is ever written by accident. */}
          <Button
            variant="outline"
            disabled={!idea.trim() || busy !== null}
            onClick={async () => {
              const b = await call<Batch>("/drafts/variants", { idea });
              if (b) {
                setBatch(b);
                setDraft(null);
              }
            }}
          >
            {busy === "/drafts/variants" ? "Writing variants…" : "Write variants"}
          </Button>
        </div>

        {/* What the press will cost, before it is pressed. The response reports `llm_calls` and
            `image_calls` afterwards and still does — that is the receipt, and a receipt is not a
            price. `variantsMax` is the server's own ceiling; with `/health` unread the sentence
            stops at the per-variant rate, which is true at any ceiling. */}
        <p className="text-caption text-muted">
          Write variants writes this idea several times over, each through a different approved
          hook, structure and visual, then shows them side by side to keep one. It ignores the
          three selects above on purpose — varying the combination is the point — and it spends a
          chat completion and a render per variant
          {variantsMax === null
            ? "."
            : `, up to ${variantsMax} of them — at most ${calls(variantsMax, "chat completion")} and ${calls(variantsMax, "image render")} for one press.`}
        </p>

        {reason && <p className="text-xs text-muted">{reason}</p>}
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
        <div className="border-b border-text pb-3">
          <p className="font-mono text-caption uppercase tracking-[0.12em] text-muted">
            {batch ? "Choose" : draft ? "Draft detail" : "Output"}
          </p>
          <h2 className="mt-1 text-head font-medium">
            {batch ? "Keep one direction" : draft ? `Draft #${draft.id}` : "A reviewable draft appears here"}
          </h2>
        </div>
        {/* A draft in hand wins over both notices, and the order is load-bearing rather than
            arbitrary. `missing` is a prop and never clears, but the left column keeps working
            on that route: landing on `?draft=999` from a stale link and clicking Generate
            writes a real row, and with the alert tested first the column would go on reporting
            a 404 for a draft that now exists on screen. A write that renders as a failure is
            the same conflation as an error that renders as an empty state. */}
        {batch ? (
          /* A batch on screen outranks everything below it, including `missing`: it is the most
             recent thing that happened and it is the one state on this page with an unfinished
             decision in it. */
          <Variants
            batch={batch}
            assets={library}
            busy={busy}
            onKeep={async (kept) => {
              const d = await call<Draft>("/drafts/variants/keep", {
                keep_id: kept.id,
                // Every other variant, and never the kept one — the backend 422s on an id in
                // both lists rather than guessing which was meant.
                discard_ids: batch.variants.filter((v) => v.id !== kept.id).map((v) => v.id),
              });
              // Only on success. A 409 means a discard is already in Zernio and the route
              // deleted nothing — not even the rows it could have — so the batch on screen is
              // still exactly what is in the database, and clearing it would strand drafts
              // nobody can see into Inbox queue 2. `call` has already shown the API's own words.
              if (d) {
                setDraft(d);
                setBatch(null);
              }
            }}
          />
        ) : draft ? (
          <>
            <Lineage draft={draft} assets={library} />

            {draft.zernio_post_id && (
              <p className="text-xs text-muted">
                In Zernio as a draft ({draft.zernio_post_id}). Publishing stays a human act.
              </p>
            )}

            <article className="whitespace-pre-wrap border-y border-border py-6 text-[15px] leading-7">
              {draft.full_text}
            </article>

            {draft.visual_png ? (
              <div
                className={
                  draft.has_previous_visual
                    ? "grid max-w-3xl gap-4 sm:grid-cols-2"
                    : "max-w-md"
                }
              >
                <figure className="space-y-2">
                  <figcaption className="font-mono text-caption uppercase tracking-label text-muted">
                    Current
                  </figcaption>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={`data:image/png;base64,${draft.visual_png}`}
                    alt="Generated visual"
                    className="w-full rounded-card border border-border"
                  />
                </figure>
                {draft.has_previous_visual && (
                  <figure className="space-y-2">
                    <figcaption className="font-mono text-caption uppercase tracking-label text-muted">
                      Before redraw
                    </figcaption>
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={`${API_BASE}/drafts/${draft.id}/previous-visual?revision=${visualRevision}`}
                      alt="Previous visual"
                      className="w-full rounded-card border border-border"
                    />
                  </figure>
                )}
              </div>
            ) : (
              <p className="text-sm text-amber-700 dark:text-amber-400">
                Visual not produced: {draft.visual_error ?? "unknown"} — the text is unaffected.
              </p>
            )}

            {draft.visual_png && draft.visual_error && (
              <p role="status" className="text-sm text-amber-700 dark:text-amber-400">
                Redraw failed, so the last working visual is still in place: {draft.visual_error}
              </p>
            )}

            <div className="flex flex-wrap gap-2">
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
                  if (d) {
                    setDraft(d);
                    setVisualRevision((revision) => revision + 1);
                  }
                }}
              >
                Redraw visual
              </Button>
              {draft.has_previous_visual && (
                <Button
                  variant="outline"
                  disabled={busy !== null}
                  onClick={async () => {
                    const d = await call<Draft>(`/drafts/${draft.id}/restore-visual`);
                    if (d) {
                      setDraft(d);
                      setVisualRevision((revision) => revision + 1);
                    }
                  }}
                >
                  Restore previous visual
                </Button>
              )}
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
             this — which the PRD names as an AA failure — is now --text-muted.
             A plain Card, deliberately: `bg-surface-2` is the raised brand cream and it sat
             here as a tint on the one state that is correct on every visit to a fresh Studio,
             beside a column whose other two states are a real failure (the `missing` alert) and
             a real warning. Neutral is what this is. */
          <Card className="border-dashed p-6 text-body">
            <p className="text-head font-medium">No draft yet.</p>
            <p className="mt-2 max-w-2xl text-muted">
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
    </div>
  );
}
