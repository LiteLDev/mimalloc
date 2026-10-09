#!/usr/bin/env python3
"""Detect a new upstream mimalloc release and expose the result as workflow outputs."""

from __future__ import annotations

import json
import os
import re
import urllib.request

UPSTREAM_REPO = os.environ.get("UPSTREAM_REPO", "microsoft/mimalloc")
TOOTH_JSON = os.environ.get("TOOTH_JSON", "tooth.json")
API_ROOT = "https://api.github.com"
SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def set_output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")
    print(f"[output] {name}={value}")


def api_get(path: str):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "mimalloc-auto-update",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(f"{API_ROOT}{path}", headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def latest_upstream_version() -> tuple[int, int, int]:
    best = None
    for page in range(1, 11):
        tags = api_get(f"/repos/{UPSTREAM_REPO}/tags?per_page=100&page={page}")
        if not tags:
            break
        for tag in tags:
            match = SEMVER_RE.match(tag["name"])
            if match:
                version = tuple(int(part) for part in match.groups())
                if best is None or version > best:
                    best = version
        if len(tags) < 100:
            break
    if best is None:
        raise SystemExit(f"could not find a semver tag in {UPSTREAM_REPO}")
    return best


def current_tooth_version() -> tuple[int, int, int]:
    with open(TOOTH_JSON, encoding="utf-8") as handle:
        raw = json.load(handle)["version"]
    match = SEMVER_RE.match(str(raw))
    if not match:
        raise SystemExit(f"unexpected version in {TOOTH_JSON}: {raw!r}")
    return tuple(int(part) for part in match.groups())


def format_version(version: tuple[int, int, int]) -> str:
    return ".".join(str(part) for part in version)


def main() -> None:
    current = current_tooth_version()
    forced = os.environ.get("FORCED_VERSION", "").strip()

    if forced:
        match = SEMVER_RE.match(forced)
        if not match:
            raise SystemExit(f"invalid version input: {forced!r}")
        target = tuple(int(part) for part in match.groups())
        has_update = True
        print("a specific version was requested, forcing a run")
    else:
        target = latest_upstream_version()
        has_update = target != current

    print(f"current version: {format_version(current)}")
    print(f"target version:  {format_version(target)}")

    set_output("version", format_version(target))
    set_output("tag", f"v{format_version(target)}")
    set_output("has_update", "true" if has_update else "false")


if __name__ == "__main__":
    main()
