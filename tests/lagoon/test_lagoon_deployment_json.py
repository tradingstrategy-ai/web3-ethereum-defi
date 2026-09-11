import json
from decimal import Decimal
from inspect import signature
from pathlib import Path
from types import SimpleNamespace

import pytest
from hexbytes import HexBytes
from web3 import Web3

import eth_defi.erc_4626.vault_protocol.lagoon.deployment as lagoon_deployment
from eth_defi.erc_4626.vault_protocol.lagoon.deployment import (
    LagoonAutomatedDeployment,
    LagoonDeploymentParameters,
    LighterAccountSetup,
    WhitelistEntry,
)
from eth_defi.hotwallet import HotWallet
from eth_defi.lighter.api_key import LighterApiKey
from eth_defi.lighter.deployment import LighterDeployment

CHAIN_ID = 1
ACCOUNT_INDEX = 123
API_KEY_INDEX = 4
PRIVATE_FILE_MODE = 0o600
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
    def __init__(self, address: str) -> None:
        self.address = Web3.to_checksum_address(address)


class FakeToken:
    def __init__(self, address: str, symbol: str) -> None:
        self.address = Web3.to_checksum_address(address)
        self.symbol = symbol


class FakeVault:
    def __init__(self) -> None:
        self.address = VAULT
        self.underlying_token = FakeToken(UNDERLYING, "USDC")
        self.share_token = FakeToken(SHARE, "LIGHTER-TEST")


class FakeHydratedVault(FakeVault):
    def __init__(self, web3: FakeWeb3, spec: object, **kwargs: str) -> None:
        super().__init__()
        self.web3 = web3
        self.spec = spec
        self.trading_strategy_module_address = kwargs["trading_strategy_module_address"]
        self.vault_abi = kwargs["vault_abi"]


def create_deploy_info() -> LagoonAutomatedDeployment:
    """Create deployment metadata shared by serialisation tests."""
    return LagoonAutomatedDeployment(
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
        lighter_account_setup=LighterAccountSetup(
            account_index=ACCOUNT_INDEX,
            api_key_index=API_KEY_INDEX,
            private_key="0xprivate-key",
            public_key="0x" + "01" * 40,
            activation_amount=Decimal("1"),
            deposit_tx_hash="0xdeposit",
            change_pubkey_tx_hash="0xchange",
            observed_collateral=Decimal("1"),
        ),
    )


def test_lagoon_automated_deployment_json_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lagoon deployment info is JSON-serialisable and can be hydrated."""
    deploy_info = create_deploy_info()

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
    assert hydrated.lighter_account_setup is not None
    assert hydrated.lighter_account_setup.private_key is None
    assert "0xprivate-key" not in json.dumps(decoded)

    secret_data = deploy_info.as_json_friendly_dict(include_secrets=True)
    secret_hydrated = LagoonAutomatedDeployment.from_json_friendly_dict(FakeWeb3(), secret_data)
    assert secret_hydrated.lighter_account_setup is not None
    assert secret_hydrated.lighter_account_setup.private_key == "0xprivate-key"
    assert "0xprivate-key" not in deploy_info.pformat()


def test_lagoon_deployment_report_is_private_and_exclusive(tmp_path: Path) -> None:
    """Secret deployment reports use mode 0600 and are never overwritten."""
    report_path = tmp_path / "deployment.json"
    deploy_info = create_deploy_info()

    deploy_info.write_json_file(report_path, include_secrets=True)

    assert report_path.stat().st_mode & 0o777 == PRIVATE_FILE_MODE
    assert json.loads(report_path.read_text())["lighter_account_setup"]["private_key"] == "0xprivate-key"
    with pytest.raises(FileExistsError):
        deploy_info.write_json_file(report_path, include_secrets=True)


def test_activate_lighter_account_registers_key_after_minimum_deposit(monkeypatch: pytest.MonkeyPatch) -> None:
    """The deployment ceremony deposits, observes and registers one API key."""
    events: list[object] = []
    balances = iter((Decimal(0), Decimal(0), Decimal(1)))
    token = SimpleNamespace(
        address=UNDERLYING,
        symbol="USDC",
        fetch_balance_of=lambda _address: next(balances),
        convert_to_raw=lambda amount: int(amount * 10**6),
    )
    session = SimpleNamespace(close=lambda: events.append("close"))
    deployer = SimpleNamespace(
        address=ASSET_MANAGER,
        private_key=HexBytes("0x" + "11" * 32),
    )
    safe = SimpleNamespace(address=SAFE)
    web3 = SimpleNamespace(eth=FakeEth())
    generated_key = LighterApiKey(
        api_key_index=API_KEY_INDEX,
        private_key="0x" + "22" * 40,
        public_key="0x" + "33" * 40,
    )
    deployment = LighterDeployment(zk_lighter=MODULE, usdc=UNDERLYING)

    monkeypatch.setattr(lagoon_deployment, "LagoonVault", lambda *_args, **_kwargs: "lagoon-vault")
    monkeypatch.setattr(lagoon_deployment.time, "sleep", lambda seconds: events.append(("sleep", seconds)))
    monkeypatch.setattr(lagoon_deployment, "fetch_erc20_details", lambda *_args, **_kwargs: token)
    monkeypatch.setattr(lagoon_deployment, "fund_lagoon_vault", lambda **kwargs: events.append(("fund", kwargs["amount"])))
    monkeypatch.setattr(
        lagoon_deployment,
        "deposit_usdc_from_lagoon_safe_into_lighter",
        lambda **kwargs: events.append(("deposit", kwargs["deposit_usdc"], kwargs["zk_lighter"])) or "0xdeposit",
    )
    monkeypatch.setattr(lagoon_deployment, "create_lighter_session", lambda: session)
    monkeypatch.setattr(
        lagoon_deployment,
        "wait_for_lighter_account",
        lambda _session, address: events.append(("account", address)) or ACCOUNT_INDEX,
    )
    monkeypatch.setattr(
        lagoon_deployment,
        "wait_for_lighter_collateral",
        lambda _session, account_index, amount: events.append(("collateral", account_index, amount)) or Decimal(1),
    )
    monkeypatch.setattr(lagoon_deployment, "generate_lighter_api_key_pair", lambda index: events.append(("generate", index)) or generated_key)
    monkeypatch.setattr(
        lagoon_deployment,
        "execute_change_pubkey",
        lambda **kwargs: events.append(("register", kwargs["account_index"], kwargs["api_key_index"], kwargs["pubkey"])) or HexBytes("0xdead"),
    )
    monkeypatch.setattr(
        lagoon_deployment,
        "wait_for_lighter_api_key",
        lambda _session, account_index, key_index, public_key, **_kwargs: events.append(("verify", account_index, key_index, public_key)),
    )

    setup = lagoon_deployment._activate_lighter_account(
        web3=web3,
        deployer=deployer,
        safe=safe,
        vault_address=VAULT,
        module_address=MODULE,
        vault_abi="lagoon/v0.5.0/Vault.json",
        lighter_deployment=deployment,
        api_key_index=API_KEY_INDEX,
    )

    assert events == [
        ("fund", Decimal(1)),
        ("sleep", lagoon_deployment.LIGHTER_ACTIVATION_BALANCE_POLL_SECONDS),
        ("deposit", Decimal(1), MODULE),
        ("account", SAFE),
        ("collateral", ACCOUNT_INDEX, Decimal(1)),
        ("generate", API_KEY_INDEX),
        ("register", ACCOUNT_INDEX, API_KEY_INDEX, bytes.fromhex("33" * 40)),
        ("verify", ACCOUNT_INDEX, API_KEY_INDEX, generated_key.public_key),
        "close",
    ]
    assert setup.account_index == ACCOUNT_INDEX
    assert setup.activation_amount == Decimal(1)
    assert setup.deposit_tx_hash == "0xdeposit"
    assert setup.change_pubkey_tx_hash == "0xdead"


def test_lagoon_deployer_exposes_opt_in_lighter_activation_flags() -> None:
    """Lighter activation remains an explicit deployer option."""
    parameters = signature(lagoon_deployment.deploy_automated_lagoon_vault).parameters

    assert parameters["generate_lighter_api_key"].default is False
    assert parameters["lighter_api_key_index"].default == API_KEY_INDEX


def create_valid_lighter_config() -> tuple[dict[str, object], HotWallet]:
    """Create a valid, network-free input set for activation validation."""
    deployer = HotWallet.from_private_key("0x" + "11" * 32)
    canonical_lighter = LighterDeployment.create_ethereum()
    parameters = LagoonDeploymentParameters(
        underlying=canonical_lighter.usdc,
        name="Lighter test",
        symbol="LIGHTER-TEST",
        valuationManager=deployer.address,
    )
    kwargs: dict[str, object] = {
        "web3": SimpleNamespace(eth=SimpleNamespace(chain_id=CHAIN_ID)),
        "deployer": deployer,
        "parameters": parameters,
        "primary_asset_manager": deployer.address,
        "lighter_deployment": canonical_lighter,
        "generate_lighter_api_key": True,
        "lighter_api_key_index": API_KEY_INDEX,
        "guard_only": False,
        "satellite_chain": False,
        "existing_vault_address": None,
        "existing_safe_address": None,
        "max_settlement_amount": None,
    }
    return kwargs, deployer


@pytest.mark.parametrize(
    ("overrides", "error_type", "match"),
    [
        ({"web3": SimpleNamespace(eth=SimpleNamespace(chain_id=2))}, ValueError, "Ethereum chain ID 1"),
        ({"lighter_deployment": None}, ValueError, "requires lighter_deployment"),
        ({"lighter_deployment": LighterDeployment(zk_lighter=MODULE, usdc=LighterDeployment.create_ethereum().usdc)}, ValueError, "canonical Ethereum Lighter"),
        ({"parameters": LagoonDeploymentParameters(underlying=UNDERLYING, name="Wrong asset", symbol="WRONG")}, ValueError, "native Ethereum USDC"),
        ({"guard_only": True}, ValueError, "new full Lagoon vault"),
        ({"satellite_chain": True}, ValueError, "new full Lagoon vault"),
        ({"existing_vault_address": VAULT}, ValueError, "new full Lagoon vault"),
        ({"existing_safe_address": SAFE}, ValueError, "new full Lagoon vault"),
        ({"primary_asset_manager": ASSET_MANAGER}, ValueError, "primary asset manager"),
        ({"lighter_api_key_index": 255}, ValueError, "lighter_api_key_index"),
        ({"max_settlement_amount": Decimal("0.5")}, ValueError, "at least 1"),
    ],
)
def test_validate_lighter_api_key_deployment_rejects_invalid_config(
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, object],
    error_type: type[Exception],
    match: str,
) -> None:
    """Activation validation rejects unsupported topology before network reads."""
    kwargs, _deployer = create_valid_lighter_config()
    kwargs.update(overrides)
    monkeypatch.setattr(lagoon_deployment, "fetch_erc20_details", lambda *_args, **_kwargs: SimpleNamespace(symbol="USDC", fetch_balance_of=lambda _address: Decimal("1")))

    with pytest.raises(error_type, match=match):
        lagoon_deployment._validate_lighter_api_key_deployment_config(**kwargs)


def test_validate_lighter_api_key_deployment_checks_deployer_balance(monkeypatch: pytest.MonkeyPatch) -> None:
    """Activation validation requires the fixed one-USDC activation amount."""
    kwargs, _deployer = create_valid_lighter_config()
    monkeypatch.setattr(lagoon_deployment, "fetch_erc20_details", lambda *_args, **_kwargs: SimpleNamespace(symbol="USDC", fetch_balance_of=lambda _address: Decimal("0.99")))

    with pytest.raises(ValueError, match="at least 1 USDC"):
        lagoon_deployment._validate_lighter_api_key_deployment_config(**kwargs)


def test_validate_lighter_api_key_deployment_accepts_canonical_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Canonical Ethereum USDC deployment configuration passes validation."""
    kwargs, _deployer = create_valid_lighter_config()
    monkeypatch.setattr(lagoon_deployment, "fetch_erc20_details", lambda *_args, **_kwargs: SimpleNamespace(symbol="USDC", fetch_balance_of=lambda _address: Decimal("1")))

    lagoon_deployment._validate_lighter_api_key_deployment_config(**kwargs)


def test_validate_lighter_api_key_deployment_checks_signer_and_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    """Activation requires one deployer to retain all ceremony permissions."""
    kwargs, deployer = create_valid_lighter_config()
    kwargs["parameters"] = LagoonDeploymentParameters(
        underlying=LighterDeployment.create_ethereum().usdc,
        name="Wrong manager",
        symbol="WRONG",
        valuationManager=ASSET_MANAGER,
    )
    monkeypatch.setattr(lagoon_deployment, "fetch_erc20_details", lambda *_args, **_kwargs: SimpleNamespace(symbol="USDC", fetch_balance_of=lambda _address: Decimal("1")))

    with pytest.raises(ValueError, match="valuation manager"):
        lagoon_deployment._validate_lighter_api_key_deployment_config(**kwargs)

    kwargs["parameters"].valuationManager = deployer.address
    kwargs["deployer"] = SimpleNamespace(address=deployer.address)
    with pytest.raises(TypeError, match="HotWallet"):
        lagoon_deployment._validate_lighter_api_key_deployment_config(**kwargs)


def test_validate_lighter_api_key_deployment_flag_is_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    """The disabled activation flag does not perform Lighter network reads."""
    kwargs, _deployer = create_valid_lighter_config()
    kwargs["generate_lighter_api_key"] = False
    monkeypatch.setattr(lagoon_deployment, "fetch_erc20_details", lambda *_args, **_kwargs: pytest.fail("unexpected token read"))

    lagoon_deployment._validate_lighter_api_key_deployment_config(**kwargs)
