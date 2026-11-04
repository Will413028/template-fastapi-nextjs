"""Have I Been Pwned's Pwned Passwords range API with k-anonymity (Go version: internal/platform/hibp): only the first
5 hex characters of the password's SHA-1 leave the process, and responses are padded (Add-Padding) so their size does
not hint at the prefix. Enabled with PASSWORD_HIBP_CHECK=true.

It uses the standard library's http.client on a worker thread, so the check adds no dependency and never blocks the
event loop.
"""

import asyncio
import hashlib
import http.client
import urllib.parse

DEFAULT_BASE_URL = "https://api.pwnedpasswords.com"

# The whole check, DNS lookup included: short enough not to hold up a sign-up.
DEFAULT_TIMEOUT_SECONDS = 3.0

# The API asks every client to identify itself.
USER_AGENT = "template-fastapi-nextjs-password-check"


class HIBPError(Exception):
    """The range API could not answer; the message never contains the password or its hash."""


class HIBPClient:
    """Queries the range API at base_url (scheme://host[:port], http or https)."""

    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        parts = urllib.parse.urlsplit(base_url)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            msg = f"not an http(s) origin: {base_url!r}"
            raise ValueError(msg)
        self._https = parts.scheme == "https"
        self._host = parts.netloc
        self._timeout = timeout

    async def breached(self, password: str) -> bool:
        """Whether password appears in Pwned Passwords; padding entries have a count of 0 and do not count. Raises
        HIBPError when the API does not answer with 200 in time."""
        digest = hashlib.sha1(password.encode(), usedforsecurity=False).hexdigest().upper()
        prefix, suffix = digest[:5], digest[5:]
        try:
            async with asyncio.timeout(self._timeout):
                # a request past the deadline is abandoned, not stopped: its thread ends at the socket timeout
                body = await asyncio.to_thread(self._range, prefix)
        except TimeoutError:
            msg = f"hibp: no answer within {self._timeout:g} s"
            raise HIBPError(msg) from None
        for line in body.splitlines():
            found, _, count = line.strip().partition(":")
            if found.upper() == suffix:
                return count.strip() != "0"
        return False

    def _range(self, prefix: str) -> str:
        connection_class = http.client.HTTPSConnection if self._https else http.client.HTTPConnection
        conn = connection_class(self._host, timeout=self._timeout)
        try:
            conn.request("GET", f"/range/{prefix}", headers={"Add-Padding": "true", "User-Agent": USER_AGENT})
            response = conn.getresponse()
            body = response.read()
        except (OSError, http.client.HTTPException) as err:
            msg = f"hibp: {type(err).__name__}: {err}"
            raise HIBPError(msg) from None
        finally:
            conn.close()
        if response.status != http.client.OK:
            msg = f"hibp: {response.status} {response.reason}"
            raise HIBPError(msg)
        return body.decode("ascii", errors="replace")
