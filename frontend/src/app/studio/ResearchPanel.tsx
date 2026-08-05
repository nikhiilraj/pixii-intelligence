import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import type {
  Citation,
  Claim,
  ClaimStatus,
  Dossier,
  ResearchJobSummary,
  ResearchSource,
} from "@/lib/api";

import { stamp } from "./PublishPanel";

/** What each claim status is called on screen, and how loudly.
 *
 *  **`unsupported` is labelled "Uncited", in the danger treatment, and that is the slice.**
 *  `research.py` made it a stored state rather than a silence precisely so that a factual
 *  sentence with nothing behind it cannot reach a draft unnoticed; rendering it as an ordinary
 *  claim — or as a claim that merely happens to list no citations — hands the silence back.
 *
 *  The word is "Uncited" rather than "Unsupported" for one reason: the two other negative
 *  states, `refuted` and `disputed`, are also "unsupported" in plain English, and a reviewer
 *  skimming three amber badges needs the one that means *nobody looked at this* to say so.
 *
 *  Note what the colours are not: a quality order. `supported` is not "good" and `disputed` is
 *  not "worse" — sources disagreeing is a finding worth reading, which is why it is a warning
 *  and not a failure. */
export const CLAIM_LABEL: Record<ClaimStatus, { label: string; variant: BadgeTone }> = {
  supported: { label: "Supported", variant: "success" },
  disputed: { label: "Sources disagree", variant: "warning" },
  refuted: { label: "Contradicted", variant: "warning" },
  unsupported: { label: "Uncited", variant: "danger" },
};

type BadgeTone = "neutral" | "success" | "warning" | "danger";

/** A count that was measured, or `—` where nothing was.
 *
 *  **Never `value || "—"`.** A measured `0` is falsy, and that expression prints an em dash for
 *  a search loop that ran and found nothing — the absence-as-measurement rule read backwards,
 *  looking correct all the while. The test named after this function is what holds the line. */
export function measured(value: number | null): string {
  return value === null ? "—" : String(value);
}

/** How many claims came out in each state, or `null` when there are no claims at all.
 *
 *  `null`, not four zeros. With an empty claim list this screen cannot tell "the claim pass ran
 *  and proposed nothing" from "the claim pass never ran" — `dossier()` builds `unknowns` from
 *  `job.open_questions or ()`, which collapses NULL and `[]` before this ever sees it — so four
 *  zeros would be a measurement nobody took. The mode says which case it probably is, in words,
 *  under the summary. */
export function coverage(claims: Claim[]): Record<ClaimStatus, number> | null {
  if (claims.length === 0) return null;
  const counts: Record<ClaimStatus, number> = {
    supported: 0,
    disputed: 0,
    refuted: 0,
    unsupported: 0,
  };
  for (const claim of claims) counts[claim.status] += 1;
  return counts;
}

/** One claim, its badge, and the words in a source that bear on it.
 *
 *  The spans are rendered as **text**. `fetching.py` already stripped active content out of
 *  these pages and `research.py` neutralised anything that could address the model, and none of
 *  that survives a `dangerouslySetInnerHTML` here — this is still someone else's document,
 *  quoted, and it goes on screen as characters. */
function ClaimRow({
  claim,
  citations,
  sources,
}: {
  claim: Claim;
  citations: Citation[];
  sources: Map<number, ResearchSource>;
}) {
  const { label, variant } = CLAIM_LABEL[claim.status];
  const mine = citations.filter((citation) => citation.claim_id === claim.id);

  return (
    <li className="min-w-0 border-t border-border py-3 first:border-t-0 first:pt-0">
      <div className="flex flex-wrap items-start gap-2">
        <Badge variant={variant}>{label}</Badge>
        <p className="min-w-0 flex-1 wrap-anywhere text-meta">{claim.text}</p>
      </div>

      {mine.length === 0 ? (
        /* Said in words as well as shown by the badge. An empty list under a claim is exactly
           the silence this slice exists to remove: it reads as "citations not loaded yet" to
           anyone who has not learned that this panel would have drawn them. */
        /* `text-red-700` and not `text-danger`. #D6455D measures 4.12:1 on --bg and fails AA
           as words — `backend-unreachable.tsx` and `badge.tsx` both say so; the danger colour
           tints a fill and never carries text. Amber is not available either: it is this
           project's warning voice, and the one line on the screen that means "nobody backed
           this up" must not read as the same register as "the clocks moved". */
        <p className="mt-2 text-caption text-red-700 dark:text-red-400">
          Nothing in this run supports this. No page was fetched whose text bears it out, so it
          is recorded as uncited rather than dropped — it is also listed under Unknowns below.
        </p>
      ) : (
        <ul className="mt-2 space-y-2">
          {mine.map((citation) => {
            const source = sources.get(citation.source_id);
            return (
              <li key={citation.id} className="min-w-0 border-l-2 border-border pl-3">
                <p className="text-caption uppercase tracking-[0.08em] text-muted">
                  {citation.stance === "contradicts" ? "Contradicts" : "Supports"}
                </p>
                {/* Quoted, and the quotation marks are ours rather than the page's. */}
                <p className="mt-0.5 min-w-0 wrap-anywhere text-meta italic">
                  &ldquo;{citation.span}&rdquo;
                </p>
                <p className="mt-0.5 min-w-0 wrap-anywhere text-caption text-muted">
                  {source ? (
                    <>
                      {/* The **final** URL. See `SourceLine`. */}
                      <a
                        className="underline underline-offset-2"
                        href={source.url}
                        rel="noreferrer noopener nofollow"
                        target="_blank"
                      >
                        {source.title || source.url}
                      </a>
                    </>
                  ) : (
                    // A citation whose source is not in this dossier cannot happen —
                    // `record_claim` refuses one — so this branch is a broken response, not a
                    // state. It says so rather than rendering a citation with no origin.
                    <span className="text-red-700 dark:text-red-400">
                      citation {citation.id} names source {citation.source_id}, which is not in
                      this dossier
                    </span>
                  )}
                </p>
              </li>
            );
          })}
        </ul>
      )}
    </li>
  );
}

/** One fetched page, and everything known about how far to trust it — which is very little.
 *
 *  Three things here are the point:
 *
 *  1. **The link is `url`, the address after redirects.** `requested_url` is what was asked
 *     for; linking it would send a reviewer to a page nobody read, which is the specific bug
 *     `ResearchSource.url` documents. The requested one is shown, as text, only when it
 *     differs — a hop that happened is part of the provenance.
 *  2. **Fetched is not verified.** `trust_tier` and `published_at` are NULL on every row this
 *     application writes, and they print `—`. "Unknown" or today's date would be a measurement
 *     nobody took, on the row whose whole job is to say what is known about a source.
 *  3. **The hash is shown in full.** Truncating it would leave a string that looks checkable
 *     and is not; it exists so a reviewer can re-fetch the page and compare. */
function SourceLine({ source }: { source: ResearchSource }) {
  const redirected = source.url !== source.requested_url;
  return (
    <li className="min-w-0 border-t border-border py-3 first:border-t-0 first:pt-0">
      <a
        className="min-w-0 wrap-anywhere text-meta font-medium underline underline-offset-2"
        href={source.url}
        rel="noreferrer noopener nofollow"
        target="_blank"
      >
        {source.title || source.url}
      </a>
      <p className="mt-0.5 min-w-0 wrap-anywhere font-mono text-caption text-muted">
        {source.url}
      </p>

      <dl className="mt-1 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 text-caption text-muted">
        {redirected && (
          <>
            <dt>Requested</dt>
            <dd className="min-w-0 wrap-anywhere font-mono">{source.requested_url}</dd>
          </>
        )}
        <dt>Host</dt>
        {/* Named "Host" and not "Publisher": this is the hostname of the final URL and no
            masthead was ever read — `ResearchSource.publisher` says so itself. Calling
            example.com a publisher is a small claim this app cannot support. */}
        <dd className="min-w-0 wrap-anywhere">{source.publisher ?? "—"}</dd>
        <dt>Fetched</dt>
        <dd className="min-w-0">{stamp(source.fetched_at)}</dd>
        <dt>Published</dt>
        <dd className="min-w-0">{stamp(source.published_at)}</dd>
        <dt>Trust tier</dt>
        <dd className="min-w-0">{source.trust_tier ?? "—"}</dd>
        <dt>Content hash</dt>
        <dd className="min-w-0 wrap-anywhere font-mono">{source.content_hash}</dd>
      </dl>
    </li>
  );
}

/** What this screen was given to show, and why there may be nothing.
 *
 *  Three fields rather than one nullable dossier, because "no research was asked for", "the
 *  read failed" and "there is a dossier" are three different things to tell a reviewer, and
 *  two of them are not an empty panel. */
export type ResearchView = {
  /** The dossier for the run this page was pointed at, or `null` when none was or the read
   *  failed — `unavailable` is what tells those apart. */
  dossier: Dossier | null;
  /** Why there is no dossier on screen, in words, or `null` when none was asked for. */
  unavailable: string | null;
  /** Recent runs, so a human can reach one. `null` means that read failed, which is not an
   *  empty list of runs. */
  jobs: ResearchJobSummary[] | null;
};

/** The research behind a draft: its sources, its claims and what backed each one, what the
 *  sources disagreed about, and what the run could not settle.
 *
 *  **Nothing on this panel is sorted.** Claims, sources and citations are rendered in the order
 *  `research.dossier()` returns them, which is insertion order — fetch order and claim order.
 *  Sorting the uncited claims to the top is one line and is the obvious thing to want; it is
 *  refused for the reason `dossier()`'s own docstring gives, which is the project's never-rank
 *  rule applied to evidence: a list in an order implies the order means something. Prominence
 *  is carried by the coverage summary above the list and by a badge on each row.
 *
 *  **Contradictions and unknowns are findings and are rendered as part of the result.** A run
 *  that came back with four uncited claims and two open questions did its job — that is what
 *  `completed` means here, and `models/research.py` refuses to give it a failure state.
 *
 *  ponytail: a read-only panel with no controls. Nothing here re-runs research, edits a claim
 *  or dismisses an unknown, because no route does any of those and a dossier is evidence — the
 *  thing a gate checked and a reviewer read must not be editable from the screen that reads it.
 *  Ceiling: a "research this draft again" control the day a route exists to run one. */
export default function ResearchPanel({
  research,
  /** The draft this panel sits under, so the links to other runs come back to it. */
  draftId,
}: {
  research: ResearchView;
  draftId: number;
}) {
  const { dossier, unavailable, jobs } = research;

  return (
    <section className="space-y-4 rounded-card border border-border p-4">
      <div>
        <p className="font-mono text-caption uppercase tracking-[0.12em] text-muted">Research</p>
        <h3 className="mt-1 text-head font-medium">Sources and claims</h3>
        <p className="mt-1 text-meta text-muted">
          What one research run read, and what it found each statement rests on. A page appearing
          here was fetched and quoted — it was not assessed, endorsed or checked for accuracy by
          anything in this application.
        </p>
      </div>

      {dossier === null ? (
        <NoDossier draftId={draftId} jobs={jobs} unavailable={unavailable} />
      ) : (
        <Dossierpanel dossier={dossier} />
      )}
    </section>
  );
}

/** No dossier on screen, and which of the three reasons it is. */
function NoDossier({
  draftId,
  jobs,
  unavailable,
}: {
  draftId: number;
  jobs: ResearchJobSummary[] | null;
  unavailable: string | null;
}) {
  return (
    <>
      {unavailable ? (
        <Card role="alert" className="border-danger/40 bg-danger/10 text-meta">
          <p className="font-medium">That research run could not be opened.</p>
          <p className="mt-1 wrap-anywhere text-muted">{unavailable}</p>
        </Card>
      ) : (
        <Card className="border-dashed text-meta">
          <p className="font-medium">No research run is linked to this draft.</p>
          <p className="mt-1 text-muted">
            Nothing in the database connects a draft to the research behind it — there is no
            column for it — so a run is opened by naming it in the address, as{" "}
            <code className="font-mono">?draft={draftId}&amp;research=&lt;id&gt;</code>. That an
            unresearched draft looks exactly like a researched one here is a gap in the schema
            and not a statement about this draft.
          </p>
        </Card>
      )}

      <div className="space-y-2">
        <p className="font-mono text-caption uppercase tracking-[0.12em] text-muted">
          Recent runs
        </p>
        {jobs === null ? (
          <p className="text-meta text-amber-700 dark:text-amber-400">
            The list of research runs could not be read. This is a failed request, not an absence
            of runs — there may well be research on this subject that this panel cannot see.
          </p>
        ) : jobs.length === 0 ? (
          <p className="text-meta text-muted">
            No research has been run. Nothing in this application has ever reached a search
            provider: there is no provider key, so every mode but <code>none</code> refuses.
          </p>
        ) : (
          // Newest first, as the API returns them. A chronology, and the only ordering over
          // these rows that carries no claim about their quality.
          <ul className="min-w-0 space-y-1">
            {jobs.map((job) => (
              <li key={job.job_id} className="min-w-0">
                <a
                  className="text-meta underline underline-offset-2 wrap-anywhere"
                  href={`/studio?draft=${draftId}&research=${job.job_id}`}
                >
                  {job.question}
                </a>{" "}
                <span className="text-caption text-muted">
                  {job.mode} · {job.state} · {stamp(job.researched_at)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  );
}

/** The dossier itself. */
function Dossierpanel({ dossier }: { dossier: Dossier }) {
  const counts = coverage(dossier.claims);
  const sources = new Map(dossier.sources.map((source) => [source.id, source]));

  return (
    <>
      <div>
        <p className="min-w-0 wrap-anywhere text-meta font-medium">{dossier.question}</p>
        <dl className="mt-2 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 text-caption text-muted">
          <dt>Mode</dt>
          {/* Both modes, always. One would make "the floor asked for light and the run did
              none" unanswerable, which is the silent downgrade `ResearchJob` stores two
              columns to prevent — so the two are shown together even when they agree. */}
          <dd className="min-w-0 wrap-anywhere">
            {dossier.mode} · floor asked for {dossier.recommended_mode}
            {dossier.mode_signals.length > 0 && ` (${dossier.mode_signals.join(", ")})`}
          </dd>
          <dt>Researched</dt>
          <dd className="min-w-0">{stamp(dossier.researched_at)}</dd>
          <dt>Freshness policy</dt>
          {/* `—` for no policy stated, not "any age is fine" — which is a policy someone would
              have had to state. Nothing enforces it either way: the fetcher cannot read a
              publication date off a page, so there is nothing to compare against. */}
          <dd className="min-w-0">
            {dossier.freshness_days === null ? "—" : `${dossier.freshness_days} days`}
          </dd>
        </dl>
      </div>

      {/* A failed run, said as an error — unlike contradictions and unknowns, which are
          findings. The reason is not shown because the dossier does not carry it: `ResearchJob`
          has an `error` column and `ResearchDossier` has no field for it. */}
      {dossier.state === "failed" && (
        <Card role="alert" className="border-danger/40 bg-danger/10 text-meta">
          <p className="font-medium">This research run failed.</p>
          <p className="mt-1 text-muted">
            It died part-way through, so what is below is what it had reached and not what it
            would have found. The reason it failed was recorded against the job and is not
            carried on this response.
          </p>
        </Card>
      )}

      <div className="space-y-2">
        <p className="font-mono text-caption uppercase tracking-[0.12em] text-muted">
          Claim coverage
        </p>
        {counts === null ? (
          <p className="text-meta text-muted">
            {/* `—` and a sentence, never four zeros. With no claims on the row this screen
                cannot tell a claim pass that proposed nothing from one that never ran, and
                `0 supported` would answer a question nobody measured. */}
            No claims, so every count here is <span className="font-mono">—</span>.{" "}
            {dossier.mode === "none"
              ? "This brief was run in none mode, which reaches no search and no claim pass at all."
              : "Either the claim pass proposed none or it never ran; this response cannot tell those apart."}
          </p>
        ) : (
          <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 text-meta">
            <dt className="text-muted">Claims</dt>
            <dd className="min-w-0">{dossier.claims.length}</dd>
            <dt className="text-muted">Supported</dt>
            <dd className="min-w-0">{counts.supported}</dd>
            <dt className="text-muted">Sources disagree</dt>
            <dd className="min-w-0">{counts.disputed}</dd>
            <dt className="text-muted">Contradicted</dt>
            <dd className="min-w-0">{counts.refuted}</dd>
            <dt className="text-muted">Uncited</dt>
            {/* The number this panel exists for, and it is a count and not a percentage: a
                proportion over four claims reads as a rate and invites a comparison between
                runs that nothing here can support. `0` is a measurement — the pass ran and
                everything it proposed was cited — and prints as `0`. */}
            <dd className="min-w-0 font-medium">{counts.unsupported}</dd>
          </dl>
        )}
      </div>

      <div className="space-y-2">
        <p className="font-mono text-caption uppercase tracking-[0.12em] text-muted">Claims</p>
        {dossier.claims.length === 0 ? (
          <p className="text-meta text-muted">This run recorded no claims.</p>
        ) : (
          <ul className="min-w-0">
            {dossier.claims.map((claim) => (
              <ClaimRow
                key={claim.id}
                citations={dossier.citations}
                claim={claim}
                sources={sources}
              />
            ))}
          </ul>
        )}
      </div>

      <div className="space-y-2">
        <p className="font-mono text-caption uppercase tracking-[0.12em] text-muted">
          Contradictions
        </p>
        {dossier.contradictions.length === 0 ? (
          <p className="text-meta text-muted">
            {/* No number in this sentence. It said "what four fetched pages happened to say"
                until a `none`-mode run — which fetches nothing — was rendered and it claimed
                four. A count written into prose is a count that goes stale silently; the
                measured one is in the table at the bottom of this panel. */}
            No claim here was contradicted by a page this run read. That is a fact about the
            pages this run happened to fetch, and not a finding about the world.
          </p>
        ) : (
          <ul className="min-w-0 space-y-1">
            {dossier.contradictions.map((claim) => (
              <li key={claim.id} className="flex min-w-0 flex-wrap items-start gap-2">
                <Badge variant={CLAIM_LABEL[claim.status].variant}>
                  {CLAIM_LABEL[claim.status].label}
                </Badge>
                <p className="min-w-0 flex-1 wrap-anywhere text-meta">{claim.text}</p>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="space-y-2">
        <p className="font-mono text-caption uppercase tracking-[0.12em] text-muted">Unknowns</p>
        {dossier.unknowns.length === 0 ? (
          <p className="text-meta text-muted">
            Nothing was recorded as outstanding. On a run with no claims that means the pass
            never got far enough to raise one, not that everything is settled.
          </p>
        ) : (
          <ul className="min-w-0 space-y-1">
            {dossier.unknowns.map((unknown, index) => (
              // Keyed by index: an unknown carries no id of its own — the two kinds are a claim
              // nothing supported and a question the model raised, and only the first has a row.
              <li key={index} className="min-w-0 wrap-anywhere text-meta">
                {unknown.text}{" "}
                <span className="text-caption text-muted">
                  {unknown.claim_id === null
                    ? "— an open question; no claim was made about it"
                    : `— claim ${unknown.claim_id}, which nothing supports`}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="space-y-2">
        <p className="font-mono text-caption uppercase tracking-[0.12em] text-muted">
          Sources read
        </p>
        {dossier.sources.length === 0 ? (
          <p className="text-meta text-muted">
            {/* "Every claim above therefore has nothing behind it" was here, and it was a
                sentence about claims on a panel that may have none — it read as a statement
                about four claims on a `none`-mode run with zero. What is true either way is
                that nothing can be cited. */}
            No page was fetched in this run, so nothing in it can be cited. What the run did,
            below, says whether a search ran at all.
          </p>
        ) : (
          <ul className="min-w-0">
            {dossier.sources.map((source) => (
              <SourceLine key={source.id} source={source} />
            ))}
          </ul>
        )}
      </div>

      <div className="space-y-2">
        <p className="font-mono text-caption uppercase tracking-[0.12em] text-muted">
          What the run did
        </p>
        {/* Every one of these is `—` for "that step never ran" and a number for a measurement,
            including `0`. A dossier with no search run is not a dossier with zero sources, and
            this table is the only place on the screen that says which. */}
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 text-meta">
          <dt className="text-muted">Queries issued</dt>
          <dd className="min-w-0">{measured(dossier.spend.queries)}</dd>
          <dt className="text-muted">Sources found</dt>
          <dd className="min-w-0">{measured(dossier.spend.sources_found)}</dd>
          <dt className="text-muted">Sources fetched</dt>
          <dd className="min-w-0">{measured(dossier.spend.sources_fetched)}</dd>
          <dt className="text-muted">Model calls</dt>
          <dd className="min-w-0">{measured(dossier.spend.llm_calls)}</dd>
          <dt className="text-muted">Stopped early by</dt>
          <dd className="min-w-0">{dossier.spend.budget_exhausted ?? "—"}</dd>
        </dl>
        {dossier.spend.budget_exhausted !== null && (
          <p className="text-caption text-amber-700 dark:text-amber-400">
            This run hit its {dossier.spend.budget_exhausted} ceiling, so the numbers above are
            what it was allowed to do and not what there was to find.
          </p>
        )}
      </div>
    </>
  );
}
