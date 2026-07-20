"""Test the shared tokenised fund vault classification."""

import importlib
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

import eth_defi.tokenised_fund
from eth_defi.tokenised_fund.theo.constants import THBILL_ETHEREUM
from eth_defi.tokenised_fund.theo.vault import TheoITokenVault
from eth_defi.tokenised_fund.vault import TokenisedFundVault
from eth_defi.vault.base import VaultBase, VaultSpec
from eth_defi.vault.flag import VaultFlag


def find_tokenised_fund_vault_classes() -> tuple[type[VaultBase], ...]:
    """Discover every protocol adapter class from the package filesystem.

    Scanning protocol ``vault.py`` modules makes this regression guard include
    newly added integrations automatically instead of relying on a second
    manually maintained protocol list.

    :return:
        All concrete vault adapter classes defined by protocol modules.
    """

    package_path = Path(eth_defi.tokenised_fund.__file__).parent
    classes: list[type[VaultBase]] = []
    for module_path in sorted(package_path.glob("*/vault.py")):
        module_name = f"eth_defi.tokenised_fund.{module_path.parent.name}.vault"
        module = importlib.import_module(module_name)
        classes.extend(vault_class for _, vault_class in inspect.getmembers(module, inspect.isclass) if vault_class.__module__ == module_name and issubclass(vault_class, VaultBase))
    return tuple(classes)


TOKENISED_FUND_VAULT_CLASSES = find_tokenised_fund_vault_classes()


@pytest.mark.parametrize(
    "vault_class",
    TOKENISED_FUND_VAULT_CLASSES,
)
def test_all_tokenised_fund_adapters_use_shared_classification(vault_class: type[VaultBase]) -> None:
    """Require every protocol adapter to inherit tokenised fund flags.

    :param vault_class:
        Protocol adapter exported from :mod:`eth_defi.tokenised_fund`.
    """

    assert issubclass(vault_class, TokenisedFundVault)


def test_tokenised_fund_adapter_always_adds_descriptive_flag() -> None:
    """Add the listing flag even when no address-specific flag exists."""

    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=THBILL_ETHEREUM.chain_id))
    vault = TheoITokenVault(web3, VaultSpec(THBILL_ETHEREUM.chain_id, THBILL_ETHEREUM.token))

    assert VaultFlag.tokenised_fund in vault.get_flags()
