"""Exercise standard ERC-4626 protocol managers through GuardV0.

Each case uses a fixed Anvil block recorded by the deposit-status compatibility
probe. These are deliberately separate from the canonical midnight block: the
selected deployment accepted an immediate full deposit-and-redeem lifecycle at
the listed historical state. The compatibility probe used governance as the
asset manager, so this suite independently proves the GuardV0 policy with a
distinct asset-manager wallet and adversarial address substitutions.
"""

import os
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal

import pytest
from eth_account import Account
from eth_typing import HexAddress
from hexbytes import HexBytes
from web3 import Web3
from web3.contract import Contract
from web3.contract.contract import ContractFunction

from eth_defi.abi import get_deployed_contract
from eth_defi.deploy import GUARD_LIBRARIES, deploy_contract
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.erc_4626.vault_protocol.autopool.vault import AutoPoolVault
from eth_defi.erc_4626.vault_protocol.d2.vault import D2Vault
from eth_defi.erc_4626.vault_protocol.dolomite.vault import DolomiteVault
from eth_defi.erc_4626.vault_protocol.euler.vault import EulerEarnVault, EulerVault
from eth_defi.erc_4626.vault_protocol.fluid.vault import FluidVault
from eth_defi.erc_4626.vault_protocol.gearbox.vault import GearboxVault
from eth_defi.erc_4626.vault_protocol.ipor.vault import IPORVault
from eth_defi.erc_4626.vault_protocol.kiln.vault import KilnVault
from eth_defi.erc_4626.vault_protocol.plutus.vault import PlutusVault
from eth_defi.erc_4626.vault_protocol.royco.vault import RoycoVault
from eth_defi.erc_4626.vault_protocol.silo.vault import SiloVault
from eth_defi.erc_4626.vault_protocol.superform.vault import SuperformVault
from eth_defi.erc_4626.vault_protocol.yearn.vault import YearnV3Vault
from eth_defi.erc_4626.vault_protocol.yo.vault import YoVault
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.anvil import AnvilLaunch, fund_erc20_on_anvil, set_balance
from eth_defi.simple_vault.transact import encode_simple_vault_transaction
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.evm_snapshot_fixture import evm_snapshot_revert
from eth_defi.trace import TransactionAssertionError, assert_transaction_success_with_explanation
from eth_defi.vault.deposit_redeem import WhitelistingRequired

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")


@dataclass(frozen=True, slots=True)
class StandardERC4626Profile:
    """One historical immediate-redemption GuardV0 test case."""

    name: str
    vault_address: HexAddress
    fork_block: int
    vault_type: type[ERC4626Vault]
    expected_remaining_raw_shares: int = 0
    requires_simultaneous_funding_and_redemption: bool = False
    required_remaining_denomination_raw: int = 0


#: Compatibility-probe states from ``vault-deposit-status.json``.
#:
#: These blocks are protocol-specific exceptions to the shared midnight block.
#: They prove a full manager lifecycle for the exact vault, but not GuardV0
#: policy: the probe called the guard as governance. This test uses them only
#: as deterministic liquidity candidates and independently exercises GuardV0.
PROFILES = (
    StandardERC4626Profile("autopool", "0xf63b7f49b4f5dc5d0e7e583cfd79dc64e646320c", 483_533_711, AutoPoolVault),
    # D2 Finance accepts the lifecycle only during its funding phase, before
    # funds are custodied. Its admission rule is strictly greater than one
    # USDC, so retain one raw unit beyond that threshold for redemption too.
    StandardERC4626Profile("d2_finance", "0x75288264fdfea8ce68e6d852696ab1ce2f3e5004", 387_000_000, D2Vault, 0, True, 1_000_001),
    StandardERC4626Profile("dolomite", "0x444868b6e8079ac2c55eea115250f92c2b2c4d14", 483_532_556, DolomiteVault),
    StandardERC4626Profile("euler", "0x05d28a86e057364f6ad1a88944297e58fc6160b3", 483_530_654, EulerVault),
    StandardERC4626Profile("euler_earn", "0xe4783824593a50bfe9dc873204cec171ebc62de0", 483_533_574, EulerEarnVault, 1),
    StandardERC4626Profile("fluid", "0x1a996cb54bb95462040408c06122d45d6cdb6096", 483_530_654, FluidVault),
    StandardERC4626Profile("gearbox", "0x890a69ef363c9c7bdd5e36eb95ceb569f63acbf6", 483_533_025, GearboxVault),
    StandardERC4626Profile("ipor", "0x7fbfd8cda97c0221b39c581c34afd24c523a3990", 483_532_955, IPORVault),
    StandardERC4626Profile("kiln", "0x1c107c4233ab3056254e717c7a67f9917079b615", 483_530_654, KilnVault),
    StandardERC4626Profile("peapods", "0xc2810eb57526df869049fbf4c541791a3255d24c", 483_533_273, ERC4626Vault),
    StandardERC4626Profile("plutus", "0xf2ee51a5e7af0f59e27dda070ce79c3c935a2a67", 483_533_532, PlutusVault),
    StandardERC4626Profile("royco", "0x13c798c93e9c6293dd3c40d1f5c9fdcd4f92aa14", 483_530_654, RoycoVault),
    StandardERC4626Profile("silo", "0x86b1c293e56cbac04d9c15a1af2ef1d2050ff6cd", 483_532_934, SiloVault),
    StandardERC4626Profile("superform", "0x030cdecbdca6a34e8de3f49d1798d5f70e3a3414", 483_530_654, SuperformVault),
    # Yearn V3's manager applies its maxRedeem epsilon correction, leaving a
    # deterministic one-unit share remainder after a guarded full redemption.
    StandardERC4626Profile("yearn_v3", "0x2e7aa06a0f0816de4b1a32a12b0ac4eb584bff2a", 483_531_738, YearnV3Vault, 1),
    StandardERC4626Profile("yo", "0x0000000f2eb9f69274678c76222b35eec7588a65", 483_530_594, YoVault),
)

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ARBITRUM is None, reason="JSON_RPC_ARBITRUM needed to run these tests"),
    # Keep all custom historical Arbitrum forks on one worker.  The pool still
    # reuses a fork whenever two cases select the same fixed block.
    pytest.mark.xdist_group("fork:arbitrum:guard-standard-erc4626"),
]


@pytest.fixture(params=PROFILES, ids=lambda profile: profile.name)
def profile(request: pytest.FixtureRequest) -> StandardERC4626Profile:
    """Select one protocol's known-liquid guarded lifecycle state."""
    return request.param


@pytest.fixture
def anvil_fork(profile: StandardERC4626Profile, anvil_fork_pool: AnvilForkPool) -> AnvilLaunch:
    """Return the shared Anvil launch for the selected immutable state."""
    return anvil_fork_pool.get_launch(JSON_RPC_ARBITRUM, profile.fork_block)


@pytest.fixture
def web3(profile: StandardERC4626Profile, anvil_fork_pool: AnvilForkPool) -> Web3:
    """Connect to the selected pooled Arbitrum fork."""
    return anvil_fork_pool.get_web3(JSON_RPC_ARBITRUM, profile.fork_block)


@pytest.fixture(autouse=True)
def _evm_snapshot(anvil_fork: AnvilLaunch) -> Iterator[None]:
    """Restore the selected pooled fork after every mutating protocol test."""
    yield from evm_snapshot_revert(anvil_fork)


@pytest.fixture
def protocol_vault(web3: Web3, profile: StandardERC4626Profile) -> ERC4626Vault:
    """Open and type-check the protocol-specific ERC-4626 deployment."""
    vault = create_vault_instance_autodetect(web3, profile.vault_address)
    assert isinstance(vault, profile.vault_type)
    return vault


@pytest.fixture
def guarded_simple_vault(web3: Web3, protocol_vault: ERC4626Vault) -> tuple[Contract, Contract, HotWallet]:
    """Deploy a SimpleVaultV0 and allow its standard ERC-4626 call surface."""
    deployer = HotWallet(Account.create())
    asset_manager = HotWallet(Account.create())
    set_balance(web3, deployer.address, Web3.to_wei(10, "ether"))
    set_balance(web3, asset_manager.address, Web3.to_wei(10, "ether"))
    deployer.sync_nonce(web3)
    asset_manager.sync_nonce(web3)
    simple_vault = deploy_contract(web3, "guard/SimpleVaultV0.json", deployer, asset_manager.address, libraries=GUARD_LIBRARIES)
    initialise_hash = _broadcast(web3, deployer, simple_vault.functions.initialiseOwnership(deployer.address))
    assert_transaction_success_with_explanation(web3, initialise_hash)

    guard = get_deployed_contract(web3, "guard/GuardV0.json", simple_vault.functions.guard().call())
    whitelist_hash = _broadcast(web3, deployer, guard.functions.whitelistERC4626(protocol_vault.address, f"Allow {protocol_vault.get_protocol_name()}"))
    assert_transaction_success_with_explanation(web3, whitelist_hash)
    return simple_vault, guard, asset_manager


def _broadcast(web3: Web3, control: HotWallet, func: ContractFunction, gas: int = 2_000_000) -> HexBytes:
    """Broadcast a generously gas-limited transaction from the Anvil control wallet."""
    signed = control.sign_bound_call_with_new_nonce(func, {"gas": gas}, web3=web3, fill_gas_price=True)
    return web3.eth.send_raw_transaction(signed.rawTransaction)


def _perform_guarded_call(
    web3: Web3,
    simple_vault: Contract,
    control: HotWallet,
    func: ContractFunction,
) -> HexBytes:
    """Execute one manager-generated call through SimpleVaultV0 and GuardV0."""
    target, call_data = encode_simple_vault_transaction(func)
    tx_hash = _broadcast(web3, control, simple_vault.functions.performCall(target, call_data))
    assert_transaction_success_with_explanation(web3, tx_hash)
    return tx_hash


def _assert_guarded_call_rejected(
    web3: Web3,
    simple_vault: Contract,
    control: HotWallet,
    func: ContractFunction,
    expected_error: str,
) -> None:
    """Assert that GuardV0 rejects a substituted standard ERC-4626 address."""
    target, call_data = encode_simple_vault_transaction(func)
    tx_hash = _broadcast(web3, control, simple_vault.functions.performCall(target, call_data))
    with pytest.raises(TransactionAssertionError, match=expected_error):
        assert_transaction_success_with_explanation(web3, tx_hash)


def test_guarded_standard_erc4626_deposit_and_redeem(  # noqa: PLR0914
    web3: Web3,
    profile: StandardERC4626Profile,
    protocol_vault: ERC4626Vault,
    guarded_simple_vault: tuple[Contract, Contract, HotWallet],
) -> None:
    """Execute every synchronous manager phase through GuardV0."""
    simple_vault, guard, control = guarded_simple_vault
    manager = protocol_vault.get_deposit_manager()
    assert isinstance(manager, ERC4626DepositManager)

    if profile.requires_simultaneous_funding_and_redemption:
        assert protocol_vault.vault_contract.functions.isFunding().call() is True
        assert protocol_vault.vault_contract.functions.notCustodiedAndNotDuringEpoch().call() is True
        assert protocol_vault.vault_contract.functions.whitelistAsset().call().lower() == protocol_vault.denomination_token.address.lower()

    raw_amount = protocol_vault.denomination_token.convert_to_raw(Decimal(10))

    if profile.requires_simultaneous_funding_and_redemption:
        # D2 allows either an explicit mapping entry or a strictly-greater
        # whitelist-asset balance. Deposit all balance-based admission funds,
        # then prove redemption is preflight-rejected instead of broadcasting a
        # call which its identical ``onlyWhitelisted`` modifier would revert.
        snapshot_id = web3.provider.make_request("evm_snapshot", [])["result"]
        try:
            fund_erc20_on_anvil(web3, protocol_vault.denomination_token.address, simple_vault.address, raw_amount)
            approval = protocol_vault.denomination_token.contract.functions.approve(protocol_vault.address, raw_amount)
            _perform_guarded_call(web3, simple_vault, control, approval)
            deposit_request = manager.create_deposit_request(owner=simple_vault.address, raw_amount=raw_amount)
            _perform_guarded_call(web3, simple_vault, control, deposit_request.funcs[0])
            raw_shares = protocol_vault.share_token.fetch_raw_balance_of(simple_vault.address)
            assert raw_shares > 0
            assert protocol_vault.is_account_whitelisted(simple_vault.address) is False
            with pytest.raises(WhitelistingRequired, match="not whitelisted"):
                manager.create_redemption_request(owner=simple_vault.address, raw_shares=raw_shares)
        finally:
            assert web3.provider.make_request("evm_revert", [snapshot_id])["result"] is True
            # Anvil restores the account nonce but a HotWallet intentionally
            # refuses to move its local counter backwards without this reset.
            control.current_nonce = None
            control.sync_nonce(web3)

    fund_erc20_on_anvil(
        web3,
        protocol_vault.denomination_token.address,
        simple_vault.address,
        raw_amount + profile.required_remaining_denomination_raw,
    )
    assert protocol_vault.denomination_token.fetch_raw_balance_of(simple_vault.address) == raw_amount + profile.required_remaining_denomination_raw

    approval_target = manager.get_deposit_approval_target()
    approval = protocol_vault.denomination_token.contract.functions.approve(approval_target, raw_amount)
    _perform_guarded_call(web3, simple_vault, control, approval)
    assert protocol_vault.denomination_token.contract.functions.allowance(simple_vault.address, approval_target).call() == raw_amount
    assert guard.functions.isAllowedApprovalDestination(approval_target).call() is True

    deposit_request = manager.create_deposit_request(owner=simple_vault.address, raw_amount=raw_amount)
    assert len(deposit_request.funcs) == 1
    deposit_hash = _perform_guarded_call(web3, simple_vault, control, deposit_request.funcs[0])
    deposit_ticket = deposit_request.parse_deposit_transaction([deposit_hash])
    deposit_analysis = manager.analyse_deposit(deposit_hash, deposit_ticket)
    assert deposit_analysis.denomination_amount == Decimal(10)
    assert deposit_analysis.share_count > 0
    assert protocol_vault.denomination_token.fetch_raw_balance_of(simple_vault.address) == profile.required_remaining_denomination_raw

    raw_shares = protocol_vault.share_token.fetch_raw_balance_of(simple_vault.address)
    assert raw_shares > 0
    assert manager.force_settle(None).settlement_required is False

    redemption_request = manager.create_redemption_request(owner=simple_vault.address, raw_shares=raw_shares)
    assert len(redemption_request.funcs) == 1
    redemption_hash = _perform_guarded_call(web3, simple_vault, control, redemption_request.funcs[0])
    redemption_ticket = redemption_request.parse_redeem_transaction([redemption_hash])
    redemption_analysis = manager.analyse_redemption(redemption_hash, redemption_ticket)
    analysed_raw_shares = protocol_vault.share_token.convert_to_raw(redemption_analysis.share_count)
    # A vault may round the emitted ``Withdraw`` share amount down by one raw
    # unit. Euler Earn leaves that unit as share dust in the caller balance.
    assert 0 <= raw_shares - analysed_raw_shares <= 1
    assert redemption_analysis.denomination_amount > 0
    assert protocol_vault.share_token.fetch_raw_balance_of(simple_vault.address) == profile.expected_remaining_raw_shares
    assert protocol_vault.denomination_token.fetch_raw_balance_of(simple_vault.address) == profile.required_remaining_denomination_raw + protocol_vault.denomination_token.convert_to_raw(redemption_analysis.denomination_amount)


def test_guarded_standard_erc4626_rejects_substituted_addresses(
    web3: Web3,
    protocol_vault: ERC4626Vault,
    guarded_simple_vault: tuple[Contract, Contract, HotWallet],
) -> None:
    """Reject unwhitelisted standard ERC-4626 receivers and share owners."""
    simple_vault, _guard, control = guarded_simple_vault
    outsider = HexAddress(web3.eth.accounts[3])

    _assert_guarded_call_rejected(
        web3,
        simple_vault,
        control,
        protocol_vault.vault_contract.functions.deposit(1, outsider),
        "Receiver not whitelisted",
    )
    _assert_guarded_call_rejected(
        web3,
        simple_vault,
        control,
        protocol_vault.vault_contract.functions.redeem(1, outsider, simple_vault.address),
        "Receiver not whitelisted",
    )
    _assert_guarded_call_rejected(
        web3,
        simple_vault,
        control,
        protocol_vault.vault_contract.functions.redeem(1, simple_vault.address, outsider),
        "Owner not whitelisted",
    )
