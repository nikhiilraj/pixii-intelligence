"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import { postJson, type Asset, type AssetKind } from "@/lib/api";

/** The shelves an asset can land on — `AssetKind` in backend/app/models/asset.py.
 *
 *  A kind is a filter, not a permission, and it is **the operator's call** — nothing infers
 *  one from the post. `product` rather than `photo` is the default because the media on a
 *  corpus post is overwhelmingly a made image rather than a photograph, and a wrong default on
 *  a filter costs a re-tag rather than anything real. */
const KINDS: AssetKind[] = ["logo", "product", "screenshot", "brand", "photo"];

/** Promote this post's downloaded media into the asset library — `POST /assets/promote`.
 *
 *  **Here rather than on the operations screen**, because the operation acts on the post you
 *  are looking at. The route takes a list of post ids and this sends a list of one: a bulk
 *  selection UI over the corpus table is a real thing to want and is not this, and building it
 *  would have meant a multi-select on a table nobody has asked to multi-select.
 *
 *  Promotion is what makes the asset picker useful at all. The 107 posts arrived with their
 *  media downloaded; the library starts empty, so without this a visual template with an
 *  `image_url` slot has nothing real to offer.
 *
 *  ponytail: shaped after `ExcludeToggle` — one client component, one `postJson`, a `busy`
 *  flag, `router.refresh()` on success. The confirmation is here rather than absent because
 *  this writes a file into the library and the dialog is where the kind is chosen; it is not
 *  the paid-external-call confirmation the operations screen uses, and it does not pretend to
 *  be. Ceiling: multi-post promotion the day somebody wants more than one at a time. */
export default function PromoteMedia({
  postId,
  /** `true` for `.mp4`/`.mov`/`.webm`. The route answers 422 for these — 3 of the 62 files in
   *  `media/` are video and `local_media_path` points straight at them — so the refusal is
   *  said here, before a button that could only fail. */
  isVideo,
}: {
  postId: number;
  isVideo: boolean;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [kind, setKind] = useState<AssetKind>("product");
  const [tags, setTags] = useState("");
  const [promoted, setPromoted] = useState<Asset | null>(null);

  async function promote() {
    setBusy(true);
    const result = await postJson<Asset[]>("/assets/promote", {
      post_ids: [postId],
      kind,
      // Split and trimmed here rather than sent as one string: `tags` is a list on the wire,
      // and a single "a, b, c" element is a tag nobody can filter on.
      tags: tags
        .split(",")
        .map((tag) => tag.trim())
        .filter(Boolean),
    });
    setBusy(false);
    setOpen(false);

    if (!result.ok) {
      toast.error("Could not promote this post's media", { description: result.message });
      return;
    }
    // One row per post named, in the order given — so one post yields one asset.
    setPromoted(result.data[0] ?? null);
    router.refresh();
  }

  if (isVideo) {
    return (
      <p className="mt-3 text-caption text-muted">
        This post&apos;s media is a video. The asset library serves images, so there is nothing
        here to promote — the API refuses it rather than storing a file it cannot show.
      </p>
    );
  }

  return (
    <div className="mt-3">
      {promoted ? (
        <p className="text-caption text-muted">
          Promoted into the library as{" "}
          <span className="font-medium">{promoted.label}</span>{" "}
          <Badge variant="neutral">{promoted.kind}</Badge> — {promoted.width}×{promoted.height}.
          Studio&apos;s asset picker can use it now.
        </p>
      ) : (
        <Button variant="outline" disabled={busy} onClick={() => setOpen(true)}>
          Promote this image to Assets…
        </Button>
      )}

      <Dialog open={open} onOpenChange={(next) => !next && setOpen(false)}>
        {open && (
          <DialogContent aria-describedby="promote-consequence">
            <DialogTitle>Promote this image to Assets?</DialogTitle>
            <DialogDescription id="promote-consequence">
              This copies the post&apos;s downloaded image into the asset library, where visual
              templates can use it. The post is unchanged and nothing leaves this machine.
            </DialogDescription>

            <div className="mt-4 space-y-3">
              <label className="block space-y-1">
                <span className="text-caption font-medium uppercase tracking-label text-muted">
                  Kind
                </span>
                {/* A native select. The kind is a filter on the library, not a permission —
                    nothing behaves differently because of it — so it is chosen here and can be
                    got wrong without consequence. */}
                <select
                  value={kind}
                  onChange={(event) => setKind(event.target.value as AssetKind)}
                  aria-label="asset kind"
                  className="min-h-8 w-full rounded-input border border-border bg-transparent px-2 py-1.5 text-meta"
                >
                  {KINDS.map((option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </select>
              </label>

              <label className="block space-y-1">
                <span className="text-caption font-medium uppercase tracking-label text-muted">
                  Tags
                </span>
                <input
                  value={tags}
                  onChange={(event) => setTags(event.target.value)}
                  placeholder="comma, separated, optional"
                  aria-label="tags"
                  className="min-h-8 w-full rounded-input border border-border bg-transparent px-2 py-1.5 text-meta"
                />
              </label>
            </div>

            <p className="mt-3 text-caption text-muted">
              Safe to repeat: promotion goes through the same hash-and-store path an upload
              does, so a post whose image is already in the library yields the row it already
              has rather than a second copy.
            </p>

            <div className="mt-5 flex flex-wrap gap-2 border-t border-border pt-4">
              <Button disabled={busy} onClick={promote}>
                {busy ? "Promoting…" : "Confirm promote"}
              </Button>
              <Button variant="outline" disabled={busy} onClick={() => setOpen(false)}>
                Cancel
              </Button>
            </div>
          </DialogContent>
        )}
      </Dialog>
    </div>
  );
}
