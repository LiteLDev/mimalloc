#!/usr/bin/env python3
"""Stage the freshly built mimalloc DLLs and pack them into the release archive."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import zipfile

DLLS = ("mimalloc.dll", "mimalloc-redirect.dll")


def find_dll(build_dir: str, name: str) -> str:
    matches = [
        os.path.join(root, name)
        for root, _dirs, files in os.walk(build_dir)
        if name in files
    ]
    if not matches:
        raise SystemExit(f"could not find {name} under {build_dir}")
    matches.sort(key=lambda path: (0 if "release" in path.lower() else 1, path.count(os.sep), path))
    return matches[0]


def write_manifest(source: str, destination: str, version: str) -> None:
    with open(source, encoding="utf-8") as handle:
        data = json.load(handle)

    data["version"] = version

    with open(destination, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=4, ensure_ascii=False)
    print(f"writing {destination} (version={version})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", required=True, help="directory holding the compiled DLLs")
    parser.add_argument("--version", required=True, help="mimalloc version written into the manifest")
    parser.add_argument("--manifest", default="manifest.json", help="manifest template to use")
    parser.add_argument("--stage-dir", default="mimalloc", help="folder name used inside the archive")
    parser.add_argument("--output", default="mimalloc-windows-x64.zip", help="archive to create")
    args = parser.parse_args()

    if os.path.isdir(args.stage_dir):
        shutil.rmtree(args.stage_dir)
    os.makedirs(args.stage_dir)

    stage_name = os.path.basename(os.path.normpath(args.stage_dir))
    manifest_name = os.path.basename(args.manifest)
    write_manifest(args.manifest, os.path.join(args.stage_dir, manifest_name), args.version)

    staged = [manifest_name]
    for name in DLLS:
        source = find_dll(args.build_dir, name)
        print(f"staging {source} -> {args.stage_dir}/{name}")
        shutil.copy2(source, os.path.join(args.stage_dir, name))
        staged.append(name)

    with zipfile.ZipFile(args.output, "w", zipfile.ZIP_DEFLATED) as archive:
        folder = zipfile.ZipInfo(f"{stage_name}/")
        folder.external_attr = (0o40775 << 16) | 0x10
        archive.writestr(folder, b"")
        for name in staged:
            archive.write(os.path.join(args.stage_dir, name), f"{stage_name}/{name}")

    print(f"created {args.output} ({os.path.getsize(args.output)} bytes)")


if __name__ == "__main__":
    main()
