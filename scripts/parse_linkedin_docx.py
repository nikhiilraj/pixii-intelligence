#!/usr/bin/env python3
"""Parse a LinkedIn "inspiration archive" .docx into normalized JSON + extracted images.

The archive is hand-assembled, so its structure is irregular:

  * Post delimiters vary: ``Post 1``..``Post 4`` are numbered, everything after
    is just ``Post-``.
  * Delimiters are NOT paragraph-aligned. One paragraph reads
    ``7-C, 61-s`` / ``Post 4`` / ``My dog made a mess...`` as a single run of
    text broken only by ``<w:br/>``, so we split on a regex over the joined
    text stream rather than on paragraph boundaries.
  * Engagement metrics appear in both orders and several spellings
    (``L-334``, ``45-L``, ``7-C, 61-s``, ``Likes - 4,``, ``Repost - 4``,
    ``63-Reshare``). Partial metrics are normal; a missing metric is ``None``
    (``null`` in JSON), never 0.
  * Images sit at the END of a post, immediately before its metrics.
  * Some images carry their original LinkedIn CDN address, either labelled
    ("Image asset address: https://media.licdn.com/...") or as a bare hyperlink.
    That is document annotation, not post text, so it moves to ``image_url`` and
    is removed from ``content``.

Output schema, per post:
    index, label, content, likes, comments, shares, image_files, image_url, notes
``label`` records which delimiter form produced the post; ``image_url`` is null
when the doc did not note one. Missing metrics are null, never 0.

Post boundary rule
------------------
A post ends at the *contiguous run of metric-only lines* that terminates it
(blank lines are allowed inside the run). Any non-blank text after that run but
before the next ``Post`` delimiter belongs to the NEXT post -- the archive has
one place where the delimiter was pasted a few lines too late, and this rule
keeps that post whole instead of splitting it in two.

Usage:
    python3 scripts/parse_linkedin_docx.py <input.docx> <output-dir>

Writes ``<output-dir>/creator-inspiration.json`` and
``<output-dir>/creator-images/``.
"""

from __future__ import annotations

import json
import os
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
RELS = "{http://schemas.openxmlformats.org/package/2006/relationships}"

# Sentinel for an inline image; the ``|`` never occurs in the archive's prose.
IMG_RE = re.compile(r"\x00IMG\|([^\x00]+)\x00")


def _img_token(target: str) -> str:
    return "\x00IMG|%s\x00" % target


# A whole line that is nothing but a post delimiter.
DELIM_RE = re.compile(r"(?m)^[ \t]*Post[ \t]*[-–—]?[ \t]*(\d*)[ \t]*[-–—]?[ \t]*$")
# Any occurrence, used only to flag delimiters we may have missed.
DELIM_ANY_RE = re.compile(r"(?<![A-Za-z])Post[ \t]*[-–—]?[ \t]*\d*")

# The doc annotates some images with their original LinkedIn CDN address, either
# labelled ("Image asset address: https://media.licdn.com/...") or as a bare
# hyperlink on its own line. That is document annotation, not post text: it is
# lifted into ``image_url`` and removed from ``content`` so it never reaches hook
# extraction. Deliberately restricted to the image CDN host -- posts legitimately
# end with links their author wrote (post 14 signs off with an lnkd.in article
# link), and those must stay in ``content``.
IMAGE_URL_RE = re.compile(
    r"^[ \t]*(?:Image[ \t]+asset[ \t]+address[ \t]*:[ \t]*)?(?P<url>https://media\.licdn\.com/\S+)[ \t]*$",
    re.IGNORECASE,
)

LIKE_WORDS = ("l", "like", "likes")
COMMENT_WORDS = ("c", "comment", "comments")
SHARE_WORDS = ("s", "share", "shares", "repost", "reposts", "reshare", "reshares")
ALL_WORDS = LIKE_WORDS + COMMENT_WORDS + SHARE_WORDS

_WORD_ALT = "|".join(sorted(ALL_WORDS, key=len, reverse=True))
_NUM = r"\d[\d,]*"
_SEP = r"[ \t]*[-–—][ \t]*"
# One metric token, in either order: ``334-L`` or ``L-334``.
TOKEN_RE = re.compile(
    r"(?:(?P<num1>%s)%s(?P<word1>%s)|(?P<word2>%s)%s(?P<num2>%s))" % (_NUM, _SEP, _WORD_ALT, _WORD_ALT, _SEP, _NUM),
    re.IGNORECASE,
)
# Same pattern without group names, so it can be repeated inside one regex.
_TOKEN = r"(?:%s%s(?:%s)|(?:%s)%s%s)" % (_NUM, _SEP, _WORD_ALT, _WORD_ALT, _SEP, _NUM)
# A whole line consisting only of metric tokens, comma-separated.
METRIC_LINE_RE = re.compile(
    r"^[ \t]*%s(?:[ \t]*,[ \t]*%s)*[ \t]*,?[ \t]*$" % (_TOKEN, _TOKEN),
    re.IGNORECASE,
)


def read_docx(path: str):
    """Return (joined_text_stream, {media_target: bytes}).

    Images are inlined into the text stream as ``\\x00IMG|media/imageN.png\\x00``
    tokens at the exact position they occur in the document body.
    """
    with zipfile.ZipFile(path) as zf:
        rels = {}
        for rel in ET.fromstring(zf.read("word/_rels/document.xml.rels")):
            rels[rel.get("Id")] = rel.get("Target")

        root = ET.fromstring(zf.read("word/document.xml"))
        body = root.find(W + "body")

        paragraphs = []
        for p in body.iter(W + "p"):
            out = []
            for el in p.iter():
                if el.tag == W + "t":
                    out.append(el.text or "")
                elif el.tag == W + "tab":
                    out.append("\t")
                elif el.tag == W + "br":
                    out.append("\n")
                elif el.tag.endswith("}blip"):
                    rid = el.get(R + "embed")
                    out.append(_img_token(rels.get(rid, rid)))
            paragraphs.append("".join(out))

        media = {}
        for target in set(rels.values()):
            if target and target.startswith("media/"):
                media[target] = zf.read("word/" + target)

    return "\n".join(paragraphs), media


def parse_metric_line(line: str):
    """Return [(kind, value)] for a metric-only line."""
    found = []
    for m in TOKEN_RE.finditer(line):
        word = (m.group("word1") or m.group("word2")).lower()
        num = int((m.group("num1") or m.group("num2")).replace(",", ""))
        if word in LIKE_WORDS:
            kind = "likes"
        elif word in COMMENT_WORDS:
            kind = "comments"
        else:
            kind = "shares"
        found.append((kind, num))
    return found


def split_segments(stream: str):
    """Split the text stream into (label, body) segments on post delimiters."""
    matches = list(DELIM_RE.finditer(stream))
    if not matches:
        raise SystemExit("no post delimiters found")

    preamble = stream[: matches[0].start()].strip()
    segments = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(stream)
        segments.append((m.group(0).strip(), stream[m.end() : end]))
    return preamble, segments, matches


def find_stray_delimiters(stream: str, matches):
    """Delimiter-looking text that is NOT a standalone line -- worth a human look."""
    full = {(m.start(), m.group(0).strip()) for m in matches}
    stray = []
    for m in DELIM_ANY_RE.finditer(stream):
        line_start = stream.rfind("\n", 0, m.start()) + 1
        line_end = stream.find("\n", m.end())
        line = stream[line_start : line_end if line_end != -1 else len(stream)]
        if DELIM_RE.fullmatch(line.strip()) or any(s == m.start() for s, _ in full):
            continue
        if line.strip() and not DELIM_RE.match(line.strip()):
            stray.append(line.strip())
    return stray


def parse_segment(body: str):
    """Split one segment into (content_lines, metrics, trailing_lines)."""
    lines = body.split("\n")

    metric_start = None
    for i, line in enumerate(lines):
        if line.strip() and METRIC_LINE_RE.match(IMG_RE.sub("", line)):
            metric_start = i
            break

    if metric_start is None:
        return lines, {}, []

    # Extend over a contiguous run of metric lines; blanks may sit inside it.
    last = metric_start
    metrics = {}
    for i in range(metric_start, len(lines)):
        stripped = IMG_RE.sub("", lines[i])
        if not stripped.strip():
            continue
        if not METRIC_LINE_RE.match(stripped):
            break
        for kind, value in parse_metric_line(stripped):
            metrics.setdefault(kind, value)
        last = i

    content = lines[:metric_start]
    trailing = [l for l in lines[last + 1 :]]
    return content, metrics, trailing


def split_image_urls(lines):
    """Pull image-CDN annotation lines out of the content. Returns (lines, urls)."""
    kept, urls = [], []
    for line in lines:
        bare = IMG_RE.sub("", line).strip()
        m = IMAGE_URL_RE.match(bare)
        if m:
            urls.append(m.group("url"))
            # Keep any image token that shared the line; drop the URL text itself.
            kept.append("".join(_img_token(t) for t in IMG_RE.findall(line)))
            continue
        kept.append(line)
    return kept, urls


def clean(lines):
    """Join content lines, strip image tokens, normalize blank runs."""
    text = "\n".join(lines)
    text = IMG_RE.sub("", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def images_in(lines):
    return IMG_RE.findall("\n".join(lines))


def parse(docx_path: str):
    stream, media = read_docx(docx_path)
    preamble, segments, matches = split_segments(stream)
    stray = find_stray_delimiters(stream, matches)

    raw = []
    for label, body in segments:
        content, metrics, trailing = parse_segment(body)
        raw.append({"label": label, "content": content, "metrics": metrics, "trailing": trailing})

    posts = []
    carry = []  # lines that followed the previous post's metrics
    carry_from = None
    for i, seg in enumerate(raw):
        notes = []
        lines = carry + seg["content"]
        if carry:
            notes.append(
                "Opening lines were located before the '%s' delimiter in the source doc "
                "(the delimiter was pasted mid-post, after this post's hook); "
                "they follow post %d's metrics block and were reattached here."
                % (seg["label"], carry_from)
            )
        carry = []
        carry_from = None
        if seg["trailing"] and any(l.strip() for l in seg["trailing"]):
            carry = seg["trailing"]
            carry_from = i + 1
            notes.append(
                "Text after this post's metrics block was reattached to post %d "
                "(misplaced '%s' delimiter in the source doc)." % (i + 2, raw[i + 1]["label"] if i + 1 < len(raw) else 0)
            )

        lines, urls = split_image_urls(lines)
        content = clean(lines)
        imgs = images_in(lines)
        if not content and imgs:
            notes.append("Image-only post: no body text in the source doc.")
        if not seg["metrics"]:
            notes.append("No engagement metrics recorded in the source doc.")
        if len(urls) > 1:
            notes.append("Source doc lists %d image CDN URLs; kept the first in image_url: %s" % (len(urls), urls))

        posts.append(
            {
                "index": i + 1,
                "label": seg["label"],
                "content": content,
                "likes": seg["metrics"].get("likes"),
                "comments": seg["metrics"].get("comments"),
                "shares": seg["metrics"].get("shares"),
                "_images": imgs,
                "image_url": urls[0] if urls else None,
                "notes": " ".join(notes),
            }
        )

    return posts, media, preamble, stray


def write_output(docx_path: str, out_dir: str):
    posts, media, preamble, stray = parse(docx_path)

    img_dir = os.path.join(out_dir, "creator-images")
    os.makedirs(img_dir, exist_ok=True)

    for post in posts:
        names = []
        for n, target in enumerate(post.pop("_images"), start=1):
            stem = os.path.splitext(os.path.basename(target))[0]
            ext = os.path.splitext(target)[1] or ".png"
            name = "post-%02d_img-%d_%s%s" % (post["index"], n, stem, ext)
            with open(os.path.join(img_dir, name), "wb") as fh:
                fh.write(media[target])
            names.append(name)
        post["image_files"] = names
        # Keep the documented key order.
        ordered = {
            k: post[k]
            for k in ("index", "label", "content", "likes", "comments", "shares", "image_files", "image_url", "notes")
        }
        post.clear()
        post.update(ordered)

    doc = {
        "source_doc": os.path.basename(docx_path),
        "count": len(posts),
        "posts": posts,
    }
    if preamble:
        doc["preamble"] = preamble
    if stray:
        doc["ambiguous_delimiters"] = stray

    json_path = os.path.join(out_dir, "creator-inspiration.json")
    with open(json_path, "w") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return doc, json_path, img_dir


# --------------------------------------------------------------------------
# Self-checks. These run on every parse; they are the reason a re-parse of a
# NEW doc is trustworthy (the invariants hold for any doc; the hard-coded
# expectations only fire for the original archive).
#
# Two known limits, so nobody over-trusts a green run on a NEW doc:
#   * Text conservation compares a flat concatenation of all post contents, so
#     it proves no line was dropped, duplicated or reordered -- but it is blind
#     to where the post boundaries fall. Boundaries are pinned by the
#     delimiter/metric-run pairing and by EXPECTED, not by this check.
#   * The check filters lines with the same METRIC_LINE_RE the parser uses, so a
#     line that is falsely read as metrics disappears from both sides and
#     conservation still passes. EXPECTED catches that for the original archive;
#     for a new doc, eyeball the metric counts in the summary output.
# --------------------------------------------------------------------------

EXPECTED = {
    "source_doc": "Linkedin Post Archive Of Inspirations for pixii intelligence.docx",
    "count": 14,
    # (likes, comments, shares) per post, None = not recorded
    "metrics": [
        (4, 4, 4),
        (345, 49, 6),
        (1127, 7, 61),
        (18, 2, None),
        (734, 6, 33),
        (78, 195, 1),
        (245, 20, 5),
        (334, 8, 7),
        (276, 139, None),
        (45, 5, 2),
        (18, 9, None),
        (1334, 2934, 63),
        (67, 25, 2),
        (433, 138, 39),
    ],
    # Posts whose image carries an original CDN address in the doc.
    "image_urls": {3: "D5622AQE6ovK1-jGQIg", 4: "D4E22AQHzrjOoYv21HQ"},
}

# Every label format observed in the archive, asserted on every run. Adding a new
# spelling here is the way to extend the parser's vocabulary safely.
FORMAT_CASES = [
    # word-first, spaced separator, trailing comma
    ("Likes - 4,", [("likes", 4)]),
    ("Comment - 4,", [("comments", 4)]),
    ("Repost - 4", [("shares", 4)]),
    ("Likes - 4", [("likes", 4)]),
    # word-first, tight separator, mixed case
    ("Likes-345", [("likes", 345)]),
    ("comments-49", [("comments", 49)]),
    ("Shares-6", [("shares", 6)]),
    # letter-first
    ("L-334", [("likes", 334)]),
    ("C-8", [("comments", 8)]),
    ("S-7", [("shares", 7)]),
    # number-first
    ("734-L", [("likes", 734)]),
    ("2-C", [("comments", 2)]),
    ("33-S", [("shares", 33)]),
    # thousands separators
    ("1,334-L", [("likes", 1334)]),
    ("2,934-C", [("comments", 2934)]),
    ("1,127-L", [("likes", 1127)]),
    # further share spellings
    ("63-Reshare", [("shares", 63)]),
    ("1-S", [("shares", 1)]),
    # comma-joined on one line, lowercase s
    ("7-C, 61-s", [("comments", 7), ("shares", 61)]),
]

# Lines that must NOT be read as metrics (guards against eating post prose).
# The two em-dash lines are real post-3 prose and are the closest near-misses in
# the archive: "brands—73%" is a plural 's' before an em dash, one character away
# from a share token. They are pinned here so loosening _SEP or the word list
# fails the suite instead of silently swallowing a paragraph.
FORMAT_NON_CASES = [
    "4",
    "1,334",
    "Post-",
    "Post 4",
    "269 brands—73%—appeared in only one market",
    "Only 9 brands—2%—entered the Top 100 in all six",
    "( ♻️Repost for priority access)",
    'Comment “𝗔+” and I’ll share my exact workflow.',
    "10 products × traditional A+ pricing = ~$30k",
    "Read the full piece on State of Brand: https://lnkd.in/g-cEZ_Y9",
]

# A metric glued directly onto prose with no separator would be swallowed into
# content. w:br renders as a newline so this does not happen in the archive, but
# a future doc could differ -- this catches it.
#
# Only the multi-letter spellings are usable for the word-first branch: the
# single letters l/c/s collide with ordinary prose ("269 brands—73%" is a plural
# 's' before an em dash, not a share count). The number-first branch is safe with
# the full vocabulary because a digit must precede the separator.
_LONG_WORDS = tuple(w for w in ALL_WORDS if len(w) > 1)
_LONG_ALT = "|".join(sorted(_LONG_WORDS, key=len, reverse=True))
GLUED_METRIC_RE = re.compile(
    r"\S(?:%s)[ \t]*[-–—][ \t]*\d|\S\d[\d,]*[ \t]*[-–—][ \t]*(?:%s)(?![A-Za-z])" % (_LONG_ALT, _WORD_ALT),
    re.IGNORECASE,
)


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def self_check(doc, docx_path, img_dir):
    problems = []
    stream, media = read_docx(docx_path)

    # 0. Label-format coverage: every spelling the archive uses, and every
    #    near-miss that must not be mistaken for one.
    for text, want_tokens in FORMAT_CASES:
        if not METRIC_LINE_RE.match(text):
            problems.append("format not recognized as a metric line: %r" % text)
        elif parse_metric_line(text) != want_tokens:
            problems.append("format %r parsed as %s, expected %s" % (text, parse_metric_line(text), want_tokens))
    for text in FORMAT_NON_CASES:
        if METRIC_LINE_RE.match(text):
            problems.append("non-metric line wrongly matched as metrics: %r" % text)

    # 1. Text conservation: every non-delimiter, non-metric, non-image-URL line
    #    survives exactly once.
    expected_lines, stripped_urls = [], []
    for line in DELIM_RE.sub("", stream).split("\n"):
        bare = IMG_RE.sub("", line)
        if not bare.strip():
            continue
        if METRIC_LINE_RE.match(bare):
            continue
        m = IMAGE_URL_RE.match(bare.strip())
        if m:
            stripped_urls.append(m.group("url"))
            continue
        expected_lines.append(bare)
        if GLUED_METRIC_RE.search(bare):
            problems.append("possible metric glued onto prose, check by hand: %r" % bare.strip()[:120])
    got = normalize(" ".join(p["content"] for p in doc["posts"]))
    want = normalize(" ".join(expected_lines))
    if got != want:
        problems.append("text conservation failed (%d vs %d chars)" % (len(got), len(want)))
        for i in range(min(len(got), len(want))):
            if got[i] != want[i]:
                problems.append("  first divergence at %d: got %r want %r" % (i, got[i : i + 90], want[i : i + 90]))
                break

    # 1b. Every CDN address stripped from content is preserved in an image_url,
    #     and nothing was invented.
    captured = [p["image_url"] for p in doc["posts"] if p["image_url"]]
    if sorted(captured) != sorted(stripped_urls):
        problems.append("image_url mismatch: captured %s, stripped %s" % (captured, stripped_urls))
    for post in doc["posts"]:
        if post["image_url"] and "media.licdn.com" in post["content"]:
            problems.append("post %d still has a CDN URL in content" % post["index"])

    # 2. Every image referenced exactly once.
    used = [n for p in doc["posts"] for n in p["image_files"]]
    if len(used) != len(set(used)):
        problems.append("duplicate image filenames")
    if len(used) != len(media):
        problems.append("image count mismatch: %d attributed, %d in docx" % (len(used), len(media)))
    for name in used:
        if not os.path.exists(os.path.join(img_dir, name)):
            problems.append("missing extracted image %s" % name)

    # 3. Hard-coded expectations for the original archive.
    if doc["source_doc"] == EXPECTED["source_doc"]:
        if doc["count"] != EXPECTED["count"]:
            problems.append("expected %d posts, got %d" % (EXPECTED["count"], doc["count"]))
        for post, exp in zip(doc["posts"], EXPECTED["metrics"]):
            got_m = (post["likes"], post["comments"], post["shares"])
            if got_m != exp:
                problems.append("post %d metrics: got %s expected %s" % (post["index"], got_m, exp))
        for post in doc["posts"]:
            want_url = EXPECTED["image_urls"].get(post["index"])
            if want_url and (not post["image_url"] or want_url not in post["image_url"]):
                problems.append("post %d image_url: expected one containing %r, got %r" % (post["index"], want_url, post["image_url"]))
            if not want_url and post["image_url"]:
                problems.append("post %d unexpected image_url %r" % (post["index"], post["image_url"]))
    return problems


def main():
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    docx_path, out_dir = sys.argv[1], sys.argv[2]
    os.makedirs(out_dir, exist_ok=True)
    doc, json_path, img_dir = write_output(docx_path, out_dir)

    problems = self_check(doc, docx_path, img_dir)
    print("wrote %s (%d posts)" % (json_path, doc["count"]))
    print("wrote %s (%d images)" % (img_dir, sum(len(p["image_files"]) for p in doc["posts"])))
    complete = sum(1 for p in doc["posts"] if all(p[k] is not None for k in ("likes", "comments", "shares")))
    partial = sum(
        1
        for p in doc["posts"]
        if any(p[k] is not None for k in ("likes", "comments", "shares"))
        and any(p[k] is None for k in ("likes", "comments", "shares"))
    )
    none_ = sum(1 for p in doc["posts"] if all(p[k] is None for k in ("likes", "comments", "shares")))
    print("metrics: %d complete, %d partial, %d none" % (complete, partial, none_))
    print("with images: %d" % sum(1 for p in doc["posts"] if p["image_files"]))
    if doc.get("ambiguous_delimiters"):
        print("ambiguous delimiter lines: %s" % doc["ambiguous_delimiters"])
    if problems:
        print("\nSELF-CHECK FAILURES:")
        for p in problems:
            print("  - " + p)
        raise SystemExit(1)
    print("self-check: OK")


if __name__ == "__main__":
    main()
