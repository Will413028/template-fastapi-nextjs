#!/usr/bin/env python3
"""Keep the release binaries pinned in Makefiles (hadolint, oasdiff) current, with verified checksums.

Dependabot reads manifests, not Makefiles, so these pins would otherwise age unnoticed. Tools that ship as packages
are pinned where Dependabot looks instead (the backend's dev tools in backend/pyproject.toml and backend/uv.lock).

Usage (from the repository root):
  uv run --no-project --python 3.14 python scripts/tool_versions.py verify          # offline: every pinned tool has a version and a checksum per platform,
                                                   # and backend/Dockerfile's uv image tag equals [tool.uv] required-version
  uv run --no-project --python 3.14 python scripts/tool_versions.py check           # print pins older than the newest release past the cooldown;
                                                   # exit 10 when any is, 1 when a source cannot be read
  uv run --no-project --python 3.14 python scripts/tool_versions.py bump <tool> [--version V]
                                                   # rewrite version and checksums from the release's checksum files

Releases younger than COOLDOWN_DAYS are ignored, the same cooldown as Dependabot (.github/dependabot.yml).
"""

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import sys
import tomllib
import urllib.error
import urllib.request

COOLDOWN_DAYS = 7
# check's exit status when a pin is behind, so a caller can tell it from a failure (1).
OUTDATED = 10
ROOT_MAKEFILE = pathlib.Path("Makefile")
BACKEND_MAKEFILE = pathlib.Path("backend/Makefile")
BACKEND_DOCKERFILE = pathlib.Path("backend/Dockerfile")
BACKEND_PYPROJECT = pathlib.Path("backend/pyproject.toml")


def http_get(url: str, method: str = "GET") -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(url, method=method, headers={"User-Agent": "tool-versions"})
    if url.startswith("https://api.github.com/") and os.environ.get("GITHUB_TOKEN"):
        req.add_header("Authorization", "Bearer " + os.environ["GITHUB_TOKEN"])
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers), b""


def make_var(text: str, name: str) -> str:
    m = re.search(rf"^{re.escape(name)}\s*:=\s*(\S+)", text, re.M)
    if not m:
        raise SystemExit(f"no {name} := … line")
    return m.group(1)


def set_make_var(text: str, name: str, value: str) -> str:
    new, n = re.subn(rf"^({re.escape(name)}\s*:=\s*)\S+", lambda m: m.group(1) + value, text, flags=re.M)
    if n != 1:
        raise SystemExit(f"expected one {name} := … line, found {n}")
    return new


# Each tool: where its version and checksums live, and how a release's checksums are published.
HADOLINT_PLATFORMS = ["macos-arm64", "linux-x86_64", "linux-arm64"]
OASDIFF_PLATFORMS = ["darwin_all", "linux_amd64", "linux_arm64"]


def github_latest(repo: str, cooldown: dt.date) -> tuple[str, dt.date]:
    status, _, body = http_get(f"https://api.github.com/repos/{repo}/releases?per_page=30")
    if status != 200:
        raise SystemExit(f"{repo}: GitHub releases returned {status}")
    for rel in json.loads(body):
        published = dt.date.fromisoformat(rel["published_at"][:10])
        if not rel["draft"] and not rel["prerelease"] and published <= cooldown:
            return rel["tag_name"].removeprefix("v"), published
    raise SystemExit(f"{repo}: no release older than the cooldown")


def checksum_lines(text: str) -> dict[str, str]:
    """Parse `<sha256>  <file>` or `<sha256> *<file>` lines."""
    sums = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and re.fullmatch(r"[0-9a-f]{64}", parts[0]):
            sums[parts[1].lstrip("*")] = parts[0]
    return sums


def fetch_text(url: str) -> str:
    status, _, body = http_get(url)
    if status != 200:
        raise SystemExit(f"{url}: {status}")
    return body.decode()


TOOLS = {
    "hadolint": {"file": ROOT_MAKEFILE, "version_var": "HADOLINT_VERSION"},
    "oasdiff": {"file": BACKEND_MAKEFILE, "version_var": "OASDIFF_VERSION"},
}


def pinned(tool: str) -> str:
    spec = TOOLS[tool]
    return make_var(spec["file"].read_text(), spec["version_var"]).removeprefix("v")


def latest(tool: str, cooldown: dt.date) -> tuple[str, dt.date]:
    if tool == "hadolint":
        return github_latest("hadolint/hadolint", cooldown)
    if tool == "oasdiff":
        return github_latest("oasdiff/oasdiff", cooldown)
    raise SystemExit(f"no release source for {tool}")


def new_checksums(tool: str, version: str) -> dict[str, str]:
    """Makefile variable -> checksum for the version, from the release's published checksum files."""
    if tool == "hadolint":
        sums = checksum_lines(
            fetch_text(f"https://github.com/hadolint/hadolint/releases/download/v{version}/checksums.sha256")
        )
        return {f"HADOLINT_SHA256_{p}": sums[f"hadolint-{p}"] for p in HADOLINT_PLATFORMS}
    if tool == "oasdiff":
        listing = fetch_text(f"https://github.com/oasdiff/oasdiff/releases/download/v{version}/checksums.txt")
        return archive_checksums(tool, version, listing)
    raise SystemExit(f"no checksum source for {tool}")


def archive_checksums(tool: str, version: str, listing: str) -> dict[str, str]:
    """Makefile variable -> checksum of the release's .tar.gz per platform (the archive the Makefile downloads)."""
    sums = checksum_lines(listing)
    prefix, platforms = PLATFORMS[tool]
    names = {p: f"{tool}_{version}_{p}.tar.gz" for p in platforms}
    missing = [n for n in names.values() if n not in sums]
    if missing:
        raise SystemExit(f"{tool} {version}: the release lists no {', '.join(missing)}; update the platforms for {tool}")
    return {f"{prefix}_{p}": sums[n] for p, n in names.items()}


PLATFORMS = {
    "hadolint": ("HADOLINT_SHA256", HADOLINT_PLATFORMS),
    "oasdiff": ("OASDIFF_SHA256", OASDIFF_PLATFORMS),
}


def pin_problems(tool: str, text: str) -> list[str]:
    """A pinned tool needs a version and one 64-hex checksum per platform, or `make` installs nothing verifiable."""
    problems = []
    if not re.search(rf"^{TOOLS[tool]['version_var']}\s*:=\s*\d+\.\d+\.\d+\s*$", text, re.M):
        problems.append(f"{tool}: {TOOLS[tool]['version_var']} is not a plain x.y.z version")
    prefix, platforms = PLATFORMS[tool]
    for p in platforms:
        m = re.search(rf"^{prefix}_{re.escape(p)}\s*:=\s*(\S+)", text, re.M)
        if not m or not re.fullmatch(r"[0-9a-f]{64}", m.group(1)):
            problems.append(f"{tool}: {prefix}_{p} is missing or not a sha256")
    return problems


def uv_pin_problems(dockerfile: str, pyproject: str) -> list[str]:
    """The uv image tag in backend/Dockerfile must equal [tool.uv] required-version, the pin CI's setup-uv reads.

    Only an exact `==x.y.z` counts: a range (`>=`, `~=`, `<`) lets CI and local runs pick a uv the image does not
    have, so "one uv everywhere" would hold by luck; it is reported rather than compared against the tag.
    """
    required = tomllib.loads(pyproject).get("tool", {}).get("uv", {}).get("required-version")
    m = re.fullmatch(r"==\s*(\d+\.\d+\.\d+)", (required or "").strip())
    if not m:
        return [f"uv: required-version {required} is not an exact ==x.y.z pin"]
    tag = re.search(r"^FROM\s+ghcr\.io/astral-sh/uv:(\d+\.\d+\.\d+)\b", dockerfile, re.M | re.I)
    if not tag:
        return [f"uv: {BACKEND_DOCKERFILE} has no ghcr.io/astral-sh/uv:x.y.z image"]
    if tag.group(1) != m.group(1):
        return [f"uv: {BACKEND_DOCKERFILE} uses uv {tag.group(1)} but required-version is {required}"]
    return []


def cmd_verify() -> int:
    problems = [p for tool in TOOLS for p in pin_problems(tool, TOOLS[tool]["file"].read_text())]
    problems += uv_pin_problems(BACKEND_DOCKERFILE.read_text(), BACKEND_PYPROJECT.read_text())
    for p in problems:
        print(p, file=sys.stderr)
    return 1 if problems else 0


def cmd_check() -> int:
    cooldown = dt.date.today() - dt.timedelta(days=COOLDOWN_DAYS)
    outdated = 0
    for tool in TOOLS:
        current = pinned(tool)
        newest, released = latest(tool, cooldown)
        if newest == current:
            print(f"{tool}: {current} (current)")
            continue
        outdated += 1
        print(f"{tool}: {current} -> {newest} (released {released}): uv run --no-project --python 3.14 python scripts/tool_versions.py bump {tool}")
    return OUTDATED if outdated else 0


def cmd_bump(tool: str, version: str | None) -> int:
    cooldown = dt.date.today() - dt.timedelta(days=COOLDOWN_DAYS)
    if version is None:
        version, _ = latest(tool, cooldown)
    spec = TOOLS[tool]
    sums = new_checksums(tool, version)
    text = spec["file"].read_text()
    text = set_make_var(text, spec["version_var"], version)
    for var, value in sums.items():
        text = set_make_var(text, var, value)
    spec["file"].write_text(text)
    print(f"{tool} -> {version}; checksums from the release's published checksum files")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("verify")
    sub.add_parser("check")
    bump = sub.add_parser("bump")
    bump.add_argument("tool", choices=sorted(TOOLS))
    bump.add_argument("--version")
    args = parser.parse_args()
    if args.cmd == "verify":
        return cmd_verify()
    if args.cmd == "check":
        return cmd_check()
    return cmd_bump(args.tool, args.version)


if __name__ == "__main__":
    sys.exit(main())
