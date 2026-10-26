import asyncio
import socket

import pytest

from everybench.finance import tools


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/doc",
        "file:///etc/passwd",
        "https://user:pass@example.com/doc",
        "https://example.com:8080/doc",
    ],
)
def test_nonpublic_url_forms_are_rejected_without_connecting(url):
    with pytest.raises(ValueError):
        tools.public_address(url)


@pytest.mark.parametrize("addresses", [["127.0.0.1"], ["169.254.169.254"], ["::1"], ["8.8.8.8", "10.0.0.1"]])
def test_all_resolved_addresses_must_be_public(monkeypatch, addresses):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", (ip, 443)) for ip in addresses])
    with pytest.raises(ValueError, match="blocked"):
        tools.public_address("https://example.com/doc")


def test_connection_destination_is_the_validated_address(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("8.8.8.8", 443))])
    host, address = tools.public_address("https://example.com/doc")
    assert (host, address) == ("example.com", "8.8.8.8")
    connection = tools._PinnedHTTPS(host, address)
    assert connection.host == host and connection.address == address


def test_redirect_to_private_destination_is_revalidated(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, *a, **k: [(2, 1, 6, "", ("127.0.0.1" if host == "localhost" else "8.8.8.8", 443))],
    )
    connections = []

    class Redirect:
        status = 302

        def getheader(self, name):
            return "https://localhost/private"

    class Connection:
        def __init__(self, host, address):
            connections.append(address)

        def request(self, *args, **kwargs):
            assert "Authorization" not in kwargs["headers"]

        def getresponse(self):
            return Redirect()

        def close(self):
            pass

    monkeypatch.setattr(tools, "_PinnedHTTPS", Connection)
    with pytest.raises(ValueError, match="blocked"):
        tools.fetch_public("https://example.com/doc")
    assert connections == ["8.8.8.8"]


def test_fetch_tool_keeps_complete_text_and_returns_explicit_windows(monkeypatch):
    document = "x" * 40000 + "IMPORTANT_END"
    monkeypatch.setattr(tools, "fetch_public", lambda url: (url, document))
    execute = tools.fetch_url()
    result = asyncio.run(execute("https://example.com/doc", offset=40000, max_chars=100))
    assert "IMPORTANT_END" in result and '"total_chars": 40013' in result
    assert "text_sha256" in result
