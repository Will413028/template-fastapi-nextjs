#!/usr/bin/env python3
"""Sign up, sign in, read the user, sign out and read again against the running compose stack (CI's stack job).

Usage, from the repository root once `docker compose up` is ready:

    docker compose run --rm --no-deps -T -v ./scripts/stack_auth_smoke.py:/smoke.py:ro --entrypoint python \
        backend /smoke.py

It runs in a one-off container of the backend image on the compose network, so it reaches backend:8000 from another
address, as cloudflared does, and reads the origin secret from the backend's compose secret (never printed). It also
checks that a client's own X-Request-ID is replaced. Standard library only: the image has no test tools.
"""

import json
import pathlib
import sys
import urllib.error
import urllib.request
import uuid

BASE = "http://backend:8000/api"
SECRET_FILE = pathlib.Path("/run/secrets/origin_secret")
PASSWORD = "a long enough smoke-test passphrase"


class Client:
    def __init__(self, secret: str) -> None:
        self.secret = secret
        self.cookies: dict[str, str] = {}

    def call(
        self, method: str, path: str, body: object = None, headers: dict[str, str] | None = None
    ) -> tuple[int, dict[str, str], object]:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(BASE + path, data=data, method=method)
        request.add_header("X-Origin-Secret", self.secret)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if self.cookies:
            request.add_header("Cookie", "; ".join(f"{k}={v}" for k, v in self.cookies.items()))
        for name, value in (headers or {}).items():
            request.add_header(name, value)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                status, raw, message = response.status, response.read(), response.headers
        except urllib.error.HTTPError as err:
            status, raw, message = err.code, err.read(), err.headers
        for header in message.get_all("Set-Cookie") or []:
            name, _, rest = header.partition("=")
            value = rest.split(";", 1)[0]
            if "Max-Age=0" in header:
                self.cookies.pop(name, None)
            else:
                self.cookies[name] = value
        return status, {k.lower(): v for k, v in message.items()}, json.loads(raw) if raw else None


def expect(step: str, got: int, want: int) -> None:
    if got != want:
        sys.exit(f"{step}: status {got}, want {want}")
    print(f"{step}: {got}")


def main() -> None:
    client = Client(SECRET_FILE.read_text(encoding="utf-8").strip())
    account = f"smoke-{uuid.uuid4().hex[:12]}"
    credentials = {"account": account, "password": PASSWORD}
    status, _, _ = client.call("POST", "/users", credentials)
    expect("sign up", status, 201)
    client.cookies.clear()
    status, _, _ = client.call("POST", "/sessions", credentials)
    expect("sign in", status, 201)
    status, _, user = client.call("GET", "/users/me")
    expect("read the signed-in user", status, 200)
    if not isinstance(user, dict) or user.get("account") != account:
        sys.exit(f"read the signed-in user: got {user!r}")
    signed_out = dict(client.cookies)
    status, _, _ = client.call("DELETE", "/sessions/current")
    expect("sign out", status, 204)
    # with the cookie the browser had before signing out: the session must be gone, not only the cookie
    client.cookies = signed_out
    status, headers, _ = client.call("GET", "/users/me", headers={"X-Request-ID": "client-chosen"})
    expect("read with the signed-out session", status, 401)
    # the backend makes every request id itself (ADR vercel-rewrite-and-cloudflare-tunnel D4): a client's own must not
    # reach the logs or the response
    request_id = headers.get("x-request-id", "")
    if not request_id or request_id == "client-chosen":
        sys.exit(f"X-Request-ID is {request_id!r}: the client's own was kept or none was set")
    print("client-chosen X-Request-ID replaced")


if __name__ == "__main__":
    main()
