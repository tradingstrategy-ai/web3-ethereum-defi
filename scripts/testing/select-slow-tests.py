"""Write the slow workflow's module manifest without importing integration tests."""

import logging
import os
from pathlib import Path

from eth_defi.testing.slow_tests import discover_slow_test_files

logger = logging.getLogger(__name__)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    root = Path(__file__).resolve().parents[2]
    modules = list(discover_slow_test_files(root))
    assert modules, "No slow test modules found"
    output = Path(os.environ.get("SLOW_TEST_PATHS_FILE", "slow-test-paths.txt"))
    output.write_text("\n".join(str(path.relative_to(root)) for path in modules) + "\n")
    logger.info("Selected %d slow-test modules: %s", len(modules), output)
