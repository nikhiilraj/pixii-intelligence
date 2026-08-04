"use client";

import { useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Dialog,
  DialogClose,
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
import { assetSrc, getJson, postForm, postJson, type Asset, type AssetKind } from "@/lib/api";

const KINDS: AssetKind[] = ["logo", "product", "screenshot", "brand", "photo"];

/* Radix rejects `Select.Item value=""` — it reserves the empty string for "nothing selected".
 * The kind filter uses "" for "no filter", so it needs a stand-in item value, and that value
 * must never reach the query: `GET /assets?kind=__all__` is a 422 naming the five kinds, so
 * the filter would report a validation error for the one option meaning "don't filter".
 *
 * ponytail: two lines re-declared rather than imported from `posts/Explorer`, which exports
 * the same pair. Importing them would pull Explorer's recharts import into this page's bundle
 * for the sake of a constant; lifting them into `lib/` would edit a file US-004 owns. The
 * pair is tested here the same way it is tested there.
 */
export const ALL = "__all__";
export const noFilter = (value: string) => (value === ALL ? "" : value);

export type AssetFilters = { kind: string; tag: string };

/** The `/assets` query string for a set of filters. A pure function so "filtering asks the API
 *  for the right thing" is testable without driving a Radix listbox in jsdom. An unset filter
 *  is omitted entirely — `?kind=` is a 422 and `?tag=` matches the rows carrying an empty
 *  tag, which is not what an empty control means. */
export function assetQuery(f: AssetFilters): string {
  const params = new URLSearchParams();
  if (f.kind) params.set("kind", f.kind);
  if (f.tag) params.set("tag", f.tag);
  return params.toString();
}

/** `/assets` with the filters applied, and no trailing `?` when there are none. */
export function assetPath(f: AssetFilters): string {
  const query = assetQuery(f);
  return query ? `/assets?${query}` : "/assets";
}

/** The comma-separated tag field, as the repeated `tags` form fields the API reads. */
export function tagList(raw: string): string[] {
  return raw
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);
}

/** What a screen reader is told about the picture. Never empty: a grid of images whose alt is
 *  the filename digest tells a non-sighted reviewer nothing, and an empty alt claims the image
 *  is decorative when it is the entire content of the card. */
function altOf(asset: Asset): string {
  return asset.label ? `${asset.label} — ${asset.kind}` : `untitled ${asset.kind}`;
}

export default function AssetLibrary({ initial }: { initial: Asset[] }) {
  const [assets, setAssets] = useState(initial);
  const [filters, setFilters] = useState<AssetFilters>({ kind: "", tag: "" });
  // The API's own message when the last read failed, `null` when it did not. Was a boolean: it
  // only gated the empty state, and the message it withheld went to a toast which then faded,
  // leaving a library that had not filtered and nothing on screen saying so.
  const [failed, setFailed] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // The upload form.
  const fileInput = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [kind, setKind] = useState<AssetKind>("logo");
  const [label, setLabel] = useState("");
  const [tags, setTags] = useState("");
  const [dropping, setDropping] = useState(false);

  // Deleting: one Dialog for the whole grid, and the asset awaiting confirmation is what
  // opens it. A Dialog per card would mount N overlays and N focus traps for one decision.
  const [pending, setPending] = useState<Asset | null>(null);

  // Suggestions for the tag field, from the library as it arrived. Deliberately a `datalist`
  // on a text input rather than a Select: `tag` is a free string at the API, a tag minted by
  // the upload form seconds ago would not be in a fixed option list, and a stale *suggestion*
  // costs nothing while a stale *option list* makes a real tag unreachable.
  const knownTags = useMemo(
    () => Array.from(new Set(initial.flatMap((a) => a.tags))).sort(),
    [initial],
  );

  // Reads are user actions — a click or a change — so they refetch from the handler. React
  // 19's `set-state-in-effect` rule forbids syncing server state through an effect, and the
  // established pattern here (posts/Explorer) is the handler.
  //
  // The tag filter is a text input, so one deliberate filter is several requests and they can
  // finish out of order: the response for "crea" arriving after the one for "cream" would leave
  // the grid showing a filter nobody asked for while the field says otherwise. Only the newest
  // request is allowed to write, which is a wrong-answer guard rather than a tidiness one.
  // ponytail: a sequence number, not a debounce and not AbortController. `GET /assets` is a
  // local read of a handful of rows, so the extra requests cost nothing worth spending code on;
  // what mattered was that the stale ones cannot win. Debounce it if the library ever paginates.
  const latestRead = useRef(0);

  async function reload(next: AssetFilters) {
    setFilters(next);
    const ticket = ++latestRead.current;
    const result = await getJson<Asset[]>(assetPath(next));
    if (ticket !== latestRead.current) return;

    if (result.ok) {
      setAssets(result.data);
      setFailed(null);
      return;
    }
    // The rows below are kept and the empty-state claim is withheld: after a failed request
    // there is no data to say "the library is empty" about.
    setFailed(result.message);
    toast.error("Could not read the library", {
      description: `${result.message} — the assets below are the previous result.`,
    });
  }

  async function upload(event: React.FormEvent) {
    event.preventDefault();
    if (!file) return;

    const body = new FormData();
    body.append("file", file);
    body.append("kind", kind);
    if (label) body.append("label", label);
    // Repeated fields, not a joined string — `tags: list[str] | None` from `Form()` reads the
    // key once per value, and one comma-joined field would store a single tag called
    // "brand,orange".
    for (const tag of tagList(tags)) body.append("tags", tag);

    setBusy(true);
    const result = await postForm<Asset>("/assets", body);
    setBusy(false);

    if (!result.ok) {
      // The API's own `detail` — "not a readable image", "not a format this library serves",
      // and the 422 listing the five kinds are all written for a reader. Surfacing them is the
      // difference between a fixable refusal and a file that silently never arrived. The form
      // keeps its contents, so there is something to retry.
      toast.error("Upload failed", { description: result.message });
      return;
    }

    // Dedupe is on the uploaded bytes, so re-adding a file already in the library hands back
    // the row it already has rather than a second copy. Saying so is the only way the operator
    // can tell that outcome from a fresh upload — the grid looks identical either way.
    const known = assets.some((a) => a.id === result.data.id);
    toast.success(known ? "Already in the library" : "Added to the library", {
      description: known
        ? `Those exact bytes are already stored as “${result.data.label}”.`
        : `${result.data.label} — ${result.data.width}×${result.data.height}`,
    });

    setFile(null);
    setLabel("");
    setTags("");
    if (fileInput.current) fileInput.current.value = "";
    // Refetched rather than prepended: the new row may not match the active filters, and
    // showing it anyway would misdescribe what the filter says. No optimistic update.
    await reload(filters);
  }

  async function confirmDelete() {
    if (!pending) return;
    setBusy(true);
    const result = await postJson<{ deleted: number }>(`/assets/${pending.id}`, undefined, "DELETE");
    setBusy(false);

    if (!result.ok) {
      // A 409 arrives here naming the draft or template that still holds the asset. It is the
      // one message on this page that is genuinely load-bearing, so the dialog stays open with
      // the refusal beside the button that caused it.
      // `kind` before `status`: a network failure carries no status at all, and reaching for
      // one is what the discriminated union exists to prevent.
      const referenced = result.kind === "http" && result.status === 409;
      toast.error(referenced ? "Still in use" : "Delete failed", { description: result.message });
      return;
    }

    toast.success("Deleted", { description: `“${altOf(pending)}” and its file are gone.` });
    setPending(null);
    await reload(filters);
  }

  function dropped(event: React.DragEvent) {
    event.preventDefault();
    setDropping(false);
    const dropped = event.dataTransfer.files?.[0];
    if (dropped) setFile(dropped);
  }

  return (
    <>
      {/* ponytail: `<input type="file">` plus the three native drag events. No drag-and-drop
          library, no cropper, no client-side resize — the API downscales anything over
          1600px on the long edge. Ceiling: reach for a library when multi-file upload with
          per-file progress is wanted. */}
      <div className="-mx-6 mt-8 flex flex-wrap items-center justify-between gap-3 border-y border-border bg-surface px-6 py-3">
        <p className="text-meta"><span className="font-mono font-semibold tabular-nums">{assets.length}</span> reusable asset{assets.length === 1 ? "" : "s"}</p>
        <p className="text-caption text-muted">Uploads stay drafts until a visual uses them.</p>
      </div>

      <form onSubmit={upload} className="mt-8">
        <div className="mb-3 flex items-baseline justify-between gap-4">
          <h2 className="text-head font-semibold">Add to the library</h2>
          <span className="text-caption text-muted">One file at a time</span>
        </div>
        <label
          onDragOver={(e) => {
            e.preventDefault();
            setDropping(true);
          }}
          onDragLeave={() => setDropping(false)}
          onDrop={dropped}
          /* `has-[:focus-visible]` is not decoration: the input inside is `sr-only`, so the
             global 2px ring in globals.css would draw around a 1px clipped box and a keyboard
             user tabbing here would see nothing at all move. The dropzone borrows the ring on
             the input's behalf, with the same width, colour and offset the rule uses. */
          className={`flex min-h-24 cursor-pointer flex-col items-center justify-center gap-1 rounded-card border border-dashed px-4 py-6 text-center transition-colors has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-accent-text ${
            // Accent as a border and a fill behind no text — never as text. #F2610C is
            // 3.09:1 and fails AA for words.
            dropping ? "border-accent bg-surface-2" : "border-border hover:bg-surface-2"
          }`}
        >
          <span className="text-body font-medium">
            {file ? file.name : "Drop an image here, or choose a file"}
          </span>
          <span className="text-caption text-muted">
            PNG, JPEG, GIF or WEBP. Anything over 1600px on the long edge is stored downscaled.
          </span>
          {/* `sr-only`, not styled. The native widget's own chrome — a grey OS button reading
              "Choose file" beside the words "No file chosen" — is the last unstyled control in
              the app, and it is redundant here: the `<label>` wrapping it already renders the
              chosen file's name, already accepts a drop, and already activates the picker when
              clicked. Hiding it visually keeps every behaviour (label-click, keyboard focus,
              the three drag handlers, `fileInput.current.value = ""` on reset) and removes the
              chrome outright, which `file:`-variant styling cannot do — that only restyles the
              button and leaves "No file chosen" on screen.
              ponytail: one utility class. Ceiling: a real Button that calls
              `fileInput.current.click()` the day this needs to look like a button rather than a
              dropzone. */}
          <input
            ref={fileInput}
            type="file"
            accept="image/png,image/jpeg,image/gif,image/webp"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="sr-only"
            aria-label="image file"
          />
        </label>

        <div className="mt-3 flex flex-wrap items-center gap-2">
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

          <input
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="label (defaults to the file name)"
            aria-label="label"
            className="min-h-8 flex-1 rounded-input border border-border bg-transparent px-2 py-1.5 text-meta"
          />
          <input
            value={tags}
            onChange={(e) => setTags(e.target.value)}
            placeholder="tags, comma separated"
            aria-label="tags"
            className="min-h-8 flex-1 rounded-input border border-border bg-transparent px-2 py-1.5 text-meta"
          />

          <Button type="submit" disabled={busy || !file}>
            {busy ? "Uploading…" : "Upload"}
          </Button>
        </div>
      </form>

      <div className="mt-10 flex flex-wrap items-center gap-2 border-y border-border bg-surface px-3 py-3">
        <Select
          value={filters.kind || ALL}
          onValueChange={(v) => reload({ ...filters, kind: noFilter(v) })}
        >
          <SelectTrigger aria-label="filter by kind">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>all kinds</SelectItem>
            {KINDS.map((k) => (
              <SelectItem key={k} value={k}>
                {k}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <input
          value={filters.tag}
          onChange={(e) => reload({ ...filters, tag: e.target.value })}
          list="asset-tags"
          placeholder="filter by tag"
          aria-label="filter by tag"
          className="min-h-8 rounded-input border border-border bg-transparent px-2 py-1.5 text-meta"
        />
        <datalist id="asset-tags">
          {knownTags.map((t) => (
            <option key={t} value={t} />
          ))}
        </datalist>

        {/* aria-live because the count is the only confirmation a filter took effect, and it
            changes away from the control that caused it. */}
        <span className="text-meta text-muted" aria-live="polite">
          {assets.length} asset{assets.length === 1 ? "" : "s"}
        </span>
      </div>

      {/* The failed read, persistently, with the retry that re-issues it. `reload(filters)`
          changes nothing about the filter — it rebuilds the same path and requests it again,
          and the ticket guard above still applies, so a retry racing a keystroke cannot write a
          stale result. The toast stays too: it fires beside the control just used, and this card
          sits above a grid that may be a scroll away. */}
      {failed && (
        <Card role="alert" className="mt-6 border-danger/40 bg-danger/10 text-body">
          <p className="font-medium">
            Could not read the library — the assets below are the previous result.
          </p>
          <p className="mt-1 text-muted">{failed}</p>
          <Button variant="outline" className="mt-3" onClick={() => reload(filters)}>
            Try again
          </Button>
        </Card>
      )}

      {assets.length > 0 ? (
        <ul className="mt-6 grid grid-cols-[repeat(auto-fill,minmax(13rem,1fr))] gap-4">
          {assets.map((asset) => (
            <li key={asset.id}>
              <Card className="group flex h-full flex-col gap-3 border-border-subtle transition-colors hover:border-border">
                {/* A plain <img>: these are served by the backend at an arbitrary path, which
                    is the case next/image is wrong for (US-003's finding). The eslint
                    directive has to sit immediately above the tag — a comment between them
                    makes it an unused directive. */}
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={assetSrc(asset)}
                  alt={altOf(asset)}
                  className="h-36 w-full rounded-input bg-surface-2 object-contain p-2"
                />
                <div className="flex items-start justify-between gap-2">
                  <span className="text-meta font-medium break-words">{asset.label || "—"}</span>
                  <Badge>{asset.kind}</Badge>
                </div>
                <span className="text-caption tabular-nums text-muted">
                  {asset.width}×{asset.height}
                </span>
                {asset.tags.length > 0 && (
                  <span className="text-caption text-muted break-words">
                    {asset.tags.join(" · ")}
                  </span>
                )}
                <Button
                  variant="outline"
                  className="mt-auto"
                  onClick={() => setPending(asset)}
                  aria-label={`delete ${altOf(asset)}`}
                >
                  Delete
                </Button>
              </Card>
            </li>
          ))}
        </ul>
      ) : (
        // Suppressed while `failed` is set: "no assets" is a claim about the library, and a
        // failed read has no library to make it about.
        !failed && (
          <Card className="mt-6 bg-surface-2">
            <p className="text-body font-medium">
              {filters.kind || filters.tag
                ? "No assets match that filter."
                : "No assets yet — upload a logo or a product shot."}
            </p>
            <p className="mt-1 text-meta text-muted">
              {filters.kind || filters.tag
                ? "Clear the filter to see the whole library."
                : "An image slot on a visual template is filled from this library, so a" +
                  " template with a logo slot cannot render until something is here."}
            </p>
          </Card>
        )
      )}

      {/* Nothing deletes without passing through here. The Dialog is controlled by `pending`
          rather than wrapped around a per-card trigger, so there is exactly one focus trap on
          the page; Radix restores focus to whatever was focused when it opened, which is the
          Delete button of the card in question. */}
      <Dialog open={pending !== null} onOpenChange={(open) => !open && setPending(null)}>
        <DialogContent aria-describedby="delete-consequence">
          <DialogTitle>Delete “{pending ? altOf(pending) : ""}”?</DialogTitle>
          <DialogDescription id="delete-consequence">
            This deletes the file from disk, and the library holds the only copy. Drafts that
            already rendered keep their image — the picture is stored on the draft itself — but
            this asset can never be picked again. If a draft or a template still points at it,
            the delete is refused and names which one.
          </DialogDescription>
          <div className="mt-5 flex justify-end gap-2">
            <DialogClose asChild>
              <Button variant="outline">Keep it</Button>
            </DialogClose>
            <Button onClick={confirmDelete} disabled={busy}>
              {busy ? "Deleting…" : "Delete from disk"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
