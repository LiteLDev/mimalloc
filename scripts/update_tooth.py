#!/usr/bin/env python3
"""Set the version field of tooth.json."""

from __future__ import annotations

import argparse
import json


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="version to write, for example 3.5.4")
    parser.add_argument("--file", default="tooth.json")
    args = parser.parse_args()

    with open(args.file, encoding="utf-8") as handle:
        data = json.load(handle)

    if "version" not in data:
        raise SystemExit(f"no version field in {args.file}")
    data["version"] = args.version

    with open(args.file, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, indent=4, ensure_ascii=False)
    print(f"{args.file}: version set to {args.version}")


if __name__ == "__main__":
    main()
