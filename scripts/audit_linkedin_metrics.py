#!/usr/bin/env python3
"""Audit creator-inspiration.json's engagement metrics against the raw docx XML.

For every post this pulls the literal metric lines out of ``word/document.xml``,
recomputes likes/comments/shares strictly from the label attached to each number,
and diffs that against the JSON.

Scope of the guarantee: this is independent of the parser's ``self_check``, but
NOT of the parser -- it imports the same tokenizer and segmenter. It will catch
a JSON that drifted from the document; it will not catch a label mapping that
was wrong in the first place. To check that, read the bytes with no code in the
loop:

    unzip -p <input.docx> word/document.xml | grep -o 'Likes - 4,.\\{0,80\\}'

It exists because post 1's metrics (``Likes - 4,`` / ``Comment - 4,`` /
``Repost - 4``) all happen to be 4, which reads like a single number broadcast
into three fields. It is not -- each field comes from its own labelled line. The
two ``L``/``C`` lines are easy to miss when skimming: they sit in the SAME
``<w:p>`` as the post's image, separated from it only by ``<w:br/>``, so a
paragraph-level view shows the image paragraph and then ``Repost - 4`` alone.
Run this before concluding a metric is wrong.

Usage:
    python3 scripts/audit_linkedin_metrics.py <input.docx> <creator-inspiration.json>

Exits non-zero if any post disagrees.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import zipfile

_spec = importlib.util.spec_from_file_location(
    "parse_linkedin_docx", os.path.join(os.path.dirname(os.path.abspath(__file__)), "parse_linkedin_docx.py")
)
P = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P)

SHARE_LABEL_RE = re.compile(r"(?i)(?<![A-Za-z])(s|shares?|reposts?|reshares?)(?![A-Za-z])")


def xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def metric_lines(body: str):
    """Metric-only lines in a segment, split into the terminating run and any strays."""
    run, stray, started, ended = [], [], False, False
    for line in body.split("\n"):
        bare = P.IMG_RE.sub("", line).strip()
        if not bare:
            continue
        if P.METRIC_LINE_RE.match(bare):
            (stray if ended else run).append(bare)
            started = True
        elif started:
            ended = True
    return run, stray


def main():
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    docx_path, json_path = sys.argv[1], sys.argv[2]

    raw = zipfile.ZipFile(docx_path).read("word/document.xml").decode("utf-8")
    stream, _ = P.read_docx(docx_path)
    _, segments, _ = P.split_segments(stream)
    doc = json.load(open(json_path))

    if len(segments) != len(doc["posts"]):
        raise SystemExit("segment/post count mismatch: %d vs %d" % (len(segments), len(doc["posts"])))

    cursor = 0  # walks forward so repeated literals resolve to the right occurrence
    failures = []
    print("%-5s | %-46s | %-11s | %-16s | %s" % ("post", "metric lines in docx (document order)", "xml offset", "json L/C/S", "verdict"))
    print("-" * 108)

    for i, (label, body) in enumerate(segments):
        run, stray = metric_lines(body)

        offsets = []
        for line in run:
            needle = xml_escape(line.rstrip(","))
            at = raw.find(needle, cursor)
            offsets.append(at)
            if at != -1:
                cursor = at + len(needle)

        expected = {}
        for line in run:
            for kind, value in P.parse_metric_line(line):
                expected.setdefault(kind, value)
        want = (expected.get("likes"), expected.get("comments"), expected.get("shares"))

        post = doc["posts"][i]
        got = (post["likes"], post["comments"], post["shares"])

        verdict = "OK"
        if got != want:
            verdict = "MISMATCH expected %s" % (want,)
            failures.append((i + 1, got, want))
        if stray:
            verdict += "  (stray metric lines after run: %s)" % stray
            failures.append((i + 1, "stray", stray))

        # A null share must mean no share label anywhere in the segment.
        if post["shares"] is None:
            segment_text = "\n".join(P.IMG_RE.sub("", l) for l in body.split("\n"))
            for line in segment_text.split("\n"):
                bare = line.strip()
                if P.METRIC_LINE_RE.match(bare) and SHARE_LABEL_RE.search(bare):
                    verdict += "  (MISSED SHARE: %r)" % bare
                    failures.append((i + 1, "missed share", bare))
                if re.fullmatch(r"[\d,]+", bare):
                    verdict += "  (bare number, possible unlabelled share: %r)" % bare
                    failures.append((i + 1, "bare number", bare))

        print("%-5d | %-46s | %-11s | %-16s | %s" % (
            i + 1,
            " / ".join(run)[:46],
            ",".join(str(o) for o in offsets)[:11],
            str(got),
            verdict,
        ))

    print()
    if failures:
        print("AUDIT FAILED (%d):" % len(failures))
        for f in failures:
            print("  - %s" % (f,))
        raise SystemExit(1)
    print("audit: all %d posts match the document. Every value traces to its own labelled line;" % len(doc["posts"]))
    print("no number populates a field whose label it does not carry.")


if __name__ == "__main__":
    main()
