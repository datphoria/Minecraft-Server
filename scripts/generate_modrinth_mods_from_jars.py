#!/usr/bin/env python3

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile


MODID_RE = re.compile(r'modId\s*=\s*"([^"]+)"', re.IGNORECASE)

# Never try to resolve these via Modrinth
SKIP_MODIDS = {
    "minecraft",
    "neoforge",
    "forge",
    "fabricloader",
    "fabric",
    "java",
    "quilt_loader",
    "configureddefaults",
    "crash_assistant",
}

FABRIC_BUILTIN_IDS = SKIP_MODIDS | {
    "quilted_fabric_api",
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


def http_get_json(url: str, timeout_s: int) -> dict | list | None:
    req = urllib.request.Request(url, headers={"User-Agent": "minecraft-modlist-generator/2.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def modrinth_project_by_slug(slug: str, timeout_s: int) -> dict | None:
    url = f"https://api.modrinth.com/v2/project/{urllib.parse.quote(slug)}"
    data = http_get_json(url, timeout_s)
    return data if isinstance(data, dict) else None


def modrinth_search(query: str, limit: int, timeout_s: int) -> list[dict]:
    q = urllib.parse.quote(query)
    url = f"https://api.modrinth.com/v2/search?query={q}&limit={limit}"
    data = http_get_json(url, timeout_s)
    if not isinstance(data, dict):
        return []
    return data.get("hits") or []


def slug_candidates(modid: str) -> list[str]:
    """modId in jars often uses underscores; Modrinth slugs often use hyphens."""
    cands = [modid, modid.replace("_", "-")]
    return _dedup_keep_order(cands)


def resolve_modrinth_slug(modid: str, search_limit: int, timeout_s: int) -> tuple[str | None, str]:
    """
    Returns (slug, reason). Tries direct project lookup first, then plain search.
    """
    for cand in slug_candidates(modid):
        proj = modrinth_project_by_slug(cand, timeout_s)
        if proj and proj.get("slug"):
            return str(proj["slug"]), f"direct lookup ({cand})"

    hits = modrinth_search(modid, limit=search_limit, timeout_s=timeout_s)
    if not hits:
        alt = modid.replace("_", "-")
        if alt != modid:
            hits = modrinth_search(alt, limit=search_limit, timeout_s=timeout_s)

    if not hits:
        return None, "no Modrinth project"

    for hit in hits:
        slug = (hit.get("slug") or "").strip()
        if slug and slug.lower() == modid.lower():
            return slug, "search exact slug"
        if slug and slug.lower() == modid.replace("_", "-").lower():
            return slug, "search slug (underscore→hyphen)"

    first = hits[0]
    slug = (first.get("slug") or "").strip()
    if slug:
        return slug, f"search best guess ({first.get('title', '?')})"
    return None, "no slug in search hit"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mods-dir", default="/minecraft/mods")
    ap.add_argument("--out", default="generated-modrinth-mods.txt")
    ap.add_argument("--mc-version", default="1.21.1", help="Recorded in header only")
    ap.add_argument("--recursive", action="store_true")
    ap.add_argument("--search-limit", type=int, default=5)
    ap.add_argument("--timeout-s", type=int, default=20)
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
        jar_paths = [os.path.join(mods_dir, f) for f in os.listdir(mods_dir) if f.lower().endswith(".jar")]
    jar_paths = sorted(jar_paths)

    if not jar_paths:
        print(f"ERROR: no .jar files in {mods_dir}", file=sys.stderr)
        return 2

    extracted_modids: list[str] = []
    seen_modids: set[str] = set()
    for jar in jar_paths:
        mids = extract_modids_from_toml_in_jar(jar) or extract_modids_from_fabric_jar(jar)
        for mid in mids:
            if mid in seen_modids or mid in SKIP_MODIDS:
                continue
            seen_modids.add(mid)
            extracted_modids.append(mid)

    if not extracted_modids:
        print("ERROR: no mod IDs found in jars.", file=sys.stderr)
        return 2

    out_lines: list[str] = []
    unresolved: list[str] = []

    for modid in extracted_modids:
        try:
            slug, reason = resolve_modrinth_slug(modid, args.search_limit, args.timeout_s)
        except Exception as e:
            unresolved.append(modid)
            print(f"WARN: lookup failed for {modid}: {e}", file=sys.stderr)
            continue

        if not slug:
            unresolved.append(modid)
            print(f"WARN: no Modrinth results for {modid}", file=sys.stderr)
            continue

        exact = reason.startswith("direct") or reason.startswith("search exact")
        if exact or not args.optional_when_uncertain:
            out_lines.append(slug)
        else:
            out_lines.append(f"{slug}?  # modId={modid} ({reason})")

    with open(args.out, "w", encoding="utf-8") as f:
        f.write("# AUTO-GENERATED from jar modIds (Modrinth only — many pack mods are CurseForge-only).\n")
        f.write(f"# MC version: {args.mc_version}\n")
        f.write("# For All of Create / CurseForge packs, prefer scripts/generate_curseforge_mods_from_manifest.py\n")
        f.write("#\n")
        f.write("\n".join(out_lines))
        f.write("\n")

    print(f"Wrote: {args.out}")
    print(f"JAR files scanned: {len(jar_paths)}")
    print(f"Unique modIds: {len(extracted_modids)}")
    print(f"Modrinth lines: {len(out_lines)}")
    print(f"Not on Modrinth (use CurseForge list): {len(unresolved)}")
    if unresolved:
        print("Examples: " + ", ".join(unresolved[:15]), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
