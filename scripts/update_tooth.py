#!/usr/bin/env python3
"""Write the version of tooth.json and record what the release was built from."""

from __future__ import annotations

import argparse
import json


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="version to write, for example 3.5.4")
    parser.add_argument("--file", default="tooth.json", help="package manifest to update")
    parser.add_argument("--upstream", default="", help="upstream tag the binaries were built from")
    parser.add_argument("--state", default="upstream.json", help="file recording the released build")
    args = parser.parse_args()

    with open(args.file, encoding="utf-8") as handle:
        data = json.load(handle)

    if "version" not in data:
        raise SystemExit(f"no version field in {args.file}")
    data["version"] = args.version

    with open(args.file, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, indent=4, ensure_ascii=False)
        handle.write("\n")
    print(f"{args.file}: version set to {args.version}")

    if args.upstream:
        with open(args.state, "w", encoding="utf-8", newline="\n") as handle:
            json.dump({"upstream": args.upstream, "version": args.version}, handle, indent=4, ensure_ascii=False)
            handle.write("\n")
        print(f"{args.state}: {args.upstream} released as {args.version}")


if __name__ == "__main__":
    main()