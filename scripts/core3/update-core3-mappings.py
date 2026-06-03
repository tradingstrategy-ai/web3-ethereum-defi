"""Generate Core3 mapping tables and discover missing mappings.

Reads our vault protocol metadata and the Core3 DuckDB database,
then writes a Markdown report with three tables:

1. Our vault protocols (name, slug)
2. Core3 projects (name, slug, category, PoL score)
3. Current mappings and overlap

For unmapped protocols, applies heuristics to suggest matches:

- Exact slug match
- Website domain match (our metadata ``links.homepage`` vs Core3 ``links.website``)
- DeFi Llama slug match (our ``links.defillama`` vs Core3 ``coingecko_id``)
- Fuzzy name similarity

Usage:

.. code-block:: shell

    poetry run python scripts/core3/update-core3-mappings.py

Environment variables:

- ``CORE3_DATABASE_PATH``: Path to DuckDB database file. Default: ~/.tradingstrategy/vaults/core3/core3.duckdb
- ``LOG_LEVEL``: Logging level. Default: warning
"""

import json
import logging
import os
from pathlib import Path
from urllib.parse import urlparse

import yaml

from eth_defi.core3.constants import CORE3_DATABASE_PATH
from eth_defi.core3.database import Core3Database
from eth_defi.core3.mappings import CORE3_MAPPINGS
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)

#: Output file for the Markdown report.
OUTPUT_PATH = Path("/tmp/core3-mappings.md")

#: Path to our vault protocol metadata YAML files.
METADATA_DIR = Path(__file__).resolve().parents[2] / "eth_defi" / "data" / "vaults" / "metadata"


def _load_our_protocols() -> list[dict]:
    """Load all vault protocol metadata YAML files.

    :return:
        List of dicts with ``name``, ``slug``, ``homepage``, ``defillama`` keys.
    """
    protocols = []
    for yaml_path in sorted(METADATA_DIR.glob("*.yaml")):
        with open(yaml_path) as f:
            data = yaml.safe_load(f)
        links = data.get("links") or {}
        protocols.append(
            {
                "name": data.get("name", ""),
                "slug": data.get("slug", yaml_path.stem),
                "homepage": links.get("homepage", ""),
                "defillama": links.get("defillama", ""),
            }
        )
    return protocols


def _load_core3_projects(db: Core3Database) -> dict[str, dict]:
    """Load all Core3 projects from the database.

    :return:
        Dict keyed by Core3 slug with name, category, pol_score, website, coingecko_id.
    """
    df = db.get_latest_project_snapshots()
    projects = {}
    for _, row in df.iterrows():
        payload = json.loads(row["payload"])
        cat = (payload.get("category") or {}).get("name", "")
        links = payload.get("links") or {}
        projects[row["slug"]] = {
            "name": row["name"],
            "category": cat,
            "pol_score": row["pol_score"],
            "rank": row["rank"],
            "website": links.get("website", ""),
            "coingecko_id": payload.get("coingecko_id", ""),
        }
    return projects


def _extract_domain(url: str) -> str:
    """Extract the registrable domain from a URL, stripping ``www.`` and ``app.``.

    :param url:
        Full URL string.
    :return:
        Lowercase domain like ``morpho.org``.
    """
    if not url:
        return ""
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return ""
    # Strip common prefixes
    for prefix in ("www.", "app."):
        if host.startswith(prefix):
            host = host[len(prefix) :]
    return host.lower()


def _extract_defillama_slug(url: str) -> str:
    """Extract the DeFi Llama protocol slug from a URL.

    E.g. ``https://defillama.com/protocol/morpho`` → ``morpho``.

    :param url:
        DeFi Llama URL.
    :return:
        Lowercase slug, or empty string.
    """
    if not url:
        return ""
    path = urlparse(url).path.rstrip("/")
    parts = path.split("/")
    if len(parts) >= 3 and parts[1] == "protocol":
        return parts[2].lower()
    return ""


def _normalise_name(name: str) -> str:
    """Normalise a protocol name for fuzzy comparison.

    Strips common suffixes, lowercases, removes non-alphanumeric characters.

    :param name:
        Protocol name.
    :return:
        Normalised string.
    """
    name = name.lower()
    for suffix in (" finance", " protocol", " network", " dao", " labs", ".fi", ".finance", ".xyz"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    # Remove non-alphanumeric
    return "".join(c for c in name if c.isalnum())


def find_candidate_mappings(
    our_protocols: list[dict],
    core3_projects: dict[str, dict],
) -> dict[str, tuple[str, str]]:
    """Find candidate Core3 mappings for unmapped protocols.

    Applies heuristics in priority order:

    1. Exact slug match
    2. Website domain match
    3. DeFi Llama slug match against coingecko_id
    4. Normalised name match

    :param our_protocols:
        Our vault protocol list from :func:`_load_our_protocols`.
    :param core3_projects:
        Core3 project dict from :func:`_load_core3_projects`.
    :return:
        Dict of our_slug → (core3_slug, reason) for newly discovered candidates.
    """
    # Build reverse indexes for Core3
    domain_to_slug: dict[str, str] = {}
    cgid_to_slug: dict[str, str] = {}
    normalised_name_to_slug: dict[str, str] = {}

    for c3_slug, c3 in core3_projects.items():
        domain = _extract_domain(c3["website"])
        if domain:
            domain_to_slug[domain] = c3_slug

        cgid = c3["coingecko_id"]
        if cgid:
            cgid_to_slug[cgid.lower()] = c3_slug

        norm = _normalise_name(c3["name"])
        if norm:
            normalised_name_to_slug[norm] = c3_slug

    candidates = {}

    for proto in our_protocols:
        our_slug = proto["slug"]

        # Skip already mapped
        if our_slug in CORE3_MAPPINGS:
            continue

        # 1. Exact slug match
        if our_slug in core3_projects:
            candidates[our_slug] = (our_slug, "exact slug match")
            continue

        # 2. Website domain match
        our_domain = _extract_domain(proto["homepage"])
        if our_domain and our_domain in domain_to_slug:
            candidates[our_slug] = (domain_to_slug[our_domain], f"website domain match ({our_domain})")
            continue

        # 3. DeFi Llama slug → CoinGecko ID match
        llama_slug = _extract_defillama_slug(proto["defillama"])
        if llama_slug and llama_slug in cgid_to_slug:
            candidates[our_slug] = (cgid_to_slug[llama_slug], f"DeFi Llama slug matches coingecko_id ({llama_slug})")
            continue

        # 4. Normalised name match
        our_norm = _normalise_name(proto["name"])
        if our_norm and our_norm in normalised_name_to_slug:
            candidates[our_slug] = (normalised_name_to_slug[our_norm], f"normalised name match ({our_norm})")
            continue

    return candidates


def _write_markdown(
    our_protocols: list[dict],
    core3_projects: dict[str, dict],
    candidates: dict[str, tuple[str, str]],
) -> str:
    """Generate Markdown report with mapping tables.

    :return:
        Markdown string.
    """
    lines = ["# Core3 mapping report", ""]

    # Table 1: Our vault protocols
    lines.append("## Our vault protocols")
    lines.append("")
    lines.append(f"Total: {len(our_protocols)}")
    lines.append("")
    lines.append("| Slug | Name | Homepage |")
    lines.append("|------|------|----------|")
    for p in our_protocols:
        lines.append(f"| {p['slug']} | {p['name']} | {p['homepage']} |")
    lines.append("")

    # Table 2: Core3 DeFi-related projects
    DEFI_CATS = {
        "Decentralized Finance",
        "Lending/Borrowing",
        "Decentralized Exchange",
        "Liquid Staking",
        "RWA",
        "Stablecoin",
    }
    defi = [(slug, p) for slug, p in core3_projects.items() if p["category"] in DEFI_CATS]
    defi.sort(key=lambda x: x[1]["rank"] or 9999)

    lines.append("## Core3 DeFi-related projects")
    lines.append("")
    lines.append(f"Total: {len(defi)} (out of {len(core3_projects)} total)")
    lines.append("")
    lines.append("| Slug | Name | Category | PoL | Rank |")
    lines.append("|------|------|----------|-----|------|")
    for slug, p in defi:
        pol = f"{p['pol_score']:.1f}" if p["pol_score"] else "N/A"
        rank = str(p["rank"]) if p["rank"] else "N/A"
        lines.append(f"| {slug} | {p['name']} | {p['category']} | {pol} | {rank} |")
    lines.append("")

    # Table 3: Current mappings
    lines.append("## Current mappings")
    lines.append("")
    mapped = {k: v for k, v in CORE3_MAPPINGS.items() if v is not None}
    lines.append(f"Mapped: {len(mapped)} / {len(our_protocols)}")
    lines.append("")
    lines.append("| Our slug | Core3 slug | Core3 name | Category | PoL |")
    lines.append("|----------|------------|------------|----------|-----|")
    for our_slug, c3_slug in sorted(mapped.items()):
        c3 = core3_projects.get(c3_slug, {})
        name = c3.get("name", "?")
        cat = c3.get("category", "?")
        pol = f"{c3['pol_score']:.1f}" if c3.get("pol_score") else "N/A"
        lines.append(f"| {our_slug} | {c3_slug} | {name} | {cat} | {pol} |")
    lines.append("")

    # Table 4: Candidate new mappings
    if candidates:
        lines.append("## Candidate new mappings")
        lines.append("")
        lines.append(f"Found {len(candidates)} potential matches:")
        lines.append("")
        lines.append("| Our slug | Core3 slug | Core3 name | PoL | Method |")
        lines.append("|----------|------------|------------|-----|--------|")
        for our_slug, (c3_slug, reason) in sorted(candidates.items()):
            c3 = core3_projects.get(c3_slug, {})
            name = c3.get("name", "?")
            pol = f"{c3['pol_score']:.1f}" if c3.get("pol_score") else "N/A"
            lines.append(f"| {our_slug} | {c3_slug} | {name} | {pol} | {reason} |")
        lines.append("")

    # Table 5: Unmapped protocols
    all_mapped = set(CORE3_MAPPINGS.keys()) | set(candidates.keys())
    unmapped = [p for p in our_protocols if p["slug"] not in all_mapped]
    if unmapped:
        lines.append("## Unmapped protocols (no Core3 equivalent found)")
        lines.append("")
        lines.append(f"Total: {len(unmapped)}")
        lines.append("")
        lines.append("| Slug | Name |")
        lines.append("|------|------|")
        for p in unmapped:
            lines.append(f"| {p['slug']} | {p['name']} |")
        lines.append("")

    return "\n".join(lines)


def _generate_mappings_code(candidates: dict[str, tuple[str, str]]) -> str:
    """Generate Python code for new mappings to add to mappings.py.

    :return:
        Python source fragment to paste into CORE3_MAPPINGS.
    """
    if not candidates:
        return ""

    from datetime import date

    today = date.today().isoformat()
    lines = ["", "    # --- New candidates (verify before committing) ---"]
    for our_slug, (c3_slug, reason) in sorted(candidates.items()):
        lines.append(f"    # {today} — {reason}")
        lines.append(f'    "{our_slug}": "{c3_slug}",')

    return "\n".join(lines)


def main():
    default_log_level = os.environ.get("LOG_LEVEL", "warning")
    setup_console_logging(default_log_level=default_log_level)

    db_path_str = os.environ.get("CORE3_DATABASE_PATH")
    db_path = Path(db_path_str).expanduser() if db_path_str else CORE3_DATABASE_PATH

    if not db_path.exists():
        print(f"Core3 database not found: {db_path}")
        print("Run scan-core3.py first to populate the database.")
        return

    print(f"Loading Core3 database: {db_path}")
    db = Core3Database(db_path)

    try:
        our_protocols = _load_our_protocols()
        core3_projects = _load_core3_projects(db)
        print(f"Our protocols: {len(our_protocols)}")
        print(f"Core3 projects: {len(core3_projects)}")
        print(f"Existing mappings: {len([v for v in CORE3_MAPPINGS.values() if v is not None])}")

        # Find candidates
        candidates = find_candidate_mappings(our_protocols, core3_projects)
        print(f"New candidates found: {len(candidates)}")

        if candidates:
            print("\nCandidate mappings:")
            for our_slug, (c3_slug, reason) in sorted(candidates.items()):
                c3 = core3_projects.get(c3_slug, {})
                pol = f"{c3.get('pol_score', 0):.1f}" if c3.get("pol_score") else "N/A"
                print(f"  {our_slug:25s} → {c3_slug:25s} (PoL={pol}) [{reason}]")

        # Generate markdown
        md = _write_markdown(our_protocols, core3_projects, candidates)
        OUTPUT_PATH.write_text(md)
        print(f"\nMarkdown report written to: {OUTPUT_PATH}")

        # Show code to add
        code = _generate_mappings_code(candidates)
        if code:
            print("\n--- Add to CORE3_MAPPINGS in eth_defi/core3/mappings.py ---")
            print(code)
            print("--- end ---")

    finally:
        db.close()

    print("\nAll ok")


if __name__ == "__main__":
    main()
