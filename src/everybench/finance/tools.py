"""Fixed public-data research tools. Python runs only in Inspect's network-disabled sandbox."""

from __future__ import annotations

import asyncio
import hashlib
import http.client
import ipaddress
import json
import os
import socket
import ssl
from html.parser import HTMLParser
from urllib.parse import urlencode, urljoin, urlsplit

from inspect_ai.tool import Tool, python, tool, web_search

MAX_FETCH_BYTES = 10_000_000


def public_address(url: str) -> tuple[str, str]:
    """Resolve once and pin the connection to a public address (including after redirects)."""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Only credential-free public HTTPS URLs are allowed.")
    if parsed.port not in (None, 443):
        raise ValueError("Only HTTPS port 443 is allowed.")
    host = parsed.hostname.encode("idna").decode()
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    ips = [row[4][0] for row in addresses]
    if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
        raise ValueError("Private, local and reserved network destinations are blocked.")
    return host, ips[0]


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host: str, address: str) -> None:
        super().__init__(host, timeout=20, context=ssl.create_default_context())
        self.address = address

    def connect(self) -> None:
        raw = socket.create_connection((self.address, 443), timeout=self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


class _VisibleHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in ("script", "style"):
            self.hidden += 1
        elif tag in ("p", "br", "div", "tr", "li", "h1", "h2", "h3"):
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def fetch_public(url: str) -> tuple[str, str]:
    """No cookies, auth headers, proxies or host files are forwarded to model-selected URLs."""
    for _ in range(6):
        host, address = public_address(url)
        parsed = urlsplit(url)
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        connection = _PinnedHTTPS(host, address)
        try:
            connection.request(
                "GET",
                path,
                headers={
                    "User-Agent": os.environ.get("SEC_EDGAR_USER_AGENT", "everybench/0.1 public research"),
                    "Accept-Encoding": "identity",
                },
            )
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader("Location")
                if not location:
                    raise ValueError("Redirect has no destination.")
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise ValueError(f"Public source returned HTTP {response.status}.")
            body = response.read(MAX_FETCH_BYTES + 1)
            if len(body) > MAX_FETCH_BYTES:
                raise ValueError("Source exceeds the fetch size limit.")
            mime = response.getheader("Content-Type", "").split(";")[0].lower()
            if mime == "application/pdf" or body.startswith(b"%PDF"):
                import io

                try:
                    from pypdf import PdfReader
                except ImportError as ex:
                    raise ValueError("PDF reading requires `uv sync --extra finance`.") from ex
                text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(body)).pages)
            elif mime.startswith("text/") or mime in ("application/json", "application/xhtml+xml"):
                text = body.decode("utf-8", errors="replace")
                if "html" in mime:
                    parser = _VisibleHTML()
                    parser.feed(text)
                    text = "".join(parser.parts)
            else:
                raise ValueError("Only public HTML, text, JSON and PDF documents are supported.")
            return url, text
        finally:
            connection.close()
    raise ValueError("Too many redirects.")


@tool
def fetch_url() -> Tool:
    cache: dict[str, tuple[str, str]] = {}

    async def execute(url: str, offset: int = 0, max_chars: int = 20000) -> str:
        """Read a public HTTPS financial document; page through long results using character offsets.

        Args:
            url: Public document URL. Local/private addresses and authenticated URLs are forbidden.
            offset: Character offset within the extracted text, starting at zero.
            max_chars: Number of characters to return, up to 30000.
        """
        if offset < 0 or not 1 <= max_chars <= 30000:
            raise ValueError("Invalid document window.")
        if url not in cache:
            fetched = await asyncio.to_thread(fetch_public, url)
            if len(cache) >= 16:
                cache.pop(next(iter(cache)))
            cache[url] = fetched
        source, text = cache[url]
        return json.dumps(
            {
                "source": source,
                "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "offset": offset,
                "total_chars": len(text),
                "text": text[offset : offset + max_chars],
            },
            ensure_ascii=False,
        )

    return execute


@tool
def edgar_search() -> Tool:
    async def execute(query: str) -> str:
        """Search the SEC EDGAR public full-text filings index.

        Args:
            query: Company, form, period or financial terms to search in SEC filings.
        """
        if not os.environ.get("SEC_EDGAR_USER_AGENT"):
            raise ValueError("Set SEC_EDGAR_USER_AGENT to your name and contact email.")
        _, text = await asyncio.to_thread(
            fetch_public, "https://efts.sec.gov/LATEST/search-index?" + urlencode({"q": query, "from": 0, "size": 10})
        )
        return text[:30000]

    return execute


def research_tools() -> list[Tool]:
    if not os.environ.get("TAVILY_API_KEY"):
        raise ValueError("Research mode requires TAVILY_API_KEY (search requests may be billed).")
    if not os.environ.get("SEC_EDGAR_USER_AGENT"):
        raise ValueError("Research mode requires SEC_EDGAR_USER_AGENT (name and email).")
    # Inspect's Python tool executes in the task sandbox, never via host subprocess.
    return [web_search(providers="tavily"), edgar_search(), fetch_url(), python(timeout=10)]
