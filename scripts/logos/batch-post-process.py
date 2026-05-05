"""Batch post-process logos for all slugs missing generic.png or dark.png.

Selects the best input file from original_logos/{slug}/ and runs
post-process-logo.py for each missing output.

Usage:
    poetry run python scripts/logos/batch-post-process.py
"""

import logging
import os
import subprocess
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent.parent
ORIGINAL_LOGOS = REPO_ROOT / "eth_defi/data/vaults/original_logos"
FORMATTED_LOGOS = REPO_ROOT / "eth_defi/data/vaults/formatted_logos"
SCRIPT = REPO_ROOT / "scripts/logos/post-process-logo.py"

IMAGE_EXTS = {".svg", ".png", ".jpg", ".jpeg", ".webp"}

# Priority for selecting best "generic" source (lower index = higher priority)
GENERIC_PRIORITY = [
    lambda slug, files: _find(files, f"{slug}.generic", ".svg"),
    lambda slug, files: _find(files, f"{slug}.generic", ".png"),
    lambda slug, files: _find(files, f"{slug}.generic", ".jpg"),
    lambda slug, files: _find(files, f"{slug}.generic", ".jpeg"),
    lambda slug, files: _find(files, f"{slug}.generic", ".webp"),
    lambda slug, files: _find(files, f"{slug}.light", ".svg"),
    lambda slug, files: _find(files, f"{slug}.light", ".png"),
    lambda slug, files: _find(files, f"{slug}.light", ".jpg"),
    lambda slug, files: _find(files, f"{slug}.light", ".webp"),
    lambda slug, files: _find(files, f"{slug}.dark", ".svg"),
    lambda slug, files: _find(files, f"{slug}.dark", ".png"),
    lambda slug, files: _find(files, f"{slug}.dark", ".jpg"),
    # Fall back to any image file (pick first SVG, then PNG, etc.)
    lambda slug, files: next((f for f in files if f.suffix == ".svg"), None),
    lambda slug, files: next((f for f in files if f.suffix == ".png"), None),
    lambda slug, files: next((f for f in files if f.suffix in {".jpg", ".jpeg"}), None),
    lambda slug, files: next((f for f in files if f.suffix == ".webp"), None),
]

DARK_PRIORITY = [
    lambda slug, files: _find(files, f"{slug}.dark", ".svg"),
    lambda slug, files: _find(files, f"{slug}.dark", ".png"),
    lambda slug, files: _find(files, f"{slug}.dark", ".jpg"),
    # Also match files with "dark" anywhere in stem (not wordmark)
    lambda slug, files: next((f for f in files if "dark" in f.stem.lower() and "wordmark" not in f.stem.lower()), None),
]


def _find(files: list[Path], stem_prefix: str, ext: str) -> Path | None:
    for f in files:
        if f.stem.lower() == stem_prefix.lower() and f.suffix.lower() == ext.lower():
            return f
    return None


def select_input(slug: str, files: list[Path], priority: list) -> Path | None:
    for selector in priority:
        result = selector(slug, files)
        if result is not None:
            return result
    return None


def run_post_process(input_path: Path, output_path: Path) -> bool:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["INPUT_IMAGE"] = str(input_path)
    env["OUTPUT_IMAGE"] = str(output_path)
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    if result.returncode != 0:
        logger.error("FAILED %s → %s\n%s\n%s", input_path.name, output_path.name, result.stdout[-500:], result.stderr[-500:])
        return False
    logger.info("OK %s → %s", input_path.name, output_path.name)
    return True


def main() -> None:
    missing_generic: list[tuple[Path, Path]] = []
    missing_dark: list[tuple[Path, Path]] = []

    for slug_dir in sorted(ORIGINAL_LOGOS.iterdir()):
        if not slug_dir.is_dir():
            continue
        slug = slug_dir.name
        files = [f for f in slug_dir.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_EXTS]
        if not files:
            continue

        # Check generic.png
        generic_out = FORMATTED_LOGOS / slug / "generic.png"
        if not generic_out.exists():
            src = select_input(slug, files, GENERIC_PRIORITY)
            if src:
                missing_generic.append((src, generic_out))
            else:
                logger.warning("No suitable generic source for %s", slug)

        # Check dark.png
        dark_out = FORMATTED_LOGOS / slug / "dark.png"
        if not dark_out.exists():
            dark_files = [f for f in files if "dark" in f.stem.lower() and "wordmark" not in f.stem.lower()]
            if dark_files:
                src = select_input(slug, dark_files, DARK_PRIORITY)
                if src:
                    missing_dark.append((src, dark_out))

    total = len(missing_generic) + len(missing_dark)
    logger.info("Found %d missing generic.png and %d missing dark.png (%d total)", len(missing_generic), len(missing_dark), total)

    ok = 0
    fail = 0
    for src, out in missing_generic + missing_dark:
        variant = out.name
        slug = out.parent.name
        logger.info("[%s] %s ← %s", slug, variant, src.name)
        if run_post_process(src, out):
            ok += 1
        else:
            fail += 1

    logger.info("Done: %d OK, %d failed", ok, fail)
    if fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
