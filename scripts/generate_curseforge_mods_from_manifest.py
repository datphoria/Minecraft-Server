#!/usr/bin/env python3
"""
Generate curseforge-mods.txt from a CurseForge modpack manifest.json.

Best for "All of Create" and similar server packs exported from CurseForge.
Each line is: projectID:fileID  (pinned to the versions in your manifest)
"""

import argparse
import json
import os
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--manifest",
        default="/minecraft/manifest.json",
        help="Path to manifest.json (CurseForge modpack export)",
    )
    ap.add_argument("--out", default="generated-curseforge-mods.txt")
    args = ap.parse_args()

    if not os.path.isfile(args.manifest):
        print(f"ERROR: manifest not found: {args.manifest}", file=sys.stderr)
        print("Look for manifest.json in /minecraft (common for CurseForge server packs).", file=sys.stderr)
        return 2

    with open(args.manifest, encoding="utf-8") as f:
        data = json.load(f)

    files = data.get("files")
    if not isinstance(files, list) or not files:
        print("ERROR: manifest has no 'files' array.", file=sys.stderr)
        return 2

    lines: list[str] = []
    seen: set[str] = set()
    skipped = 0

    for entry in files:
        if not isinstance(entry, dict):
            continue
        pid = entry.get("projectID")
        fid = entry.get("fileID")
        if pid is None or fid is None:
            skipped += 1
            continue
        key = f"{pid}:{fid}"
        if key in seen:
            continue
        seen.add(key)
        required = entry.get("required", True)
        suffix = "" if required else "  # optional in manifest"
        lines.append(f"{pid}:{fid}{suffix}")

    if not lines:
        print("ERROR: no projectID/fileID pairs found in manifest.", file=sys.stderr)
        return 2

    pack_name = data.get("name", "unknown")
    mc = (data.get("minecraft") or {}).get("version", "?")

    with open(args.out, "w", encoding="utf-8") as f:
        f.write("# AUTO-GENERATED from CurseForge manifest.json\n")
        f.write(f"# Pack: {pack_name} | MC: {mc}\n")
        f.write("# Format: projectID:fileID — pins exact versions from your current pack.\n")
        f.write("# Requires CF_API_KEY in .env (escape $ as $$).\n")
        f.write("#\n")
        f.write("\n".join(lines))
        f.write("\n")

    print(f"Wrote: {args.out}")
    print(f"Mods listed: {len(lines)}")
    if skipped:
        print(f"Skipped malformed entries: {skipped}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
