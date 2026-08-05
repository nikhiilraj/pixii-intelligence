"use client";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { RESEARCH_MODES, type ResearchMode } from "@/lib/api";

/* One depth control, in `components/` rather than in `studio/`, because two screens ask the
 * same question of the same backend field: Studio, for Generate and Write variants, and the
 * post page's re-topic form. `IdeaIn.research_mode` is one contract and two spellings of it
 * would drift — the failure would be quiet, since a control that never sends the key looks
 * exactly like one whose default is Auto.
 *
 * It is deliberately a module of its own and not part of `Studio.tsx`: importing anything from
 * that file pulls a 1,400-line client component into the post page's bundle for one dropdown,
 * which is the reason `assetSrc` was lifted into `lib/api` rather than imported from
 * `AssetLibrary`. */

/** How much looking-up each depth does, in what it means rather than in how much it costs.
 *
 *  Shared by the control that asks for a depth and the panel that reports the one that ran, so a
 *  reader meets the same words twice. **None of these is described as better, safer or
 *  recommended.** `DEPTH` in `models/research.py` orders the three for exactly one comparison —
 *  is a request below the floor — and its own comment refuses every other reading; a label
 *  reading "deeper is safer" would be a ranking in prose. */
export const MODE_MEANING: Record<ResearchMode, string> = {
  none: "no external lookup",
  light: "confirm a few current facts against a few sources",
  deep: "multi-query investigation, including what the sources disagree about",
};

/** How much research the operator is asking for, as a control holds it.
 *
 *  `"auto"` is this module's word and never leaves it — see `modePayload`. It is deliberately
 *  not the string `"none"`: `none` is a request to look nothing up, which a factual brief
 *  refuses, and `auto` is expressing no preference at all. Sending one for the other would turn
 *  the default state of a dropdown into a request that is refused on any factual idea. */
export type DepthChoice = "auto" | ResearchMode;

export const DEPTHS: DepthChoice[] = ["auto", ...RESEARCH_MODES];

/** What each option says on screen.
 *
 *  Auto is first because it is the default, **not because it is recommended** — that word is not
 *  available to this application, and neither is an arrangement implying one option is the good
 *  one. Each depth is described by what it does. */
export const DEPTH_LABEL: Record<DepthChoice, string> = {
  auto: "Auto — let the detected floor decide",
  none: `None — ${MODE_MEANING.none}`,
  light: `Light — ${MODE_MEANING.light}`,
  deep: `Deep — ${MODE_MEANING.deep}`,
};

/** The depth as the API takes it, or `{}` for "do not send this key".
 *
 *  Auto sends nothing, which is what makes it Auto: `research_mode: null` and an absent key mean
 *  the same thing to `IdeaIn`, but a key that is present says a client had an opinion. The one
 *  that matters is that neither is `"none"`. */
export function modePayload(depth: DepthChoice): { research_mode?: ResearchMode } {
  return depth === "auto" ? {} : { research_mode: depth };
}

/** The control, with the one sentence that has to accompany it.
 *
 *  The sentence is not decoration: "you may ask for more and never less" is the whole rule, and
 *  a dropdown offering four options without it invites someone to choose `none` for a factual
 *  idea and read the refusal as a bug. */
export function ResearchDepth({
  value,
  onChange,
  disabled = false,
}: {
  value: DepthChoice;
  onChange: (depth: DepthChoice) => void;
  disabled?: boolean;
}) {
  return (
    <>
      <Select value={value} onValueChange={(next) => onChange(next as DepthChoice)} disabled={disabled}>
        <SelectTrigger aria-label="research depth" className="w-full">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {/* In declaration order, which is `RESEARCH_MODES` with Auto in front. That is not a
              ranking and nothing may present it as one. */}
          {DEPTHS.map((option) => (
            <SelectItem key={option} value={option}>
              {DEPTH_LABEL[option]}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <p className="mt-2 text-caption text-muted">
        The floor is detected from the idea. You can ask for more research than it requires and
        never less — a request below the floor is refused, with what it saw, rather than quietly
        run at the floor or quietly upgraded.
      </p>
    </>
  );
}
