import json
from decimal import Decimal

from web3 import Web3

import eth_defi.erc_4626.vault_protocol.lagoon.deployment as lagoon_deployment
from eth_defi.erc_4626.vault_protocol.lagoon.deployment import (
    LagoonAutomatedDeployment,
    LagoonDeploymentParameters,
    WhitelistEntry,
)

CHAIN_ID = 1
VAULT = Web3.to_checksum_address("0x0000000000000000000000000000000000000011")
SAFE = Web3.to_checksum_address("0x0000000000000000000000000000000000000022")
MODULE = Web3.to_checksum_address("0x0000000000000000000000000000000000000033")
ASSET_MANAGER = Web3.to_checksum_address("0x0000000000000000000000000000000000000044")
UNDERLYING = Web3.to_checksum_address("0x0000000000000000000000000000000000000055")
SHARE = Web3.to_checksum_address("0x0000000000000000000000000000000000000066")


class FakeEth:
    chain_id = CHAIN_ID


class FakeWeb3:
    eth = FakeEth()


class FakeContract:
    def __init__(self, address: str):
        self.address = Web3.to_checksum_address(address)


class FakeToken:
    def __init__(self, address: str, symbol: str):
        self.address = Web3.to_checksum_address(address)
        self.symbol = symbol


class FakeVault:
    def __init__(self):
        self.address = VAULT
        self.underlying_token = FakeToken(UNDERLYING, "USDC")
        self.share_token = FakeToken(SHARE, "LIGHTER-TEST")


class FakeHydratedVault(FakeVault):
    def __init__(self, web3, spec, **kwargs):
        super().__init__()
        self.web3 = web3
        self.spec = spec
        self.trading_strategy_module_address = kwargs["trading_strategy_module_address"]
        self.vault_abi = kwargs["vault_abi"]


def test_lagoon_automated_deployment_json_roundtrip(monkeypatch):
    """Lagoon deployment info is JSON-serialisable and can be hydrated."""
    deploy_info = LagoonAutomatedDeployment(
        chain_id=CHAIN_ID,
        vault=FakeVault(),
        trading_strategy_module=FakeContract(MODULE),
        asset_managers=(ASSET_MANAGER,),
        valuation_manager=ASSET_MANAGER,
        multisig_owners=[ASSET_MANAGER],
        deployer=ASSET_MANAGER,
        block_number=12_345_678,
        parameters=LagoonDeploymentParameters(
            underlying=UNDERLYING,
            name="Lighter Trading Vault Manual Test",
            symbol="LIGHTER-TEST",
        ),
        vault_abi="lagoon/v0.5.0/Vault.json",
        safe_address=SAFE,
        beacon_proxy_factory=None,
        gas_used=Decimal("0.01"),
        whitelisted_items=(WhitelistEntry(kind="Lighter", name="ZkLighter", address=MODULE),),
    )

    data = deploy_info.as_json_friendly_dict()
    decoded = json.loads(json.dumps(data, allow_nan=False))

    monkeypatch.setattr(lagoon_deployment, "get_deployed_contract", lambda _web3, _abi, address: FakeContract(address))
    monkeypatch.setattr(lagoon_deployment, "LagoonVault", FakeHydratedVault)

    hydrated = LagoonAutomatedDeployment.from_json_friendly_dict(FakeWeb3(), decoded)

    assert hydrated.chain_id == CHAIN_ID
    assert hydrated.vault.address == VAULT
    assert hydrated.safe_address == SAFE
    assert hydrated.trading_strategy_module.address == MODULE
    assert hydrated.parameters.underlying == UNDERLYING
    assert hydrated.whitelisted_items[0].address == MODULE
