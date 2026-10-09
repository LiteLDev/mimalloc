#!/usr/bin/env python3
"""Stage the freshly built mimalloc DLLs (and their symbols) into the release archive."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import struct
import zipfile

try:
    import pefile
except ImportError:
    raise SystemExit("pefile is required to read the debug directory: python -m pip install pefile")

DLLS = ("mimalloc.dll", "mimalloc-redirect.dll")
PDB_MAGIC = b"Microsoft C/C++ MSF 7.00"
CODEVIEW_TYPE = 2  # IMAGE_DEBUG_TYPE_CODEVIEW


def find_file(build_dir: str, name: str) -> str | None:
    matches = [
        os.path.join(root, name)
        for root, _dirs, files in os.walk(build_dir)
        if name in files
    ]
    if not matches:
        return None
    matches.sort(key=lambda path: (0 if "release" in path.lower() else 1, path.count(os.sep), path))
    return matches[0]


def read_page(handle, page: int, page_size: int) -> bytes:
    handle.seek(page * page_size)
    return handle.read(page_size)


def image_symbols(image: str) -> tuple[bytes, int, str] | None:
    """Return the (guid, age, pdb name) recorded in the CodeView record of a PE image."""
    pe = pefile.PE(image, fast_load=True)
    try:
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DEBUG"]])
        for entry in getattr(pe, "DIRECTORY_ENTRY_DEBUG", ()):
            if entry.struct.Type != CODEVIEW_TYPE:
                continue
            record = pe.get_data(entry.struct.AddressOfRawData, entry.struct.SizeOfData)
            if record[:4] == b"RSDS":
                return (
                    record[4:20],
                    struct.unpack_from("<I", record, 20)[0],
                    record[24:].split(b"\x00", 1)[0].decode("latin1", "replace"),
                )
    finally:
        pe.close()
    return None


# The image side is read with pefile, but no pdb library works here: pdbparse
# crashes on current Visual Studio symbols and the published LIEF wheels are
# built without their pdb module, so the MSF 7.0 header is read directly.
def pdb_signature(symbols: str) -> tuple[bytes, int] | None:
    """Return the (guid, age) pair stored in the header of a MSF 7.0 pdb."""
    with open(symbols, "rb") as handle:
        header = handle.read(60)
        if not header.startswith(PDB_MAGIC):
            return None

        page_size, _free, _pages, directory_size, _reserved, block_map = struct.unpack_from("<6I", header, 32)
        directory_blocks = (directory_size + page_size - 1) // page_size
        handle.seek(block_map * page_size)
        directory_pages = struct.unpack(f"<{directory_blocks}I", handle.read(4 * directory_blocks))
        directory = b"".join(read_page(handle, page, page_size) for page in directory_pages)[:directory_size]

        # stream 1 (the pdb info stream) holds version, signature, age and guid
        stream_count = struct.unpack_from("<I", directory, 0)[0]
        sizes = struct.unpack_from(f"<{stream_count}I", directory, 4)
        cursor = 4 + 4 * stream_count
        info_pages: tuple[int, ...] = ()
        for index, size in enumerate(sizes):
            pages = (size + page_size - 1) // page_size
            page_numbers = struct.unpack_from(f"<{pages}I", directory, cursor) if pages else ()
            cursor += 4 * pages
            if index == 1:
                info_pages = page_numbers
                break
        if not info_pages:
            return None

        info = b"".join(read_page(handle, page, page_size) for page in info_pages)

    # version, signature, age, then the 16 byte guid
    _version, _signature, age = struct.unpack_from("<3I", info, 0)
    return info[12:28], age


def find_symbols(library: str, build_dir: str) -> tuple[str, str] | None:
    """Return the (path, staged name) of the pdb that belongs to *library*.

    A build tree also holds compiler pdbs (CMake ``/Fd``) whose names are easy to
    confuse with the linker one, so candidates are accepted only when the guid
    and age they carry match what the image recorded, and they are then staged
    under the name the image looks for.
    """
    signature = image_symbols(library)
    if signature is None:
        print(f"warning: {os.path.basename(library)} records no pdb, no symbols staged")
        return None

    guid, age, recorded = signature
    candidates = sorted(
        (
            os.path.join(root, name)
            for root, _dirs, files in os.walk(build_dir)
            for name in files
            if name.lower().endswith(".pdb")
        ),
        key=lambda path: (0 if os.path.dirname(path) == os.path.dirname(library) else 1, path),
    )
    for candidate in candidates:
        if pdb_signature(candidate) == (guid, age):
            name = os.path.basename(recorded.replace("\\", "/"))
            if not name.lower().endswith(".pdb"):
                name = os.path.basename(candidate)
            return candidate, name

    print(f"warning: no pdb matching {os.path.basename(library)} ({recorded}) was found")
    return None


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
        library = find_file(args.build_dir, name)
        if library is None:
            raise SystemExit(f"could not find {name} under {args.build_dir}")

        copies = [(library, os.path.basename(library))]
        symbols = find_symbols(library, args.build_dir)
        if symbols is not None:
            copies.append(symbols)

        for source, basename in copies:
            print(f"staging {source} -> {args.stage_dir}/{basename}")
            shutil.copy2(source, os.path.join(args.stage_dir, basename))
            staged.append(basename)

    with zipfile.ZipFile(args.output, "w", zipfile.ZIP_DEFLATED) as archive:
        folder = zipfile.ZipInfo(f"{stage_name}/")
        folder.external_attr = (0o40775 << 16) | 0x10
        archive.writestr(folder, b"")
        for name in staged:
            archive.write(os.path.join(args.stage_dir, name), f"{stage_name}/{name}")

    print(f"created {args.output} ({os.path.getsize(args.output)} bytes)")


if __name__ == "__main__":
    main()