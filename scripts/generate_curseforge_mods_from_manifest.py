#!/usr/bin/env python3
"""
Generate curseforge-mods.txt from a modpack manifest (or related files).

Supports:
- Standard CurseForge export: { "files": [ { "projectID": N, "fileID": M }, ... ] }
- Nested/alternate key names: projectId/fileId anywhere in the JSON tree
- modlist.html (CurseForge companion file) with project/file links
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from html.parser import HTMLParser
from typing import Any


CF_PAIR_FROM_URL = re.compile(
    r"curseforge\.com/minecraft/(?:mc-mods|modpacks)/[^/\"']+/files/(\d+)",
    re.IGNORECASE,
)
CF_PROJECT_FROM_URL = re.compile(
    r"curseforge\.com/minecraft/(?:mc-mods|modpacks)/([^/\"']+)",
    re.IGNORECASE,
)


def find_cf_pairs_in_obj(obj: Any, found: list[tuple[int, int, bool]], seen: set[str]) -> None:
    """Recursively find {projectID, fileID} dicts anywhere in JSON."""
    if isinstance(obj, dict):
        pid = obj.get("projectID")
        if pid is None:
            pid = obj.get("projectId")
        fid = obj.get("fileID")
        if fid is None:
            fid = obj.get("fileId")
        if pid is not None and fid is not None:
            try:
                pid_i = int(pid)
                fid_i = int(fid)
            except (TypeError, ValueError):
                pid_i = fid_i = None  # type: ignore
            if pid_i is not None and fid_i is not None:
                key = f"{pid_i}:{fid_i}"
                if key not in seen:
                    seen.add(key)
                    required = bool(obj.get("required", True))
                    found.append((pid_i, fid_i, required))
        for value in obj.values():
            find_cf_pairs_in_obj(value, found, seen)
    elif isinstance(obj, list):
        for item in obj:
            find_cf_pairs_in_obj(item, found, seen)


class ModlistHtmlParser(HTMLParser):
  def __init__(self) -> None:
    super().__init__()
    self.links: list[str] = []

  def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
    if tag.lower() != "a":
      return
    href = dict(attrs).get("href")
    if href and "curseforge.com" in href:
      self.links.append(href)


def load_modlist_urls(path: str) -> list[str]:
    with open(path, encoding="utf-8", errors="ignore") as f:
        html = f.read()
    parser = ModlistHtmlParser()
    parser.feed(html)
    urls: list[str] = []
    seen: set[str] = set()
    for href in parser.links:
        if "curseforge.com" not in href or "/files/" not in href:
            continue
        if href in seen:
            continue
        seen.add(href)
        urls.append(href)
    return urls


def inspect_manifest(path: str) -> None:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    print(f"Top-level keys: {list(data.keys()) if isinstance(data, dict) else type(data)}")
    if isinstance(data, dict):
        for key in ("files", "mods", "projects", "manifestType", "minecraft", "name"):
            if key in data:
                val = data[key]
                if isinstance(val, list):
                    print(f"  {key}: list len={len(val)}")
                    if val and isinstance(val[0], dict):
                        print(f"    first entry keys: {list(val[0].keys())}")
                elif isinstance(val, dict):
                    print(f"  {key}: dict keys={list(val.keys())[:12]}")
                else:
                    print(f"  {key}: {val!r}")
    pairs: list[tuple[int, int, bool]] = []
    find_cf_pairs_in_obj(data, pairs, set())
    print(f"Recursive projectID/fileID pairs found: {len(pairs)}")
    if pairs[:3]:
        print(f"  examples: {pairs[:3]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="/minecraft/manifest.json")
    ap.add_argument("--modlist", default="", help="Optional modlist.html path")
    ap.add_argument("--out", default="generated-curseforge-mods.txt")
    ap.add_argument("--inspect", action="store_true", help="Print manifest structure and exit")
    args = ap.parse_args()

    if args.inspect:
        if not os.path.isfile(args.manifest):
            print(f"ERROR: not found: {args.manifest}", file=sys.stderr)
            return 2
        inspect_manifest(args.manifest)
        return 0

    if not os.path.isfile(args.manifest):
        print(f"ERROR: manifest not found: {args.manifest}", file=sys.stderr)
        return 2

    with open(args.manifest, encoding="utf-8") as f:
        data = json.load(f)

    pairs: list[tuple[int, int, bool]] = []
    seen: set[str] = set()
    find_cf_pairs_in_obj(data, pairs, seen)

    url_lines: list[str] = []

    modlist_path = args.modlist
    if not modlist_path:
        candidate = os.path.join(os.path.dirname(args.manifest), "modlist.html")
        if os.path.isfile(candidate):
            modlist_path = candidate

    if modlist_path and os.path.isfile(modlist_path):
        url_lines = load_modlist_urls(modlist_path)

    out_lines: list[str] = []
    for pid, fid, required in pairs:
        suffix = "" if required else "  # optional"
        out_lines.append(f"{pid}:{fid}{suffix}")

    for href in url_lines:
        if href not in out_lines:
            out_lines.append(href)

    if not out_lines:
        print("ERROR: no CurseForge projectID/fileID pairs found in manifest.", file=sys.stderr)
        print("", file=sys.stderr)
        print("Your manifest may be a server-pack manifest (jars already in /minecraft/mods),", file=sys.stderr)
        print("not a CurseForge download manifest. Run with --inspect to see its structure:", file=sys.stderr)
        print(f"  python3 {sys.argv[0]} --manifest {args.manifest} --inspect", file=sys.stderr)
        print("", file=sys.stderr)
        if os.path.isfile(os.path.join(os.path.dirname(args.manifest), "modlist.html")):
            print("Also try:", file=sys.stderr)
            print(f"  python3 {sys.argv[0]} --manifest {args.manifest} --modlist /minecraft/modlist.html", file=sys.stderr)
        print("", file=sys.stderr)
        print("For All of Create with jars already installed, stay on compose.existing.yaml (Phase 1)", file=sys.stderr)
        print("and add new mods manually to /minecraft/mods, OR add one line per mod to curseforge-mods.txt.", file=sys.stderr)
        return 2

    pack_name = data.get("name", "unknown") if isinstance(data, dict) else "unknown"
    mc = "?"
    if isinstance(data, dict) and isinstance(data.get("minecraft"), dict):
        mc = data["minecraft"].get("version", "?")

    with open(args.out, "w", encoding="utf-8") as f:
        f.write("# AUTO-GENERATED from manifest / modlist\n")
        f.write(f"# Pack: {pack_name} | MC: {mc}\n")
        f.write("# Format: projectID:fileID  OR full CurseForge file URL\n")
        f.write("# Requires CF_API_KEY in .env (escape $ as $$).\n")
        f.write("#\n")
        f.write("\n".join(out_lines))
        f.write("\n")

    print(f"Wrote: {args.out}")
    print(f"Entries: {len(out_lines)} ({len(pairs)} ID pairs, {len(url_lines)} URLs from modlist)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
