"""Keep permissioned deployments outside the Upshift USDC shortlist."""

import importlib.util
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from eth_typing import HexAddress

from eth_defi.erc_4626.vault_protocol.upshift.vault import UpshiftVault
from eth_defi.token import USDC_NATIVE_TOKEN
from eth_defi.vault.base import VaultSpec


@pytest.mark.parametrize("owner", [None, "0x1111111111111111111111111111111111111111"])
def test_permissioned_upshift_excluded_even_for_admitted_owner(monkeypatch: pytest.MonkeyPatch, owner: HexAddress | None) -> None:
    """Allow-list membership must not clear a permissioned shortlist candidate.

    The diagnostic retains membership evidence for operators, while its
    permissionless shortlist always excludes the deployment.

    :param monkeypatch: Isolated provider and vault observations.
    :param owner: Missing or individually admitted custody address.
    """
    path = Path(__file__).resolve().parents[2] / "scripts/erc-4626/check-upshift-usdc.py"
    spec = importlib.util.spec_from_file_location("check_upshift_usdc", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    usdc = USDC_NATIVE_TOKEN[1]
    token = SimpleNamespace(symbol="USDC", address=usdc, address_lower=usdc.lower(), fetch_balance_of=lambda address: Decimal(100))
    vault = MagicMock(spec=UpshiftVault)
    vault.name = "USPC Custody Vault"
    vault.denomination_token = token
    vault.share_token = token
    vault.fetch_all_denomination_tokens.return_value = [token]
    vault.upshift_contract.functions.lagDuration.return_value.call.return_value = 3 * 86_400
    vault.is_whitelisted_deposit.return_value = True
    vault.is_account_whitelisted.return_value = True
    vault.fetch_deposit_closed_reason.return_value = None
    vault.fetch_redemption_closed_reason.return_value = None
    monkeypatch.setattr(module, "read_json_rpc_url", lambda chain: "unused")
    monkeypatch.setattr(module, "create_multi_provider_web3", lambda url: SimpleNamespace(eth=SimpleNamespace(block_number=123)))
    monkeypatch.setattr(module, "create_vault_instance_autodetect", lambda web3, address: vault)

    result = module.fetch_vault_result(VaultSpec(1, "0x0a152c5b8d48c26619ff3ae85ef1b54a79e20870"), Decimal(10), owner)

    assert result["status"] == "permissioned_excluded"
    assert result["observed_head_block"] == 123
    assert result["owner_admitted"] is (True if owner else None)
    vault.get_deposit_manager.return_value.create_deposit_request.assert_not_called()
