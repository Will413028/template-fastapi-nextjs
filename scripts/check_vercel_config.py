#!/usr/bin/env python3
"""Check that frontend/vercel.json forwards /api to the backend with the origin secret, and holds no secret itself.

Usage: uv run --no-project --python 3.14 python scripts/check_vercel_config.py [path]   (default: frontend/vercel.json; run from the repository root)

The backend refuses an /api request without its X-Origin-Secret (backend/src/app/http/origin.py), so the route that
rewrites /api/* to BACKEND_ORIGIN must also set that header from the ORIGIN_SECRET environment variable. Vercel
expands $VAR only for names listed in the route's or the transform's `env`; an unlisted name is sent literally, and
then every API call fails in production while nothing fails here. The secret itself lives only in Vercel's
environment variables and the VM's .secrets/origin_secret, never in git.
"""

import json
import pathlib
import re
import sys

DEFAULT_PATH = "frontend/vercel.json"
API_ROUTE = "/api/(.*)"
SECRET_HEADER = "x-origin-secret"
# A run of 20 or more characters that base64, base64url or hex use, and nothing else: a token or key, not a URL, a
# pattern or a $VAR reference.
SECRET_LIKE = re.compile(r"[A-Za-z0-9+/_=-]{20,}")


def _references(text: str, name: str) -> bool:
    return text in {f"${name}", f"${{{name}}}"}


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [s for item in value for s in _strings(item)]
    if isinstance(value, dict):
        return [s for key, item in value.items() for s in [key, *_strings(item)]]
    return []


def _names(value: object) -> list[str]:
    """An env whitelist: Vercel takes a list of names; a single string is not one."""
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def _secret_transforms(route: dict) -> list[dict]:
    found = []
    transforms = route.get("transforms", [])
    for transform in transforms if isinstance(transforms, list) else []:
        if not isinstance(transform, dict):
            continue
        target = transform.get("target", {})
        key = target.get("key") if isinstance(target, dict) else None
        if transform.get("type") == "request.headers" and isinstance(key, str) and key.lower() == SECRET_HEADER:
            found.append(transform)
    return found


def problems(config: object) -> list[str]:
    if not isinstance(config, dict):
        return ["the file is not a JSON object"]
    routes = [r for r in config.get("routes", []) if isinstance(r, dict) and r.get("src") == API_ROUTE]
    if len(routes) != 1:
        return [f"want exactly one route with src {API_ROUTE!r}, found {len(routes)}"]
    route = routes[0]
    found: list[str] = []
    dest = route.get("dest", "")
    if not (isinstance(dest, str) and dest.endswith("/api/$1") and _references(dest.removesuffix("/api/$1"), "BACKEND_ORIGIN")):
        found.append(f"the route's dest must be ${{BACKEND_ORIGIN}}/api/$1, got {dest!r}")
    if "BACKEND_ORIGIN" not in _names(route.get("env")):
        found.append("the route's env must list BACKEND_ORIGIN, or Vercel sends ${BACKEND_ORIGIN} literally")
    transforms = _secret_transforms(route)
    if len(transforms) != 1:
        found.append(f"want exactly one request.headers transform for X-Origin-Secret on the route, found {len(transforms)}")
    else:
        transform = transforms[0]
        if transform.get("op") != "set":
            found.append(f"the X-Origin-Secret transform must use op set, got {transform.get('op')!r}")
        args = transform.get("args")
        if not (isinstance(args, str) and _references(args, "ORIGIN_SECRET")):
            found.append(f"the X-Origin-Secret transform's args must be exactly $ORIGIN_SECRET, got {args!r}")
        if "ORIGIN_SECRET" not in _names(transform.get("env")):
            found.append("the X-Origin-Secret transform's env must list ORIGIN_SECRET, or Vercel sends it literally")
    found.extend(
        f"a value that looks like a secret: {s[:4]}... ({len(s)} characters)"
        for s in _strings(config)
        if SECRET_LIKE.fullmatch(s)
    )
    return found


def main(argv: list[str]) -> int:
    path = pathlib.Path(argv[1] if len(argv) > 1 else DEFAULT_PATH)
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        print(f"{path}: {err}", file=sys.stderr)
        return 1
    found = problems(config)
    for problem in found:
        print(f"{path}: {problem}", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
