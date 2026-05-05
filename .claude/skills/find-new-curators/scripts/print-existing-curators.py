"""Print existing curator feeder entries.

Reads the machine-readable curator YAML files from ``eth_defi/data/feeds``
and prints a compact table of current source-bearing and alias curators.
This helper is intended for the ``find-new-curators`` Claude skill.
"""

import os
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from tabulate import tabulate


@dataclass(slots=True, frozen=True)
class CuratorRow:
    """One curator feeder row for display.

    Curator rows come from ``eth_defi/data/feeds/curators/*.yaml``.  Alias
    rows use ``canonical-feeder-id`` and do not carry feed source fields.

    :param slug:
        Curator feeder id.
    :param name:
        Human-readable curator name.
    :param kind:
        ``source`` for source-bearing YAML files or ``alias`` for canonical
        feeder aliases.
    :param website:
        Configured website URL, if any.
    :param twitter:
        Configured Twitter/X handle, if any.
    :param linkedin:
        Configured LinkedIn company id, if any.
    :param rss:
        Configured RSS feed URL, if any.
    :param canonical_feeder_id:
        Canonical feeder id for aliases, if any.
    :param path:
        YAML path relative to the repository root.
    """

    slug: str
    name: str
    kind: str
    website: str
    twitter: str
    linkedin: str
    rss: str
    canonical_feeder_id: str
    path: Path


def find_repo_root(start: Path) -> Path:
    """Find the repository root from a script path.

    Walks upward until the expected feed data directory exists.

    :param start:
        Starting path.
    :return:
        Repository root path.
    :raise RuntimeError:
        If no repository root can be found.
    """

    for candidate in [start, *start.parents]:
        if (candidate / "eth_defi" / "data" / "feeds").exists():
            return candidate
    raise RuntimeError(f"Could not find repository root from {start}")


REPO_ROOT = find_repo_root(Path(__file__).resolve())
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from eth_defi.feed.sources import load_feeder_metadata  # noqa: E402


def read_bool_env(name: str, *, default: bool) -> bool:
    """Read a boolean environment variable.

    Accepts common true/false spellings used in shell scripts.

    :param name:
        Environment variable name.
    :param default:
        Value returned when the variable is not set.
    :return:
        Parsed boolean value.
    """

    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def iter_curator_rows(feeds_dir: Path, *, include_aliases: bool) -> Iterator[CuratorRow]:
    """Iterate existing curator feeder YAML files.

    :param feeds_dir:
        Feed data directory containing the ``curators`` subfolder.
    :param include_aliases:
        Whether to include alias YAML files.
    :return:
        Iterator of curator display rows.
    """

    curators_dir = feeds_dir / "curators"
    if not curators_dir.exists():
        raise FileNotFoundError(f"Curator feed directory does not exist: {curators_dir}")

    for yaml_path in sorted(curators_dir.glob("*.yaml")):
        metadata = load_feeder_metadata(yaml_path)
        canonical_feeder_id = metadata.get("canonical-feeder-id", "") or ""
        kind = "alias" if canonical_feeder_id else "source"
        if kind == "alias" and not include_aliases:
            continue
        yield CuratorRow(
            slug=metadata["feeder-id"],
            name=metadata["name"],
            kind=kind,
            website=metadata.get("website", "") or "",
            twitter=metadata.get("twitter", "") or "",
            linkedin=metadata.get("linkedin", "") or "",
            rss=metadata.get("rss", "") or "",
            canonical_feeder_id=canonical_feeder_id,
            path=yaml_path.relative_to(REPO_ROOT),
        )


def main() -> None:
    """Print the existing curator table.

    Environment variables:

    - ``FEEDS_DIR``: Feed data directory. Defaults to ``eth_defi/data/feeds``.
    - ``INCLUDE_ALIASES``: Include alias curators. Defaults to ``true``.
    """

    feeds_dir = Path(os.environ.get("FEEDS_DIR", REPO_ROOT / "eth_defi" / "data" / "feeds")).expanduser()
    if not feeds_dir.is_absolute():
        feeds_dir = REPO_ROOT / feeds_dir

    include_aliases = read_bool_env("INCLUDE_ALIASES", default=True)
    rows = list(iter_curator_rows(feeds_dir, include_aliases=include_aliases))
    table = [
        [
            row.slug,
            row.name,
            row.kind,
            row.canonical_feeder_id,
            row.twitter,
            row.linkedin,
            row.rss,
            row.path,
        ]
        for row in rows
    ]
    print(tabulate(table, headers=["slug", "name", "kind", "canonical", "twitter", "linkedin", "rss", "file"], tablefmt="github"))  # noqa: T201
    print(f"\nCurators: {len(rows)} shown")  # noqa: T201


if __name__ == "__main__":
    main()
