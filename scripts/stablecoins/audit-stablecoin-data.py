"""Audit stablecoin metadata and feed YAML data for inconsistencies.

Deterministic structural + cross-link checks over

- ``eth_defi/data/stablecoins/*.yaml`` (metadata)
- ``eth_defi/data/feeds/stablecoins/*.yaml`` (news feeds)

It reports, grouped by severity:

- ERROR — malformed/blocking (bad address format, slug/feeder-id mismatch,
  dangling ``canonical-feeder-id``)
- WARN — likely wrong (non-checksummed EVM address, name drift, missing logo)
- INFO — content gaps (missing links / long_description)

and the metadata↔feed drift (twitter handle, website) that needs a human
decision. See ``eth_defi/data/stablecoins/TODO_STABLECOIN_DATA_FIXES_NEEDED.md``
for the triaged backlog.

No network access; safe to run any time::

    poetry run python scripts/stablecoins/audit-stablecoin-data.py
"""

import logging
import re
import sys
from collections import Counter
from pathlib import Path

import yaml
from eth_utils import to_checksum_address

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2] / "eth_defi" / "data"
META_DIR = ROOT / "stablecoins"
FEED_DIR = ROOT / "feeds" / "stablecoins"
LOGO_DIR = META_DIR / "formatted_logos"
FEED_ROLES = {
    "stablecoins": ROOT / "feeds" / "stablecoins",
    "curators": ROOT / "feeds" / "curators",
    "protocols": ROOT / "feeds" / "protocols",
}

VALID_CATEGORIES = {"stablecoin", "yield_bearing", "wrapped"}
LINK_FIELDS = ["homepage", "coingecko", "defillama", "twitter"]
CHECK_FIELDS = ["twitter_last_post_at", "domain_up_at", "marked_dead_at", "information_found_missing_at"]
EVM_ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
#: Chains whose addresses are not EVM hex and must skip the checksum/format check
NON_EVM_CHAINS = {"tron", "xdc", "solana", "near", "starknet"}


def load_yaml(p: Path) -> dict:
    return yaml.safe_load(p.read_text())


def tw_handle(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip().rstrip("/")
    m = re.search(r"(?:x\.com|twitter\.com)/([^/?#]+)", value)
    handle = m.group(1) if m else value
    return handle.lower().lstrip("@")


def domain(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip().rstrip("/")
    m = re.search(r"https?://(?:www\.)?([^/]+)", value)
    return (m.group(1) if m else value).lower()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)

    all_feeders: dict[str, list] = {}
    for role, d in FEED_ROLES.items():
        for p in sorted(d.glob("*.yaml")):
            fid = load_yaml(p).get("feeder-id")
            if fid:
                all_feeders.setdefault(fid, []).append(role)

    meta_files = {p.stem: p for p in META_DIR.glob("*.yaml")}
    feed_files = {p.stem: p for p in FEED_DIR.glob("*.yaml")}
    meta_data = {s: load_yaml(p) for s, p in meta_files.items()}
    feed_data = {s: load_yaml(p) for s, p in feed_files.items()}

    issues: list[tuple[str, str, str]] = []

    def add(slug: str, sev: str, msg: str) -> None:
        issues.append((slug, sev, msg))

    # Metadata without feed and vice versa
    for slug in sorted(set(meta_files) - set(feed_files)):
        multi = "entries" in meta_data[slug]
        add(slug, "INFO", "metadata has no feed (multi-project)" if multi else "metadata has no feed")
    for slug in sorted(set(feed_files) - set(meta_files)):
        add(slug, "WARN", "feed has no matching metadata")

    # Per-metadata checks
    for slug, d in sorted(meta_data.items()):
        multi = "entries" in d
        if d.get("slug") and d["slug"] != slug:
            add(slug, "ERROR", f"slug '{d['slug']}' != filename")
        if not d.get("symbol"):
            add(slug, "ERROR", "missing symbol")
        cat = d.get("category")
        if not multi and cat is not None and cat not in VALID_CATEGORIES:
            add(slug, "ERROR", f"invalid category '{cat}'")

        for ei, entry in enumerate([*d["entries"]] if multi else [d]):
            tag = f"entry[{ei}] " if multi else ""
            if not entry.get("short_description"):
                add(slug, "WARN", f"{tag}missing short_description")
            for ca in entry.get("contract_addresses") or []:
                chain, addr = ca.get("chain"), ca.get("address")
                if not chain:
                    add(slug, "ERROR", f"{tag}contract_address missing chain")
                if addr and chain not in NON_EVM_CHAINS:
                    if not EVM_ADDR_RE.match(addr):
                        add(slug, "ERROR", f"{tag}malformed EVM address {addr} ({chain})")
                    elif to_checksum_address(addr) != addr:
                        add(slug, "WARN", f"{tag}address not checksummed {addr} ({chain})")
            checks = entry.get("checks")
            if checks is not None:
                for cf in CHECK_FIELDS:
                    if cf not in checks:
                        add(slug, "WARN", f"{tag}checks missing key {cf}")

        if not (LOGO_DIR / slug / "light.png").exists():
            add(slug, "WARN", "missing formatted logo")

    # Per-feed checks
    for slug, d in sorted(feed_data.items()):
        if d.get("feeder-id") != slug:
            add(slug, "ERROR", f"feeder-id '{d.get('feeder-id')}' != filename")
        if d.get("role") != "stablecoin":
            add(slug, "WARN", f"role '{d.get('role')}' (expected stablecoin)")
        canon = d.get("canonical-feeder-id")
        if canon and canon not in all_feeders:
            add(slug, "ERROR", f"canonical-feeder-id '{canon}' does not resolve")

    # Name + cross-link drift
    for slug in sorted(set(meta_files) & set(feed_files)):
        md, fd = meta_data[slug], feed_data[slug]
        if "entries" in md or fd.get("canonical-feeder-id"):
            continue
        if md.get("name") and fd.get("name") and md["name"] != fd["name"]:
            add(slug, "WARN", f"name drift meta='{md['name']}' feed='{fd['name']}'")
        m_tw, f_tw = tw_handle((md.get("links") or {}).get("twitter")), tw_handle(fd.get("twitter"))
        if m_tw and f_tw and m_tw != f_tw:
            add(slug, "INFO", f"twitter drift meta=@{m_tw} feed=@{f_tw}")
        m_hp, f_ws = domain((md.get("links") or {}).get("homepage")), domain(fd.get("website"))
        if m_hp and f_ws and m_hp != f_ws:
            add(slug, "INFO", f"website drift meta={m_hp} feed={f_ws}")

    sev_order = {"ERROR": 0, "WARN": 1, "INFO": 2}
    issues.sort(key=lambda x: (sev_order[x[1]], x[0]))
    counts = Counter(s for _, s, _ in issues)
    logger.info("%d metadata, %d feed files", len(meta_files), len(feed_files))
    logger.info("issues: %s", dict(counts))
    for slug, sev, msg in issues:
        logger.info("[%s] %s: %s", sev, slug, msg)
    return 1 if counts.get("ERROR") else 0


if __name__ == "__main__":
    sys.exit(main())
