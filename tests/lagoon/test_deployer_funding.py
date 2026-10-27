"""Manual multichain deployment funding must be checked before spending gas."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from eth_defi.erc_4626.vault_protocol.lagoon import funding


@pytest.mark.parametrize("chain", ["arbitrum", "ethereum", "hyperliquid"])
@pytest.mark.parametrize("asset", ["usdc", "native"])
def test_missing_chain_funding(monkeypatch: pytest.MonkeyPatch, chain: str, asset: str) -> None:
    """Every selected chain needs native gas and its own USDC requirement."""
    chains = {name: Mock() for name in ("arbitrum", "ethereum", "hyperliquid")}
    tokens = {}
    for name, chain_id in zip(chains, (42161, 1, 999), strict=True):
        chains[name].eth.chain_id = chain_id
        chains[name].eth.get_balance.return_value = 1
        tokens[chain_id] = SimpleNamespace(fetch_balance_of=Mock(return_value=Decimal(25) if name == "arbitrum" else Decimal(1)))
    monkeypatch.setattr(funding, "fetch_erc20_details", lambda web3, _address: tokens[web3.eth.chain_id])
    funding.check_multichain_deployer_funding(chains, "0xowner", "arbitrum", Decimal(25))
    if asset == "native":
        chains[chain].eth.get_balance.return_value = 0
    else:
        tokens[chains[chain].eth.chain_id].fetch_balance_of.return_value -= Decimal("0.000001")
    with pytest.raises(ValueError, match=chain):
        funding.check_multichain_deployer_funding(chains, "0xowner", "arbitrum", Decimal(25))
