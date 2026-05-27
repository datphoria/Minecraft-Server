#!/usr/bin/env python3

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import zipfile


MODID_RE = re.compile(r'modId\s*=\s*"([^"]+)"', re.IGNORECASE)
FABRIC_BUILTIN_IDS = {
    "minecraft",
    "fabricloader",
    "fabric",
    "java",
    "quilt_loader",
    "quilted_fabric_api",
    "neoforge",
    "forge",
}


def _dedup_keep_order(items: list[str]) -> list[str]:
    out: list[str] = []
    seen = set()
    for it in items:
        it = it.strip()
        if not it or it in seen:
            continue
        seen.add(it)
        out.append(it)
    return out


def extract_modids_from_toml_in_jar(jar_path: str) -> list[str]:
    """
    Best-effort:
    - Forge:     META-INF/mods.toml
    - NeoForge:  META-INF/neoforge.mods.toml
    """
    try:
        with zipfile.ZipFile(jar_path, "r") as zf:
            names = {name.lower(): name for name in zf.namelist()}
            candidate = None
            for wanted in ("meta-inf/neoforge.mods.toml", "meta-inf/mods.toml"):
                if wanted in names:
                    candidate = names[wanted]
                    break
            if not candidate:
                return []
            raw = zf.read(candidate)
    except zipfile.BadZipFile:
        return []

    text = raw.decode("utf-8", errors="ignore")
    return _dedup_keep_order(MODID_RE.findall(text))


def extract_modids_from_fabric_jar(jar_path: str) -> list[str]:
    """
    Best-effort: Fabric jars contain fabric.mod.json at the jar root.
    The file can be an object or (rarely) an array of objects in spec; we handle both.
    """
    try:
        with zipfile.ZipFile(jar_path, "r") as zf:
            names = {name.lower(): name for name in zf.namelist()}
            if "fabric.mod.json" not in names:
                return []
            raw = zf.read(names["fabric.mod.json"])
    except zipfile.BadZipFile:
        return []

    text = raw.decode("utf-8", errors="ignore")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []

    ids: list[str] = []
    if isinstance(data, dict):
        mid = data.get("id")
        if isinstance(mid, str):
            ids.append(mid)
    elif isinstance(data, list):
        for entry in data:
            if isinstance(entry, dict):
                mid = entry.get("id")
                if isinstance(mid, str):
                    ids.append(mid)

    ids = [i for i in ids if i and i not in FABRIC_BUILTIN_IDS]
    return _dedup_keep_order(ids)


def modrinth_search(query: str, limit: int, timeout_s: int, facets: str | None) -> list[dict]:
    """
    Uses Modrinth API v3 search.
    Endpoint: https://api.modrinth.com/v3/search?query=...&limit=...
    Response: {"hits":[{"slug":"...","title":"...","versions":[...], ...}, ...]}
    """
    q = urllib.parse.quote(query)
    url = f"https://api.modrinth.com/v3/search?query={q}&limit={limit}"
    if facets:
        url += f"&facets={urllib.parse.quote(facets)}"
    req = urllib.request.Request(url, headers={"User-Agent": "minecraft-modlist-generator/1.0"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    payload = json.loads(body)
    hits = payload.get("hits", [])
    return hits


def slug_match_confidence(modid: str, hit: dict, target_mc: str | None) -> tuple[bool, str]:
    """
    Returns (required, reason).
    We mark required when the slug matches the modid exactly (case-insensitive).
    Otherwise we mark optional and return a reason for transparency.
    """
    slug = (hit.get("slug") or "").strip()
    if slug and slug.lower() == modid.lower():
        return True, "slug matches modId"

    # If we know the MC version and the project supports it, we might be slightly more confident.
    if target_mc:
        versions = hit.get("versions") or []
        if isinstance(versions, list) and target_mc in versions:
            return False, f"slug mismatch; project supports {target_mc}"

    return False, "slug mismatch"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mods-dir", default="/minecraft/mods", help="Folder containing mods*.jar")
    ap.add_argument("--out", default="generated-modrinth-mods.txt", help="Output mod list filename")
    ap.add_argument("--mc-version", default="1.21.1", help="Used for confidence heuristics (optional)")
    ap.add_argument(
        "--recursive",
        action="store_true",
        help="Scan subdirectories of --mods-dir (common for mods loaders and launchers)",
    )
    ap.add_argument("--search-limit", type=int, default=8, help="How many Modrinth search hits to fetch per modId")
    ap.add_argument("--timeout-s", type=int, default=20, help="HTTP timeout per query (seconds)")
    ap.add_argument("--optional-when-uncertain", action="store_true", default=True)
    args = ap.parse_args()

    mods_dir = args.mods_dir
    if not os.path.isdir(mods_dir):
        print(f"ERROR: mods dir not found: {mods_dir}", file=sys.stderr)
        return 2

    jar_paths: list[str] = []
    if args.recursive:
        for root, _dirs, files in os.walk(mods_dir):
            for f in files:
                if f.lower().endswith(".jar"):
                    jar_paths.append(os.path.join(root, f))
    else:
        jar_paths = [
            os.path.join(mods_dir, f) for f in os.listdir(mods_dir) if f.lower().endswith(".jar")
        ]
    jar_paths = sorted(jar_paths)
    if not jar_paths:
        print(f"ERROR: no .jar files found in: {mods_dir}", file=sys.stderr)
        return 2

    extracted_modids: list[str] = []
    seen_modids = set()
    for jar in jar_paths:
        mids: list[str] = []
        mids.extend(extract_modids_from_toml_in_jar(jar))
        if not mids:
            mids.extend(extract_modids_from_fabric_jar(jar))
        for mid in mids:
            if mid in seen_modids:
                continue
            seen_modids.add(mid)
            extracted_modids.append(mid)

    if not extracted_modids:
        print(
            "ERROR: no mod IDs found. Expected Forge/NeoForge META-INF/*.toml or Fabric fabric.mod.json.",
            file=sys.stderr,
        )
        return 2

    # Prefer mod results compatible with your target MC version and common loaders.
    facets = None
    if args.mc_version:
        # AND: project_type mod, versions MC
        # OR loader categories: neoforge or forge (Modrinth uses categories for loaders in search)
        facets = json.dumps(
            [
                ["project_type:mod"],
                [f"versions:{args.mc_version}"],
                ["categories:neoforge", "categories:forge"],
            ]
        )

    # Resolve modIds to Modrinth slugs.
    out_lines: list[str] = []
    unresolved: list[str] = []

    for modid in extracted_modids:
        try:
            hits = modrinth_search(modid, limit=args.search_limit, timeout_s=args.timeout_s, facets=facets)
        except Exception as e:
            unresolved.append(modid)
            print(f"WARN: Modrinth search failed for {modid}: {e}", file=sys.stderr)
            continue

        if not hits:
            unresolved.append(modid)
            print(f"WARN: no Modrinth results for {modid}", file=sys.stderr)
            continue

        # If we find an exact slug match, prefer it.
        required_choice = None
        for hit in hits:
            required, _reason = slug_match_confidence(modid, hit, args.mc_version)
            if required:
                required_choice = hit
                break

        if required_choice is None:
            required_choice = hits[0]
            required, reason = slug_match_confidence(modid, required_choice, args.mc_version)
            slug = (required_choice.get("slug") or "").strip()
            if not slug:
                unresolved.append(modid)
                continue
            if args.optional_when_uncertain:
                out_lines.append(f"{slug}?  # from modId={modid} ({reason})")
            else:
                out_lines.append(slug)
        else:
            slug = (required_choice.get("slug") or "").strip()
            if not slug:
                unresolved.append(modid)
                continue
            out_lines.append(slug)

    # Write results.
    out_path = args.out
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("# AUTO-GENERATED: initial Modrinth slug guesses from existing /minecraft/mods jars.\n")
        f.write("# Review entries marked with '?' (optional) and fix any wrong slugs before switching to managed mod installs.\n")
        f.write("# Format: one slug per line; lines starting with '#' are ignored by the container.\n")
        f.write("#\n")
        f.write("\n".join(out_lines))
        f.write("\n")

    print(f"Wrote: {out_path}")
    print(f"Extracted modIds: {len(extracted_modids)}")
    print(f"Resolved lines: {len(out_lines)}")
    if unresolved:
        print(f"Unresolved modIds (no match): {len(unresolved)}", file=sys.stderr)
        # Keep it short; full list is in stderr as well.
        print("Examples: " + ", ".join(unresolved[:10]), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

