"""Suggest Xerberus protocol mappings from a local DuckDB.

Writes a Markdown report to ``/tmp/xerberus-mappings.md``. Candidates must be
manually verified before editing ``eth_defi/xerberus/mappings.py``.

Usage:

.. code-block:: shell

    poetry run python scripts/xerberus/update-xerberus-mappings.py
"""

import re
from pathlib import Path

from eth_defi.xerberus.constants import resolve_xerberus_database_path
from eth_defi.xerberus.database import XerberusDatabase
from eth_defi.xerberus.mappings import XERBERUS_PROTOCOL_MAPPINGS

OUTPUT_PATH = Path("/tmp/xerberus-mappings.md")


def _norm(name: str) -> str:
    name = name.lower()
    for suffix in (" finance", " protocol", " network", " v2", " v3", " vaults"):
        name = name.replace(suffix, "")
    return re.sub(r"[^a-z0-9]+", "", name)


def main() -> None:
    """Generate mapping candidates by normalised name match."""
    db_path = resolve_xerberus_database_path()
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        return

    db = XerberusDatabase(db_path, read_only=True)
    try:
        protocols = db.get_latest_registry_entities(entity_type="protocol")
    finally:
        db.close()

    by_norm: dict[str, list[dict]] = {}
    for _, row in protocols.iterrows():
        name = row.get("name") or ""
        key = _norm(str(name))
        by_norm.setdefault(key, []).append(
            {
                "entity_id": row["entity_id"],
                "name": name,
                "score": row.get("score"),
            }
        )

    lines = [
        "# Xerberus protocol mapping candidates",
        "",
        "## Current mappings",
        "",
    ]
    for slug, xid in sorted(XERBERUS_PROTOCOL_MAPPINGS.items()):
        lines.append(f"- `{slug}` → `{xid}`")

    lines.extend(["", "## Name-match candidates (verify manually)", ""])

    # Load our protocol slugs from metadata if present
    meta_dir = Path("eth_defi/data/vaults/metadata")
    our_slugs: list[str] = []
    if meta_dir.is_dir():
        our_slugs = sorted(p.stem for p in meta_dir.glob("*.yaml"))

    for slug in our_slugs or sorted(XERBERUS_PROTOCOL_MAPPINGS.keys()):
        if XERBERUS_PROTOCOL_MAPPINGS.get(slug):
            continue
        key = _norm(slug.replace("-", " "))
        matches = by_norm.get(key, [])
        if matches:
            for m in matches:
                lines.append(f"- `{slug}` ~ `{m['entity_id']}` ({m['name']}, score={m['score']})")

    lines.extend(["", f"## Xerberus protocols in DB: {len(protocols)}", ""])
    for _, row in protocols.head(50).iterrows():
        lines.append(f"- `{row['entity_id']}` — {row.get('name')} (score={row.get('score')})")

    OUTPUT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
