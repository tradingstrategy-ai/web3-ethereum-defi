import shutil
import sys
from pathlib import Path
from tempfile import gettempdir

import pytest

from eth_defi.aave_v3.deployer import AaveDeployer


@pytest.fixture(scope="session")
def aave_deployer_test_path() -> Path:
    path = Path(gettempdir()).joinpath("aave-v3-deployer-test")
    # Clear at the start of the tets
    shutil.rmtree(path, ignore_errors=True)
    return path


@pytest.fixture(scope="session")
def aave_deployer(aave_deployer_test_path) -> AaveDeployer:
    """Set up Aave v3 deployer using git and npm.

    We use session scope, because this fixture is damn slow.
    """
    deployer = AaveDeployer(aave_deployer_test_path)
    deployer.install()
    return deployer

