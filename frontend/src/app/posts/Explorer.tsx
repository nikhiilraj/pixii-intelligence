"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
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
import { getJson, type Post, type Template } from "@/lib/api";

const SORTS = [
  "engaged_actions",
  "impressions",
  "reach",
  "likes",
  "comments",
  "shares",
  "engagement_rate",
  "published_at",
] as const;

// The two cohorts the corpus is read along, mirroring `settings.voice_account` and
// `settings.inspiration_account` in backend/app/config.py.
// ponytail: two stable config strings copied rather than served — the same call the
// templates page makes for `sample_size=27`. Serve them if they ever go per-platform.
const VOICE_ACCOUNT = "Monte Desai";
const INSPIRATION_ACCOUNT = "Creator inspiration";

/* Radix rejects `Select.Item value=""` — it reserves the empty string for "nothing is
 * selected", which is what a placeholder means. The three optional filters use "" for "no
 * filter", so they need a stand-in item value.
 *
 * The sentinel is translated at the Select's own props and nowhere else: `Filters` still
 * holds "", so `filterQuery` below is byte-identical to the query construction that was
 * inline here before this migration. That matters more than it looks — letting the sentinel
 * reach the query builder would send `platform=__all__` to the API, and `/posts` answers an
 * unknown platform with an empty list, so the filter would silently return nothing instead
 * of everything. The `noFilter`/`filterQuery` pair below is tested as the
 * composition the component actually calls, so the sentinel is pinned where it lives.
 */
export const ALL = "__all__";

/** The sentinel on the way back out — the only place `ALL` is understood. */
export const noFilter = (value: string) => (value === ALL ? "" : value);

const nf = new Intl.NumberFormat("en-US");

/* How many rows the table renders before it says it is holding some back. 69 rows made the
 * page 3936px tall (4030 when the finding was filed), which is not broken but is not a table
 * anyone reads to the end of either. 25 brings it to 1896.
 * ponytail: a slice and a toggle, not pagination — `/posts` already answers with the whole
 * filtered set in one request, so there is no second request for a page control to make and
 * nothing to keep in the URL. Ceiling: server paging, once the corpus outgrows one response.
 */
const PAGE = 25;

/* Impressions and engagement rate are `0` by column default and are written by exactly one
 * thing: a Zernio analytics read (`corpus.py::_apply`). The LinkedIn scrape ingest and
 * `ingest_inspiration_posts` write likes/comments/shares and never touch either column, so 35
 * of the 69 rows in the default view carry engagement and no impressions data at all.
 *
 * Two separate facts decide how that renders, and both are in the data rather than assumed:
 *
 *  - Where `engaged_actions > 0` and `impressions === 0` the absence is *provable*. A post
 *    with 1240 engaged actions did not have zero impressions. That is arithmetic, not a guess,
 *    and it covers every scraped row.
 *  - Where both read 0 the row is genuinely ambiguous and the schema cannot be made to say
 *    which. `_apply` stamps `metrics_updated_at` whether or not analytics returned anything,
 *    and `sync_metrics` reads a 50-row window that 11 published posts fall outside — so "never
 *    measured" and "measured zero" are byte-identical rows.
 *
 * So a 0 is not printed in either column: it is a dash, with the footnote below the table
 * saying which of the two it is and why the page will not choose. An absence reading as a
 * measurement is the one failure this app removes everywhere else.
 *
 * The two columns are keyed independently and must stay that way. Nine rows carry a Zernio
 * engagement rate (11.27, 7.5, 15.0 …) with no impressions figure beside it; deriving the ER
 * dash from the impressions dash would erase nine real measurements. They sit outside the
 * default cohort, so no screenshot of the landing view would catch it.
 *
 * ponytail: display only. The impressions CSV import stays deferred — its trigger is Monte's
 * analytics export and it has not arrived. A parser for a file nobody has seen is the ceiling.
 */
const UNMEASURED = "—";

function accountLabel(account: string): string {
  if (account === VOICE_ACCOUNT) return `${account} — our voice`;
  if (account === INSPIRATION_ACCOUNT) return `${account} — creators`;
  return account;
}

function firstLine(content: string): string {
  const line = content.split("\n").find((l) => l.trim().length > 0) ?? "";
  return line.length > 80 ? `${line.slice(0, 80)}…` : line;
}

/* Corpus spans 2024→2026, so day+month is ambiguous in both surfaces this feeds: the axis looks
 * unsorted when it isn't, and the engagement-sorted table can stack two "15 Jun"s two years
 * apart. `year: "2-digit"` is the shortest label that fixes it; recharts thins the ~69 ticks
 * itself, so the extra characters cost ticks, not legibility.
 * ponytail: one more Intl option, not a hand-rolled formatter. */
function shortDate(value: string | null): string {
  if (!value) return "—";
  return new Date(value).toLocaleDateString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "2-digit",
  });
}

export type Filters = {
  platform: string;
  account: string;
  family: string;
  since: string;
  sort: (typeof SORTS)[number];
  order: "desc" | "asc";
};

/** The `/posts` query string for a set of filters. Lifted out of `apply` unchanged when the
 *  four controls moved onto Radix Select, so that "filtering still works" is a property of a
 *  function a test can call rather than a claim about a component tree. An empty value means
 *  "no filter" and the param is omitted entirely — sending it empty would filter on "". */
export function filterQuery(f: Filters): string {
  const params = new URLSearchParams({ sort: f.sort, order: f.order });
  if (f.platform) params.set("platform", f.platform);
  if (f.account) params.set("account", f.account);
  if (f.family) params.set("template_family", f.family);
  if (f.since) params.set("since", f.since);
  return params.toString();
}

/** One filter dropdown. Local to this file rather than in `components/ui/` — it is four
 *  repetitions of the same trigger/value/content shape with nothing behavioural of its own,
 *  and the Select parts stay usable in their Radix compound form everywhere else. */
function FilterSelect({
  label,
  value,
  onValueChange,
  children,
}: {
  label: string;
  value: string;
  onValueChange: (value: string) => void;
  children: React.ReactNode;
}) {
  return (
    <Select value={value} onValueChange={onValueChange}>
      {/* Every trigger is labelled. The native selects carried the filter's name only in
          their first option ("all channels", "any template"), which a Radix trigger does not
          announce — the PRD requires all controls labelled and three of these four were bare. */}
      <SelectTrigger aria-label={label}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>{children}</SelectContent>
    </Select>
  );
}

export default function Explorer({
  initial,
  templates,
}: {
  initial: Post[];
  templates: Template[];
}) {
  // Scoped to our own account on arrival, and filtered here rather than server-side so the
  // dropdowns can still be built from the whole corpus. The reason for the default: the
  // corpus holds creator posts collected as reference material, and their numbers are an
  // order of magnitude above ours (4331 and 1195 engaged actions against our best at 185).
  // Ranked together they head the table, so an unscoped view reads as though someone
  // else's posts were our top performers. Creators stay one click away.
  const [posts, setPosts] = useState(() =>
    initial.filter((p) => p.account_username === VOICE_ACCOUNT),
  );
  const [filters, setFilters] = useState<Filters>({
    platform: "",
    account: VOICE_ACCOUNT,
    family: "",
    since: "",
    sort: "engaged_actions",
    order: "desc",
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);

  const platforms = useMemo(
    () => Array.from(new Set(initial.map((p) => p.platform))).sort(),
    [initial],
  );

  // Every account in the corpus, not just the two cohorts — the YouTube and X accounts
  // belong to neither, and a hardcoded pair would put them out of reach.
  const accounts = useMemo(
    () =>
      Array.from(
        new Set(initial.map((p) => p.account_username).filter((a): a is string => Boolean(a))),
      ).sort(),
    [initial],
  );

  // Filtering is a user action, so it refetches from the handler rather than an effect.
  async function apply(change: Partial<Filters>) {
    const next = { ...filters, ...change };
    setFilters(next);
    setLoading(true);
    // Not one of the six call sites US-003 was scoped to, but the same bug in its
    // purest form: `setPosts(res.ok ? json : [])` turned a rejected filter — a 422 naming
    // the sort column it would not accept — into "no posts match", which is a wrong answer
    // wearing the shape of a right one. It is now reported and the previous rows are kept,
    // since throwing them away tells the user even less.
    const result = await getJson<Post[]>(`/posts?${filterQuery(next)}`);
    setLoading(false);

    if (result.ok) {
      setPosts(result.data);
      setError(null);
    } else {
      // The message moved to a toast; the `error` state did not. It still gates the
      // "Nothing matches those filters." line below, and that suppression is the whole point
      // of US-003 — after a failed request there is no data to make an emptiness claim about.
      // Deleting the state along with the paragraph would put the bug straight back.
      setError(result.message);
      toast.error("Filter failed", {
        description: `${result.message} — the rows below are the previous result.`,
      });
    }
  }

  // The rows the table actually draws. The chart below still reads `posts`, not this — the
  // shape of the run is a property of the whole filtered set, and bounding a table is a
  // reading aid, not a filter.
  const visible = showAll ? posts : posts.slice(0, PAGE);

  // Engagement over time, oldest first — the shape of the run, not a leaderboard.
  const series = useMemo(
    () =>
      [...posts]
        .filter((p) => p.published_at)
        .sort((a, b) => (a.published_at! < b.published_at! ? -1 : 1))
        .map((p) => ({
          date: shortDate(p.published_at),
          engaged: p.engaged_actions,
          impressions: p.impressions,
        })),
    [posts],
  );

  return (
    <>
      <div className="mt-6 flex flex-wrap items-center gap-2">
        <FilterSelect
          label="cohort"
          value={filters.account || ALL}
          onValueChange={(v) => apply({ account: noFilter(v) })}
        >
          <SelectItem value={ALL}>all accounts</SelectItem>
          {accounts.map((a) => (
            <SelectItem key={a} value={a}>
              {accountLabel(a)}
            </SelectItem>
          ))}
        </FilterSelect>

        <FilterSelect
          label="channel"
          value={filters.platform || ALL}
          onValueChange={(v) => apply({ platform: noFilter(v) })}
        >
          <SelectItem value={ALL}>all channels</SelectItem>
          {platforms.map((p) => (
            <SelectItem key={p} value={p}>
              {p}
            </SelectItem>
          ))}
        </FilterSelect>

        <FilterSelect
          label="template family"
          value={filters.family || ALL}
          onValueChange={(v) => apply({ family: noFilter(v) })}
        >
          <SelectItem value={ALL}>any template</SelectItem>
          {templates.map((t) => (
            <SelectItem key={t.id} value={t.family_id}>
              {t.kind}: {t.name}
            </SelectItem>
          ))}
        </FilterSelect>

        {/* Stays a native date input. A listbox cannot express an arbitrary date, and none of
            what Radix Select is here for — typeahead, ARIA listbox roles, scroll lock —
            applies to a date field. It is retokenized because it shared the `field` class
            string with the four selects that just left, and `min-h-8` pins the 32px target
            the class string did not. */}
        <input
          type="date"
          value={filters.since}
          onChange={(e) => apply({ since: e.target.value })}
          className="min-h-8 rounded-input border border-border bg-transparent px-2 py-1.5 text-meta"
          aria-label="published since"
        />

        <FilterSelect
          label="sort by"
          value={filters.sort}
          onValueChange={(v) => apply({ sort: v as (typeof SORTS)[number] })}
        >
          {SORTS.map((s) => (
            <SelectItem key={s} value={s}>
              sort: {s.replace(/_/g, " ")}
            </SelectItem>
          ))}
        </FilterSelect>

        {/* Two values, not a list — a toggle, so it is US-002's Button rather than a Select. */}
        <Button
          variant="outline"
          onClick={() => apply({ order: filters.order === "desc" ? "asc" : "desc" })}
          aria-label="toggle sort direction"
        >
          {filters.order === "desc" ? "↓" : "↑"}
        </Button>

        {/* aria-live because the row count is the only confirmation that a filter took
            effect, and the change happens away from the control that caused it. */}
        <span className="text-meta text-muted" aria-live="polite">
          {loading ? "…" : `${posts.length} post${posts.length === 1 ? "" : "s"}`}
        </span>
      </div>

      {/* The failure, persistently, with the retry that re-requests it.

          Before this it was a toast and nothing else, so four seconds after a rejected filter
          the page showed the previous rows with no sign that the filter had not taken — the
          quietest version of the bug this slice is about. The toast stays as well: it fires
          next to the control that was just used, and the table it describes is a screen
          further down.

          `apply({})` is the retry — no change to the filters, so it rebuilds the same query
          and issues the same request. The rows below are still the previous result until it
          succeeds, which is what the copy says. */}
      {error && (
        <Card role="alert" className="mt-4 border-danger/40 bg-danger/10 text-body">
          <p className="font-medium">Filter failed — the rows below are the previous result.</p>
          <p className="mt-1 text-muted">{error}</p>
          <Button
            variant="outline"
            className="mt-3"
            disabled={loading}
            onClick={() => apply({})}
          >
            {loading ? "Trying…" : "Try again"}
          </Button>
        </Card>
      )}

      {series.length > 1 && (
        <div className="mt-6 h-56 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={series} margin={{ top: 4, right: 8, bottom: 0, left: -18 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="currentColor" opacity={0.12} />
              <XAxis dataKey="date" tick={{ fontSize: 11 }} stroke="currentColor" opacity={0.5} />
              <YAxis tick={{ fontSize: 11 }} stroke="currentColor" opacity={0.5} />
              <Tooltip
                contentStyle={{ fontSize: 12, borderRadius: 8 }}
                labelStyle={{ fontSize: 12 }}
              />
              <Line
                type="monotone"
                dataKey="engaged"
                stroke="#F2610C"
                strokeWidth={2}
                dot={{ r: 2 }}
                name="engaged actions"
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Wide content scrolls inside its own container so the page body never does. */}
      <div className="mt-6 overflow-x-auto">
        <table className="w-full min-w-[46rem] border-collapse text-sm">
          <thead>
            <tr className="border-b border-black/15 text-left dark:border-white/20">
              <th className="py-2 pr-4 font-medium">Post</th>
              <th className="py-2 pr-4 font-medium">Account</th>
              <th className="py-2 pr-4 font-medium">Channel</th>
              <th className="py-2 pr-4 font-medium">Published</th>
              <th className="py-2 pr-4 text-right font-medium">Engaged</th>
              <th className="py-2 pr-4 text-right font-medium">Impressions</th>
              <th className="py-2 text-right font-medium">ER</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((post) => (
              <tr key={post.id} className="border-b border-black/8 last:border-0 dark:border-white/10">
                <td className="max-w-md py-3 pr-4">
                  <Link href={`/posts/${post.id}`} className="hover:underline">
                    {firstLine(post.content)}
                  </Link>
                </td>
                {/* Whose post this is, so a number's provenance is visible without a filter. */}
                <td className="py-3 pr-4 opacity-70">{post.account_username ?? "—"}</td>
                <td className="py-3 pr-4 opacity-70">{post.platform}</td>
                <td className="py-3 pr-4 tabular-nums opacity-70">
                  {shortDate(post.published_at)}
                </td>
                <td className="py-3 pr-4 text-right font-medium tabular-nums">
                  {nf.format(post.engaged_actions)}
                </td>
                {/* See UNMEASURED above: a stored 0 in either column is an unread analytics
                    figure as often as it is a real zero, and the two are keyed separately. */}
                <td className="py-3 pr-4 text-right tabular-nums opacity-70">
                  {post.impressions ? nf.format(post.impressions) : UNMEASURED}
                </td>
                <td className="py-3 text-right tabular-nums opacity-70">
                  {post.engagement_rate ? post.engagement_rate.toFixed(2) : UNMEASURED}
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        {/* What is hidden, stated — and which end of the sort it was cut from, because
            hiding the tail of a list is only honest if the reader knows what the tail is.
            The `aria-live` count above deliberately still reads the filtered total: it is
            the only confirmation a filter took effect, and rewriting it to 25 would make
            every filter look like it returned 25 posts. Two numbers, two jobs. */}
        {posts.length > PAGE && (
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <span className="text-meta text-muted">
              Showing {showAll ? "all " : ""}
              {nf.format(visible.length)} of {nf.format(posts.length)}, sorted by{" "}
              {filters.sort.replace(/_/g, " ")}, {filters.order === "desc" ? "highest" : "lowest"}{" "}
              first.
            </span>
            <Button variant="outline" onClick={() => setShowAll(!showAll)}>
              {showAll ? `Show ${PAGE}` : `Show all ${nf.format(posts.length)}`}
            </Button>
          </div>
        )}

        {/* The footnote half of the dash. Placed here rather than in `posts/page.tsx` because
            the rule it explains lives in this component and nowhere else.

            One interpolated string rather than `{UNMEASURED}` followed by JSX prose, and that
            is not a style preference: the two-child form renders as two adjacent text nodes
            and the leading space of the second survived on the client but not in the server
            HTML, which threw a hydration mismatch and made React discard and re-render the
            tree. Caught in Chrome — the vitest suite renders client-side only and cannot see
            it. One child, one text node, nothing to disagree about. */}
        {posts.length > 0 && (
          <p className="mt-3 max-w-2xl text-caption text-muted">
            {/* Both reasons, not just the scrape one. On the creators cohort every dashed
                impressions cell is a Zernio row the analytics window did not cover, carrying a
                real engagement rate beside it — a footnote that blamed scraping there would
                name a cause that applies to none of the rows on screen. */}
            {`${UNMEASURED} in Impressions or ER means the figure was never measured, not that ` +
              `it was zero. Both columns are filled only by a Zernio analytics read — posts ` +
              `scraped into the corpus never had one, and Zernio's analytics window does not ` +
              `cover every published post — and a stored 0 cannot be told apart from one that ` +
              `was never read. Impressions arrive with Monte's analytics export.`}
          </p>
        )}

        {/* Suppressed while `error` is set: "nothing matches" is a claim about the data,
            and after a failed request there is no data to make it about. That one boolean is
            the whole bug class this slice exists to prevent, so it is pinned by test.

            Two empties, told apart by `initial` — the unfiltered server read this component
            arrived with. Empty there means the corpus itself is empty; non-empty there means
            the filters excluded everything, and the count is worth naming because "no post
            matches" beside a corpus of 107 is a filter problem, not a data problem. */}
        {posts.length === 0 && !loading && !error && (
          <Card className="mt-6 bg-surface-2 text-body">
            {initial.length === 0 ? (
              <>
                <p className="font-medium">The corpus is empty.</p>
                <p className="mt-1 text-muted">
                  Posts arrive from Zernio analytics — every published post on a connected
                  account lands here on the next sync, and extraction reads its templates out of
                  them. Nothing is written by hand except an external post added above.
                </p>
              </>
            ) : (
              <>
                <p className="font-medium">No post matches those filters.</p>
                <p className="mt-1 text-muted">
                  The corpus holds {nf.format(initial.length)} post
                  {initial.length === 1 ? "" : "s"}. Widen the cohort, the channel, the template
                  family or the date to see more — the view opens on our own account only, so
                  creator reference posts are one filter away.
                </p>
              </>
            )}
          </Card>
        )}
      </div>
    </>
  );
}
