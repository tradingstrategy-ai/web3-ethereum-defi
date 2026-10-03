"""Discover slow-test modules without importing the complete test suite.

The main workflow validates discovery against pytest's actual markers. A new
indirectly marked module therefore fails loudly instead of falling out of CI.
"""

import re
from collections.abc import Iterator
from pathlib import Path

_SLOW_MARKER = re.compile(r"\.\s*mark\s*\.\s*slow\b")


def discover_slow_test_files(root: Path) -> Iterator[Path]:
    """Find modules containing a direct slow marker, in stable order.

    Include module-level and function-level markers without importing unrelated
    integration tests. Ignore GMX because its dedicated workflow owns that suite.

    :param root:
        Repository root containing the tests directory.
    :return:
        Absolute paths to candidate modules; pytest still applies ``-m slow``.
    """
    for path in sorted((root / "tests").rglob("test_*.py")):
        if "gmx" in path.relative_to(root / "tests").parts:
            continue
        if _SLOW_MARKER.search(path.read_text()):
            yield path.resolve()
