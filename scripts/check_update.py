#!/usr/bin/env python3
"""Decide whether this repository has to build and publish a new release.

Two versions are tracked apart:

* ``upstream.json`` -> ``upstream``: the upstream tag whose binaries are shipped.
* ``tooth.json`` -> ``version``: the version this package is published under,
  which may run ahead of upstream when the package itself changes (a new tooth
  format has to be released again because clients cache by version, ...).

lip resolves a github.com package from the ``v*`` tags of the repository and
orders them by semver precedence (prereleases included, an installed package is
only replaced by a strictly greater version), so every release has to be greater
than the last one.

Exposes ``has_update``, ``version``, ``tag`` and ``upstream_ref`` as outputs.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

UPSTREAM_REPO = os.environ.get("UPSTREAM_REPO", "microsoft/mimalloc")
TOOTH_JSON = os.environ.get("TOOTH_JSON", "tooth.json")
STATE_JSON = os.environ.get("STATE_JSON", "upstream.json")
API_ROOT = "https://api.github.com"
# upstream releases only, so a stray upstream prerelease never becomes a release here
RELEASE_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
# our own versions may be prereleases, build metadata is ignored by semver and so is rejected
VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$")

Core = tuple[int, int, int]
Version = tuple[Core, tuple[str, ...]]


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


def parse_version(text: str, pattern: re.Pattern[str] = VERSION_RE) -> Version | None:
    match = pattern.match(str(text).strip())
    if not match:
        return None
    groups = match.groups()
    core = tuple(int(part) for part in groups[:3])
    prerelease = groups[3] if len(groups) > 3 else None
    return core, tuple(part for part in (prerelease or "").split(".") if part)


def format_version(version: Version) -> str:
    text = ".".join(str(part) for part in version[0])
    return f"{text}-{'.'.join(version[1])}" if version[1] else text


def compare_prerelease(left: tuple[str, ...], right: tuple[str, ...]) -> int:
    if not left or not right:
        if left == right:
            return 0
        return 1 if not left else -1  # a release is greater than its own prereleases
    for one, other in zip(left, right):
        if one == other:
            continue
        if one.isdigit() and other.isdigit():
            return -1 if int(one) < int(other) else 1
        if one.isdigit() != other.isdigit():
            return -1 if one.isdigit() else 1  # numeric identifiers sort before alphanumeric ones
        return -1 if one < other else 1
    if len(left) == len(right):
        return 0
    return -1 if len(left) < len(right) else 1


def compare_versions(left: Version, right: Version) -> int:
    """Compare two versions the way lip does (semver precedence)."""
    if left[0] != right[0]:
        return -1 if left[0] < right[0] else 1
    return compare_prerelease(left[1], right[1])


def maximum(*versions: Version) -> Version:
    best = versions[0]
    for version in versions[1:]:
        if compare_versions(version, best) > 0:
            best = version
    return best


def next_version(version: Version) -> Version:
    """Return the smallest version lip accepts as an update of *version*."""
    if version[1]:
        return version[0], ()
    return (version[0][0], version[0][1], version[0][2] + 1), ()


def latest_upstream_release() -> tuple[Version, str]:
    best: Version | None = None
    best_tag = ""
    for page in range(1, 11):
        tags = api_get(f"/repos/{UPSTREAM_REPO}/tags?per_page=100&page={page}")
        if not tags:
            break
        for tag in tags:
            version = parse_version(tag["name"], RELEASE_RE)
            if version is not None and (best is None or version[0] > best[0]):
                best, best_tag = version, tag["name"]
        if len(tags) < 100:
            break
    if best is None:
        raise SystemExit(f"could not find a semver tag in {UPSTREAM_REPO}")
    return best, best_tag


def upstream_tag_exists(tag: str) -> bool:
    try:
        api_get(f"/repos/{UPSTREAM_REPO}/git/ref/tags/{tag}")
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return False
        raise
    return True


def read_tooth_version() -> Version:
    with open(TOOTH_JSON, encoding="utf-8") as handle:
        raw = json.load(handle)["version"]
    version = parse_version(raw)
    if version is None:
        raise SystemExit(f"unexpected version in {TOOTH_JSON}: {raw!r} (semver, no build metadata)")
    return version


def read_state() -> tuple[str, str]:
    """Return the (upstream tag, published version) recorded by the last release."""
    try:
        with open(STATE_JSON, encoding="utf-8") as handle:
            state = json.load(handle)
    except FileNotFoundError:
        return "", ""
    return str(state.get("upstream", "")), str(state.get("version", ""))


def main() -> None:
    current = read_tooth_version()
    upstream, upstream_tag = latest_upstream_release()
    built_tag, published_tag = read_state()
    built, published = parse_version(built_tag), parse_version(published_tag)

    if built is None or published is None:
        built_tag, published_tag = f"v{format_version(current)}", format_version(current)
        built, published = current, current
        print(f"note: {STATE_JSON} records nothing yet, assuming {built_tag} -> {published_tag}")

    forced = os.environ.get("FORCED_VERSION", "").strip()
    forced_upstream = os.environ.get("FORCED_UPSTREAM_TAG", "").strip()

    if forced:
        version = parse_version(forced)
        if version is None:
            raise SystemExit(f"invalid version input: {forced!r} (semver, no build metadata)")
        if forced_upstream:
            ref = forced_upstream
        elif upstream_tag_exists(f"v{format_version(version)}"):
            ref = f"v{format_version(version)}"
        else:
            ref, upstream = built_tag, built
            print(f"note: upstream has no v{format_version(version)}, building {ref} again")
        if compare_versions(version, published) <= 0:
            print(
                f"warning: {format_version(version)} is not newer than {published_tag}: "
                "lip keeps preferring the newer version, so this one only reaches clients that pin it"
            )
        has_update = True
        print("a specific version was requested, forcing a run")
    elif built != upstream:
        version = maximum(upstream, current, next_version(published))
        ref = upstream_tag
        has_update = True
        if compare_versions(version, upstream) != 0:
            print(f"note: {upstream_tag} is already used, publishing {format_version(version)} instead")
    elif compare_versions(current, published) > 0:
        version = current
        ref = built_tag
        has_update = True
        print(f"{TOOTH_JSON} is ahead of the last release, publishing {format_version(version)} again from {ref}")
    else:
        version = published
        ref = built_tag
        has_update = False
        if compare_versions(current, published) != 0:
            print(f"warning: {TOOTH_JSON} is behind {published_tag}, nothing to publish")

    print(f"upstream:  {upstream_tag}")
    print(f"built:     {built_tag}")
    print(f"published: {published_tag}")
    print(f"target:    {format_version(version)} from {ref}")

    set_output("version", format_version(version))
    set_output("tag", f"v{format_version(version)}")
    set_output("upstream_ref", ref)
    set_output("has_update", "true" if has_update else "false")


if __name__ == "__main__":
    main()