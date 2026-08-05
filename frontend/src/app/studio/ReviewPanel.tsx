import { MODE_MEANING } from "@/components/research-depth";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { inFlight, retryable, type Draft, type ResearchMode } from "@/lib/api";

/** Which of the five things a draft's editorial record can be.
 *
 *  `editorial === null` on its own does **not** mean "historical", and treating it that way is
 *  the same defect as the `(draft.generation_stage ?? "historical")` fallback this page used to
 *  carry: `_editorial_lineage` returns null whenever the brief row is missing, and a run that
 *  failed at planning — a below-floor refusal the door did not catch, an unreadable model
 *  answer, `UnplannableClaims` — has no brief either. That draft was created ten seconds ago
 *  and the screen called it pre-migration.
 *
 *  The order of the tests is the whole function:
 *
 *  1. `unreviewed` first, because a legacy `POST /drafts` row also has no editorial lineage.
 *     It is a deprecated path that ran one completion, and it is not pushable.
 *  2. A lineage present is a complete workflow, whatever the stage says — a `failed_review`
 *     draft has a brief, an angle and findings, and they are the reason it failed.
 *  3. Still running: the brief is not written *yet*, which is not the same as never.
 *  4. Failed with no lineage: the run stopped before the brief landed. `generation_error` is
 *     the only account of it and the caller renders that.
 *  5. Anything left is genuinely pre-migration — `ready`, no lineage, nothing failed. All six
 *     drafts in the live database are this.
 *
 *  A discriminated union rather than a boolean pair, so a new case has to be named in the
 *  renderer's switch rather than falling through to whichever branch was last. */
export type LineageState =
  | { kind: "workflow"; editorial: NonNullable<Draft["editorial"]> }
  | { kind: "unreviewed" }
  | { kind: "running" }
  | { kind: "stopped_early" }
  | { kind: "historical" };

export function lineageState(draft: Draft): LineageState {
  if (draft.generation_stage === "unreviewed") return { kind: "unreviewed" };
  if (draft.editorial) return { kind: "workflow", editorial: draft.editorial };
  if (inFlight(draft.generation_stage)) return { kind: "running" };
  if (retryable(draft.generation_stage)) return { kind: "stopped_early" };
  return { kind: "historical" };
}

/** One `dt`/`dd` pair, with `—` where nothing was recorded.
 *
 *  `—` for an empty string and for null, and **never for a number** — every count on this panel
 *  is measured, so `0` prints `0`. Passing a number through this helper at all is a mistake the
 *  signature refuses. */
function Row({ label, value }: { label: string; value: string | null }) {
  return (
    <>
      <dt className="text-muted">{label}</dt>
      <dd className="min-w-0 wrap-anywhere">{value ? value : "—"}</dd>
    </>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="min-w-0 space-y-1">
      <h4 className="font-mono text-caption uppercase tracking-[0.12em] text-muted">{title}</h4>
      {children}
    </section>
  );
}

/** The research depth three ways, plus what the detector saw.
 *
 *  **All three, always, even when they agree.** One field would make "the operator asked for
 *  deep and the run did light" unanswerable afterwards, which is the silent downgrade
 *  `ResearchJob` keeps two columns to prevent — so this renders the requested depth, the
 *  detected floor and the depth that actually ran, side by side.
 *
 *  A requested mode of `null` is "Auto", not `none`: nobody expressed a preference and the
 *  floor decided. Rendering it as `none` would report a request nobody made, and would read as
 *  a downgrade on any brief whose floor came out above it. */
function Depth({ brief }: { brief: NonNullable<Draft["editorial"]>["brief"] }) {
  const raised = brief.requested_mode !== null && brief.requested_mode !== brief.recommended_mode;
  return (
    <Section title="Research depth">
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 text-caption">
        <dt className="text-muted">Requested</dt>
        <dd className="min-w-0">
          {brief.requested_mode === null
            ? "Auto — no depth was asked for, so the floor decided"
            : `${brief.requested_mode} — ${
                // A mode name this version has no meaning for is possible in principle — the
                // column is a plain string and a later version may write one. Named rather
                // than shown as a bare word with nothing after it.
                MODE_MEANING[brief.requested_mode as ResearchMode] ?? "a depth this version cannot name"
              }`}
        </dd>
        <dt className="text-muted">Detected floor</dt>
        <dd className="min-w-0">{brief.recommended_mode}</dd>
        <dt className="text-muted">Ran as</dt>
        <dd className="min-w-0 font-medium">{brief.research_mode}</dd>
        <dt className="text-muted">Floor signals</dt>
        <dd className="min-w-0 wrap-anywhere">
          {/* What the detector matched on, in its own words. It is a heuristic biased towards
              firing, so the first question about a surprising floor is this one — and an empty
              list is a real answer rather than a missing one. */}
          {brief.mode_signals.length > 0
            ? brief.mode_signals.join(", ")
            : "nothing in this brief leaned on the outside world"}
        </dd>
      </dl>
      {raised && (
        <p className="text-caption text-muted">
          The depth was raised above the floor deliberately. It can never go below it: a request
          under the floor is refused rather than quietly clamped up to it.
        </p>
      )}
    </Section>
  );
}

/** Which claim, or which words of the idea, stands behind each assertion the post makes.
 *
 *  **`null` and `{ assertions: [] }` are two different answers and are said differently.**
 *  `null` is nobody verified this draft — a historical row, or a run that stopped at a gate
 *  before verification. `[]` is a verifier that ran and found nothing in the post that has to
 *  stand behind anything. Collapsing them with `?? []` would report the first as the second,
 *  which is the absence-as-measurement rule in the shape it takes on this panel. */
function Verification({ review }: { review: Draft["verification_result"] }) {
  if (review === null) {
    return (
      <Section title="Claim verification">
        <p className="text-caption text-muted">
          Nobody verified this draft. That is not the same as a post that asserts nothing — no
          verifier ran, so nothing here says what its sentences rest on.
        </p>
      </Section>
    );
  }
  return (
    <Section title="Claim verification">
      <p className="min-w-0 wrap-anywhere text-caption">{review.summary}</p>
      <p className="text-caption text-muted">
        Checked against{" "}
        {review.basis === "dossier"
          ? "the research dossier's cited claims"
          : "the words of the idea itself, which is the whole evidence set in none mode"}
        .
      </p>
      {review.assertions.length === 0 ? (
        <p className="text-caption text-muted">
          The verifier ran and found nothing the post asserts that needs standing behind.
        </p>
      ) : (
        <ul className="min-w-0">
          {review.assertions.map((assertion, index) => (
            <li
              key={`${assertion.text}-${index}`}
              className="min-w-0 border-t border-border py-2 first:border-t-0 first:pt-0"
            >
              <div className="flex flex-wrap items-start gap-2">
                {/* `blocks` is the only thing that decides the tone. A `factual` assertion that
                    passed is not a warning, and an `opinion` needs no citation at all — badging
                    by `kind` would colour the sentence rather than the finding. */}
                <Badge variant={assertion.blocks ? "danger" : "neutral"}>
                  {assertion.verdict}
                </Badge>
                <p className="min-w-0 flex-1 wrap-anywhere text-caption">{assertion.text}</p>
              </div>
              <p className="mt-1 min-w-0 wrap-anywhere text-caption text-muted">
                {assertion.kind} — {assertion.evidence}
              </p>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

/** The rubric's verdict, its summary, and every deduction with its evidence.
 *
 *  `readiness_points` prints as a number including `0`: the rubric scored this run to nothing,
 *  which is a measurement. `{points || "—"}` is the trap — it draws an em dash over a real
 *  score and looks right. */
function Readiness({ result }: { result: Draft["readiness_result"] }) {
  if (result === null) {
    return (
      <Section title="Editorial readiness">
        <p className="text-caption text-muted">
          The rubric never ran. It is skipped for a candidate already carrying findings, and for
          a run that stopped before it — so this is an absence, not a score of zero.
        </p>
      </Section>
    );
  }
  return (
    <Section title="Editorial readiness">
      <p className="text-caption">
        <span className="font-medium">{result.readiness_points}</span>/100 —{" "}
        {result.decision.replaceAll("_", " ")}
      </p>
      <p className="min-w-0 wrap-anywhere text-caption text-muted">{result.summary}</p>
      {result.deductions.length === 0 ? (
        <p className="text-caption text-muted">Nothing was deducted.</p>
      ) : (
        <ul className="min-w-0">
          {result.deductions.map((deduction, index) => (
            <li
              key={`${deduction.criterion}-${index}`}
              className="min-w-0 border-t border-border py-2 first:border-t-0 first:pt-0"
            >
              <p className="min-w-0 wrap-anywhere text-caption">
                <span className="font-medium">{deduction.criterion}</span> −{deduction.points}
              </p>
              <p className="min-w-0 wrap-anywhere text-caption text-muted">{deduction.reason}</p>
              {/* The quoted evidence, as text. It is the model's excerpt of words on this
                  screen already, and it is what makes a deduction checkable rather than a
                  verdict to take on trust. */}
              {deduction.evidence && (
                <p className="mt-0.5 min-w-0 wrap-anywhere text-caption italic text-muted">
                  &ldquo;{deduction.evidence}&rdquo;
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 text-caption">
        <Row label="Rubric" value={result.rubric_version} />
        <Row label="Rubric prompt" value={`${result.prompt_name} ${result.prompt_version}`} />
      </dl>
    </Section>
  );
}

/** Everything the review recorded about one draft, and which of the five records it is.
 *
 *  Everything on this panel is already persisted and already on the wire; none of it had
 *  anywhere to render. Two rules govern how it is shown rather than whether:
 *
 *  - **Nothing is sorted.** Planned claims, gate findings, deductions, assertions and prompts
 *    are in the order the API returned them, which is the order the run produced them in. A
 *    list in an order implies the order means something, which is `research.dossier()`'s
 *    argument and this project's never-rank rule applied to a review screen.
 *  - **`0` is a number where it was measured and `—` only where nothing was.** Readiness
 *    points, revision rounds and prompt call counts are all measurements.
 *
 *  It renders during a run as well as after one — a reviewer watches the stage through this
 *  block while the poll refreshes it — which is why `running` is one of the five states and not
 *  a reason to hide the panel. */
export default function ReviewPanel({ draft }: { draft: Draft }) {
  const state = lineageState(draft);
  const stage = draft.generation_stage.replaceAll("_", " ");

  return (
    <Card className="min-w-0 space-y-4 p-4 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-medium">Editorial workflow</h3>
        <span className="rounded-full border border-border px-2 py-0.5 text-caption uppercase tracking-label">
          {stage}
        </span>
      </div>

      {draft.generation_error && (
        <p role="alert" className="min-w-0 wrap-anywhere text-red-700 dark:text-red-400">
          {draft.generation_error}
        </p>
      )}

      {state.kind === "workflow" ? (
        <Workflow draft={draft} editorial={state.editorial} />
      ) : (
        <NoLineage kind={state.kind} />
      )}
    </Card>
  );
}

/** Why there is no editorial record, in the words that fit the case.
 *
 *  Four sentences rather than one, because the four states are four different things to tell a
 *  reviewer and only one of them is "this draft predates the workflow". */
function NoLineage({ kind }: { kind: Exclude<LineageState["kind"], "workflow"> }) {
  if (kind === "unreviewed") {
    return (
      <p className="text-muted">
        Nothing reviewed this draft. It was written by the deprecated <code>POST /drafts</code>{" "}
        path — one completion, with no brief, no angle, no research floor, no gates and no
        rubric — so it cannot be pushed, and that is the arrangement rather than a fault.
      </p>
    );
  }
  if (kind === "running") {
    return (
      <p className="text-muted">
        The brief and the angle have not been written yet. They appear here as soon as the
        planning stage commits them, which is the first thing this run does.
      </p>
    );
  }
  if (kind === "stopped_early") {
    return (
      <p className="text-muted">
        This run stopped before the brief was written, so there is no editorial record to show —
        the reason above is the whole account of it. This is a recent draft that failed early,
        not an old one: Retry starts a fresh attempt and keeps this row.
      </p>
    );
  }
  return (
    <p className="text-muted">
      Historical draft — editorial lineage was not recorded when it was created. It predates the
      workflow rather than having failed one, which is why it is still marked ready.
    </p>
  );
}

function Workflow({
  draft,
  editorial,
}: {
  draft: Draft;
  editorial: NonNullable<Draft["editorial"]>;
}) {
  return (
    <div className="grid min-w-0 gap-5 md:grid-cols-2">
      <Section title="Brief">
        <p className="min-w-0 wrap-anywhere text-caption">{editorial.brief.objective}</p>
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 text-caption">
          <Row label="Audience" value={editorial.brief.audience} />
          <Row label="Desired action" value={editorial.brief.desired_action} />
        </dl>
        {editorial.brief.constraints.length === 0 ? (
          <p className="text-caption text-muted">No constraints were recorded.</p>
        ) : (
          <ul className="mt-1 list-disc space-y-0.5 pl-4 text-caption">
            {editorial.brief.constraints.map((constraint, index) => (
              <li key={`${constraint}-${index}`} className="min-w-0 wrap-anywhere">
                {constraint}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Depth brief={editorial.brief} />

      <Section title="Angle">
        <p className="min-w-0 wrap-anywhere text-caption font-medium">{editorial.angle.thesis}</p>
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 text-caption">
          <Row label="Tension" value={editorial.angle.tension} />
          <Row label="Audience stake" value={editorial.angle.audience_stake} />
          <Row label="Call to action" value={editorial.angle.cta} />
        </dl>
        {editorial.angle.beats.length > 0 && (
          <ol className="mt-1 list-decimal space-y-0.5 pl-4 text-caption">
            {editorial.angle.beats.map((beat, index) => (
              <li key={`${beat}-${index}`} className="min-w-0 wrap-anywhere">
                {beat}
              </li>
            ))}
          </ol>
        )}
      </Section>

      <Section title="Planned claims">
        {editorial.planned_claims.length === 0 ? (
          <p className="text-caption text-muted">The plan named no claim.</p>
        ) : (
          /* In the order the plan stated them — `planned_claims()` orders by id, which is
             insertion order, and by nothing else. */
          <ul className="list-disc space-y-0.5 pl-4 text-caption">
            {editorial.planned_claims.map((claim) => (
              <li key={claim.id} className="min-w-0 wrap-anywhere">
                {claim.text}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Deterministic gates">
        {draft.gate_results.length === 0 ? (
          <p className="text-caption text-muted">Every gate passed.</p>
        ) : (
          <ul className="list-disc space-y-0.5 pl-4 text-caption text-red-700 dark:text-red-400">
            {draft.gate_results.map((finding, index) => (
              <li key={`${finding.gate}-${index}`} className="min-w-0 wrap-anywhere">
                [{finding.gate}] {finding.detail}
              </li>
            ))}
          </ul>
        )}
        {/* A measured count: `0` means the candidate was clean and the loop was never entered.
            It used to render only when above zero, which hid the difference between a run that
            needed no revision and one nothing recorded. */}
        <p className="text-caption text-muted">
          {draft.revision_rounds} bounded revision round
          {draft.revision_rounds === 1 ? "" : "s"}. At most two rounds and three revision calls,
          including the one schema repair that loop allows.
        </p>
      </Section>

      <Verification review={draft.verification_result} />

      <Readiness result={draft.readiness_result} />

      <Section title="Prompts and correlation">
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 text-caption">
          <Row
            label="Brief"
            value={`${editorial.brief.prompt.name} ${editorial.brief.prompt.version}`}
          />
          <Row
            label="Angle"
            value={`${editorial.angle.prompt.name} ${editorial.angle.prompt.version}`}
          />
          <Row
            label="Writing"
            value={
              editorial.write_prompt.name
                ? `${editorial.write_prompt.name} ${editorial.write_prompt.version}`
                : null
            }
          />
          <Row
            label="Verification"
            value={
              draft.verification_result
                ? `${draft.verification_result.prompt_name} ${draft.verification_result.prompt_version}`
                : null
            }
          />
          {/* The id every trace row, the research job and both artifacts carry. It is how a
              run is looked up afterwards, so it is shown in full and never truncated. */}
          <Row label="Correlation" value={editorial.correlation_id} />
          <Row
            label="Research job"
            value={editorial.research_job_id === null ? null : `#${editorial.research_job_id}`}
          />
        </dl>
        {editorial.prompts.length > 0 && (
          <>
            <p className="mt-1 text-caption text-muted">
              Every prompt this run called, in the order it called them. Research and revision
              appear only here — neither writes a row that could carry its own prompt.
            </p>
            <ul className="text-caption">
              {editorial.prompts.map((prompt) => (
                <li key={`${prompt.name}-${prompt.version}`} className="min-w-0 wrap-anywhere">
                  <span className="font-mono">
                    {prompt.name} {prompt.version}
                  </span>{" "}
                  {/* A measured count of calls, never a score. Three calls against the
                      revision prompt is a loop that ran three times, not a ranking. */}
                  <span className="text-muted">
                    — {prompt.calls} call{prompt.calls === 1 ? "" : "s"}
                  </span>
                </li>
              ))}
            </ul>
          </>
        )}
      </Section>
    </div>
  );
}
