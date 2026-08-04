"""Fetch a web page as *evidence*.

Everything this module returns came from someone else's server, so the whole file is
written against two blueprint invariants rather than against convenience:

- **SSRF** (§18): the only thing a URL may reach is a public host over HTTPS. Every check
  here runs *before* the socket is opened, and runs again on every redirect, because a
  public host that 302s to `169.254.169.254` is the attack and validating only the first
  URL is the classic way to miss it.
- **Prompt injection** (§12 step 6, invariant 7): fetched text is quoted evidence, never
  instructions. Active content is stripped here. This module closes the *stripping* half
  only — `Fetched.text` is untrusted prose, and the delimiting that keeps a page author
  from addressing the model happens where the prompt is assembled, not here.
"""

import hashlib
import ipaddress
import socket
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser

import httpx

# Ceilings live here rather than in `config.py` on purpose: they are the security contract,
# not an operator preference, and an environment variable that loosens them is a way to
# turn the contract off from outside the repo.
MAX_BYTES = 5 * 1024 * 1024
MAX_SECONDS = 20.0
MAX_REDIRECTS = 5
REQUEST_TIMEOUT = 10.0

# HTML and plain text to start. Everything else — PDF, Office, images — needs a parser
# this module does not have, and "download it and see" is how a fetcher becomes a
# malware transport.
ALLOWED_TYPES = frozenset({"text/html", "text/plain"})

# Explicit rather than `httpx.codes.is_redirect`, so that a 302 carrying no `Location`
# is a failure with its own message instead of a 302 body parsed as content.
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

_USER_AGENT = "PixiiIntelligence/1.0 (research fetcher)"


@dataclass(frozen=True)
class Fetched:
    """One page, as it was actually served.

    `url` is the address the bytes came from, after redirects — not the one asked for.
    A citation that names the requested URL cites a page nobody read.
    """

    url: str
    title: str | None
    text: str
    content_type: str
    content_hash: str
    fetched_at: datetime


class UnsafeUrl(RuntimeError):
    """Refused by policy. No request for this address left the process.

    Raised for a redirect target too: the hop is refused before it is followed, so the
    address named in the message was never connected to.
    """


class FetchFailed(RuntimeError):
    """Attempted, and did not produce usable content."""


def fetch(url: str, *, transport: httpx.BaseTransport | None = None) -> Fetched:
    """Retrieve `url` as evidence, or refuse.

    `transport` exists for tests, the same seam `ZernioClient` and `AzureChat` carry.

    Redirects are followed by hand. `follow_redirects=True` would hand the decision to
    httpx, which validates nothing — the second request would leave the process before
    this module ever saw the address, which is precisely the hole.
    """
    deadline = time.monotonic() + MAX_SECONDS
    target = _parse(url)

    with httpx.Client(
        follow_redirects=False,
        timeout=REQUEST_TIMEOUT,
        transport=transport,
        headers={"User-Agent": _USER_AGENT},
    ) as client:
        for _ in range(MAX_REDIRECTS + 1):
            _check_url(target)
            _check_deadline(deadline)

            # Everything httpx raises for a request that did not come back usable — a
            # connect refusal, a TLS verification failure, a malformed response, and the
            # per-request timeout above — becomes `FetchFailed`, because those are the two
            # exceptions this module promises and a caller should not have to learn httpx's
            # hierarchy to handle a dead host. `UnsafeUrl` and `FetchFailed` are
            # `RuntimeError`, so nothing raised deliberately below is swallowed here.
            try:
                with client.stream("GET", target) as response:
                    if response.status_code in _REDIRECT_STATUSES:
                        location = response.headers.get("location")
                        if not location:
                            raise FetchFailed(
                                f"{target} answered {response.status_code} with no Location"
                            )
                        # Joined against the *current* url, not the original: `Location: /b`
                        # after two hops is relative to where we are now. Through `_parse`
                        # because a `Location` is attacker-controlled and httpx rejects some
                        # of them outright. The result then goes through the full check at
                        # the top of the loop — scheme included, because a redirect to
                        # `http://` is the downgrade vector.
                        target = target.join(_parse(location))
                        continue

                    if response.status_code >= 400:
                        raise FetchFailed(f"{target} answered {response.status_code}")

                    return _read(response, target, deadline)
            except httpx.HTTPError as exc:
                raise FetchFailed(f"{target} did not answer: {exc!r}") from exc

    raise FetchFailed(f"more than {MAX_REDIRECTS} redirects starting at {url}")


def _parse(url: str) -> httpx.URL:
    try:
        return httpx.URL(url)
    except httpx.InvalidURL as exc:
        raise UnsafeUrl(f"not a url: {url!r}") from exc


def _check_url(url: httpx.URL) -> None:
    """Scheme, credentials, and every address the host answers with."""
    if url.scheme != "https":
        # No `http`: the whole point of resolving and checking an address is lost if the
        # answer can be rewritten in transit. `file`, `data`, `ftp` and `gopher` are not
        # named individually because the allowlist refuses everything that is not https.
        raise UnsafeUrl(f"only https is fetched, not {url.scheme or '(no scheme)'}: {url}")

    if url.userinfo:
        # `https://evil.test@169.254.169.254/` reads as a hostname to a human and as
        # userinfo to a parser. Refusing the form outright beats trusting two parsers to
        # agree, and no legitimate research source needs inline credentials.
        raise UnsafeUrl(f"credentials in the url: {url.copy_with(userinfo=b'')}")

    host = url.raw_host.decode("ascii", "replace")
    if not host:
        raise UnsafeUrl(f"no host: {url}")

    for address in _resolve(host, url.port or 443):
        _check_address(address, url)


def _resolve(host: str, port: int) -> list[str]:
    """Every address this host answers with.

    All of them are checked, not just the first: a host with one public A record and one
    `10.0.0.1` A record is an attack, and which one the connection picks is not ours to
    decide.

    ponytail: this resolves, approves, and then lets httpx resolve again for the actual
    connection, so a DNS answer that changes between the two calls is not caught — classic
    rebinding. Checking *every* returned address at least means rebinding requires winning
    a race against the connect rather than simply answering twice. The clean fix is to pin
    the validated address into the connection: put the IP in the URL, set `Host`, and pass
    `extensions={"sni_hostname": host}` so TLS still verifies against the name. It is not
    here because it cannot be verified here — `MockTransport` never performs a handshake,
    so the one thing that change must get right is the one thing the suite cannot observe.
    Do it against a real endpoint or not at all.
    """
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        # Unresolvable is not unsafe. A caller distinguishing "we refused this" from "the
        # web did not answer" needs these to be different exceptions.
        raise FetchFailed(f"{host} does not resolve: {exc}") from exc
    return [str(info[4][0]) for info in infos]


def _check_address(raw: str, url: httpx.URL) -> None:
    """One resolved address, and anything tunnelled inside it, against the public internet.

    **`is_global` alone decides every case here** — measured, not assumed: deleting
    `is_private` or `is_loopback` from this expression fails no test, because CPython
    defines `is_global` as *not* private in the first place, and 100.64.0.0/10 (carrier
    NAT) is the only common address the named flags miss and `is_global` catches.

    The named flags stay anyway, and that is a deliberate choice rather than an oversight.
    `is_global` is derived from the IANA special-purpose registry and its membership has
    changed between Python releases; this is the one predicate in the app where a quiet
    upstream redefinition would open a hole rather than break a feature. They also record
    what this refuses without making a reader go and read `ipaddress`.
    """
    try:
        resolved = ipaddress.ip_address(raw)
    except ValueError as exc:
        # Fails closed, and with the type this module promises rather than a bare
        # ValueError from a library the caller never imported.
        raise UnsafeUrl(f"{url.host} resolved to {raw!r}, which is not an address") from exc

    for address in _candidates(resolved):
        unsafe = (
            address.is_private
            or address.is_loopback
            # 169.254.0.0/16 — the cloud instance metadata endpoint lives at
            # 169.254.169.254 and hands out role credentials to anything that asks. It is
            # inside `is_private` for IPv4 already; named here because it is *the* target.
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
            or not address.is_global
        )
        if unsafe:
            raise UnsafeUrl(f"{url.host} resolves to {raw}, which is not a public address")


def _candidates(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """The address, plus any IPv4 address tunnelled inside it.

    Measured, not assumed: `ip_address('::ffff:127.0.0.1').is_loopback` is **False**, and
    `ip_address('2002:7f00:1::1')` — 6to4 around 127.0.0.1 — reports `is_global` True and
    every other flag False. Checking the IPv6 form alone lets loopback through in two
    different wrappers, so each wrapper is unwrapped and the payload checked on its own.

    A legitimately mapped public address (`::ffff:8.8.8.8`) is refused as well, because the
    v6 wrapper reads as reserved. That is the safe direction and it costs nothing:
    `getaddrinfo` does not return mapped forms for an ordinary hostname.
    """
    found = [address]
    if isinstance(address, ipaddress.IPv6Address):
        for embedded in (address.ipv4_mapped, address.sixtofour):
            if embedded is not None:
                found.append(embedded)
        if address.teredo is not None:
            found.extend(address.teredo)
    return found


def _check_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise FetchFailed(f"gave up after {MAX_SECONDS}s")


def _read(response: httpx.Response, url: httpx.URL, deadline: float) -> Fetched:
    declared = response.headers.get("content-type")
    if not declared:
        # A missing type is not an invitation to guess HTML. Something served without one
        # is something nobody has classified, and this is the wrong place to be generous.
        raise FetchFailed(f"{url} served no content-type")
    mime = declared.split(";")[0].strip().lower()
    if mime not in ALLOWED_TYPES:
        raise FetchFailed(f"{url} served {mime}, which is not fetched")

    body = _drain(response, deadline, url)

    # `iter_bytes`, not `iter_raw`, so the ceiling applies to what we actually hold: a
    # 5 KB gzip stream expanding to 500 MB walks straight past a cap measured on the wire,
    # which is the same lie `Content-Length` tells in a different accent. It also keeps
    # `content_hash` stable — a hash of the wire bytes would change with the server's
    # choice to compress, and a reviewer re-fetching the page could not match it.
    content_hash = hashlib.sha256(body).hexdigest()

    try:
        text = body.decode(response.charset_encoding or "utf-8", errors="replace")
    except LookupError:
        text = body.decode("utf-8", errors="replace")

    title, readable = _extract(text) if mime == "text/html" else (None, text.strip())
    return Fetched(
        url=str(url),
        title=title,
        text=readable,
        content_type=mime,
        content_hash=content_hash,
        # Aware, and every datetime column in this schema is `timestamp without time
        # zone` — whoever stores this reads it back naive and must go through `db.utc`
        # before comparing it to anything.
        fetched_at=datetime.now(UTC),
    )


def _drain(response: httpx.Response, deadline: float, url: httpx.URL) -> bytes:
    """The body, counted as it arrives.

    Both ceilings are enforced per chunk rather than after the fact. `Content-Length` is a
    claim, not a fact, so a body is over the limit the moment the bytes say so and not when
    a header admits it. And a server trickling bytes just under the read timeout never
    trips httpx's own timeout at all — it streams until someone else stops it, which is
    what the wall-clock check in this loop is.
    """
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > MAX_BYTES:
            raise FetchFailed(f"{url} body exceeded {MAX_BYTES} bytes")
        _check_deadline(deadline)
        chunks.append(chunk)
    return b"".join(chunks)


# Their text is not the document's text. `script` and `style` are code; `noscript` and
# `template` are alternate or inert content that reads as duplicated prose once the tags
# are gone; `svg` is a drawing whose element names would arrive as words.
_DROPPED = frozenset({"script", "style", "noscript", "template", "svg"})

# Tags whose boundaries are a break in the prose. Without them `<p>a</p><p>b</p>` extracts
# as `ab`, and this text goes into a prompt — two sentences welded into one is a
# correctness problem, not a cosmetic one.
_BLOCKS = frozenset(
    {
        "address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt",
        "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6",
        "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section", "table", "td",
        "th", "tr", "ul",
    }
)


class _Readable(HTMLParser):
    """Text and title out of HTML, with active content dropped.

    Attributes are never read, which is what removes `onclick=` and friends: only
    `handle_data` contributes to the output, so nothing written inside a tag can reach the
    text at all. Comments go the same way — `handle_comment` is deliberately not
    implemented, and the base class then discards them.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._dropped = 0
        self._in_title = False
        self._title_seen = False
        self._title: list[str] = []
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _DROPPED:
            self._dropped += 1
        elif tag == "title" and not self._title_seen:
            self._in_title = True
        elif tag in _BLOCKS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _DROPPED:
            # Floored rather than asserted: a stray `</script>` must not decrement past
            # zero and un-drop the rest of the page. The mirror case is an *unclosed*
            # `<script>`, where the counter simply stays up and everything after it is
            # discarded — losing text is the safe direction, keeping script is not.
            self._dropped = max(0, self._dropped - 1)
        elif tag == "title":
            self._in_title = False
            self._title_seen = True
        elif tag in _BLOCKS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._dropped:
            return
        if self._in_title:
            self._title.append(data)
        else:
            self._parts.append(data)

    def result(self) -> tuple[str | None, str]:
        return _collapse("".join(self._title)) or None, _lines("".join(self._parts))


def _extract(markup: str) -> tuple[str | None, str]:
    parser = _Readable()
    parser.feed(markup)
    parser.close()
    return parser.result()


def _collapse(value: str) -> str:
    return " ".join(value.split())


def _lines(value: str) -> str:
    """Whitespace collapsed within each line, blank lines dropped.

    Markup indentation is not structure, and a page whose text is 80% newlines spends a
    prompt's budget on nothing.
    """
    collapsed = (_collapse(line) for line in value.splitlines())
    return "\n".join(line for line in collapsed if line)
