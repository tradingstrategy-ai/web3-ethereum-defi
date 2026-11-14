"""Lagoon unit test helpers."""

import logging

from eth_typing import HexAddress
from hexbytes import HexBytes
from web3 import Web3
from web3.exceptions import ContractLogicError

from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.lagoon.funding import fund_lagoon_vault  # noqa: F401
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault, LagoonVersion
from eth_defi.provider.anvil import is_anvil
from eth_defi.provider.fallback import ExtraValueError
from eth_defi.event_reader.conversion import convert_uin256_to_bytes
from eth_defi.event_reader.multicall_batcher import EncodedCall
from eth_defi.hotwallet import HotWallet
from eth_defi.token import TokenDiskCache
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.vault.deposit_redeem import UnsupportedVaultSimulation, extract_revert_data

logger = logging.getLogger(__name__)


def redeem_vault_shares(
    web3: Web3,
    vault_address: HexAddress,
    redeemer: HexAddress,
    hot_wallet: HotWallet | None = None,
    token_cache: TokenDiskCache | None = None,
) -> LagoonVault:
    """Request a full redemption of vault shares for a given depositor.

    Initiates Phase 1 of the ERC-7540 async redemption flow:

    1. Approve all shares for the vault
    2. Call ``requestRedeem()`` to queue the redemption

    After calling this function, the vault must be *settled* to process
    the redemption (Phase 2), then the redeemer calls
    ``vault.finalise_redeem()`` to claim their USDC (Phase 3).

    Supports two transaction signing modes:

    - **Anvil mode** (default): uses ``.transact({"from": ...})`` for
      unlocked accounts on Anvil forks.
    - **HotWallet mode**: when *hot_wallet* is provided, signs and
      broadcasts each transaction via
      :py:meth:`HotWallet.transact_and_broadcast_with_contract`.

    Example (HotWallet mode)::

        deployer.sync_nonce(web3)
        vault = redeem_vault_shares(
            web3,
            vault_address,
            redeemer=deployer.address,
            hot_wallet=deployer,
        )
        # Then settle the vault (e.g. via CLI lagoon-settle)
        # Then finalise:
        tx_hash = deployer.transact_and_broadcast_with_contract(
            vault.finalise_redeem(deployer.address),
        )

    :param web3:
        Web3 connection to the chain where the vault lives.

    :param vault_address:
        On-chain address of the Lagoon vault.

    :param redeemer:
        Address that holds vault shares and wants to redeem them.

    :param hot_wallet:
        When provided, all transactions are signed with this wallet
        instead of using Anvil's unlocked-account shortcut.

    :return:
        The :class:`LagoonVault` instance, which can be used for
        Phase 3 (``vault.finalise_redeem()``).
    """
    assert vault_address.startswith("0x"), f"Vault address should be an address, got: {vault_address}"
    assert redeemer.startswith("0x"), f"redeemer should be an address, got: {redeemer}"

    vault = create_vault_instance(
        web3,
        vault_address,
        features={ERC4626Feature.lagoon_like},
        default_block_identifier="latest",
        require_denomination_token=True,
        token_cache=token_cache,
    )
    assert isinstance(vault, LagoonVault), f"Vault is not a Lagoon vault: {vault}"

    share_token = vault.share_token
    raw_shares = share_token.fetch_raw_balance_of(redeemer)
    human_shares = share_token.convert_to_decimals(raw_shares)
    assert raw_shares > 0, f"Redeemer {redeemer} has no vault shares to redeem"

    logger.info(
        "Requesting full redemption: %s %s shares for %s",
        human_shares,
        share_token.symbol,
        redeemer,
    )

    def _send(bound_func, description: str, gas: int = 1_000_000):
        if hot_wallet is not None:
            logger.info("Broadcasting (HotWallet): %s", description)
            tx_hash = hot_wallet.transact_and_broadcast_with_contract(bound_func, gas_limit=gas)
        else:
            tx_hash = bound_func.transact({"from": redeemer, "gas": gas})
        assert_transaction_success_with_explanation(web3, tx_hash)

    # 1. Approve shares for the vault
    _send(
        share_token.approve(vault.address, human_shares),
        f"Approve {human_shares} shares for redemption",
    )

    # 2. Queue the redemption
    _send(
        vault.request_redeem(redeemer, raw_shares),
        f"Request redemption of {human_shares} shares",
    )

    logger.info("Redemption requested for %s %s shares", human_shares, share_token.symbol)

    return vault


def force_lagoon_settle(
    vault: LagoonVault,
    asset_manager: HexAddress,
    settlement_manager: HexAddress | None = None,
    raw_nav: int | None = None,
    gas_limit: int = 15_000_000,
) -> tuple[HexBytes, ...]:
    """Advance a Lagoon queue on Anvil using its deployed settlement interface.

    Propose the recorded NAV and settle as the configured Safe. Vaults without
    ``version()`` can have either settlement selector after a beacon upgrade;
    probe their call without persisting state before sending one transaction.
    Versioned vaults retain their explicit ABI and valid-NAV expiry handling.

    :param vault:
        Protocol adapter connected to a disposable Anvil fork.
    :param asset_manager:
        Valuation-manager account, spoofed in Anvil.
    :param settlement_manager:
        Safe account that submits the settlement. Defaults to
        ``asset_manager`` for legacy test callers where both roles coincide.
    :param raw_nav:
        Proposed assets in raw denomination units; defaults to the current NAV.
    :param gas_limit:
        Gas allowance for each privileged fork transaction.
    :return:
        Mined expiry (when needed), valuation and settlement transaction hashes.
    :raises UnsupportedVaultSimulation:
        If the valuation guard rejects the proposed NAV. The helper does not
        invent another valuation to make settlement pass.
    """

    assert asset_manager.startswith("0x"), f"asset_manager should be an address, got: {asset_manager}"
    if settlement_manager is None:
        settlement_manager = asset_manager
    assert settlement_manager.startswith("0x"), f"settlement_manager should be an address, got: {settlement_manager}"

    web3 = vault.web3
    assert is_anvil(web3), "Forced Lagoon settlement requires Anvil"
    for account in (asset_manager, settlement_manager):
        balance = web3.eth.get_balance(account)
        if balance < 10**18:
            tx_hash = web3.eth.send_transaction({"to": account, "from": web3.eth.accounts[0], "value": 5 * 10**18})
            assert_transaction_success_with_explanation(web3, tx_hash)

    if raw_nav is None:
        nav = vault.fetch_nav()
        raw_nav = vault.denomination_token.convert_to_raw(nav)

    expiry_hashes = ()
    if vault.version in (LagoonVersion.v_0_5_0, LagoonVersion.v_0_5_2, LagoonVersion.v_0_6_0) and vault.vault_contract.functions.isTotalAssetsValid().call():
        expiry_hash = vault.vault_contract.functions.expireTotalAssets().transact({"from": settlement_manager, "gas": gas_limit})
        assert_transaction_success_with_explanation(web3, expiry_hash)
        expiry_hashes = (expiry_hash,)
    valuation = vault.vault_contract.functions.updateNewTotalAssets(raw_nav)
    try:
        valuation.call({"from": asset_manager})
    except (ContractLogicError, ExtraValueError) as error:
        data = extract_revert_data(error)
        if data and data[:4] == bytes.fromhex("21aa421f"):
            raise UnsupportedVaultSimulation(
                f"Lagoon valuation guard rejects proposed raw NAV {raw_nav}; settlement requires a justified valuation from the operator",
                unsupported_reason="lagoon_valuation_guardrails",
                protocol="Lagoon",
                vault_address=vault.address,
            ) from error
        raise
    valuation_tx_hash = valuation.transact({"from": asset_manager, "gas": gas_limit})
    assert_transaction_success_with_explanation(web3, valuation_tx_hash)

    legacy = vault.version is LagoonVersion.legacy
    signature = "settleDeposit()" if legacy else "settleDeposit(uint256)"
    call = EncodedCall.from_keccak_signature(
        address=vault.address,
        function=signature,
        signature=Web3.keccak(text=signature)[0:4],
        data=b"" if legacy else convert_uin256_to_bytes(raw_nav),
        extra_data=None,
    )
    tx_data = call.transact(
        from_=settlement_manager,
        gas_limit=gas_limit,
    )
    if legacy:
        # A missing version() does not identify the settlement ABI: upgraded
        # beacon vaults can require the NAV argument. Probe without persisting
        # state; no failed settlement transaction is broadcast on the fork.
        try:
            web3.eth.call(tx_data)
        except (ContractLogicError, ExtraValueError):
            call = EncodedCall.from_keccak_signature(
                address=vault.address,
                function="settleDeposit(uint256)",
                signature=Web3.keccak(text="settleDeposit(uint256)")[0:4],
                data=convert_uin256_to_bytes(raw_nav),
                extra_data=None,
            )
            tx_data = call.transact(from_=settlement_manager, gas_limit=gas_limit)
            web3.eth.call(tx_data)
    settlement_tx_hash = web3.eth.send_transaction(tx_data)
    assert_transaction_success_with_explanation(web3, settlement_tx_hash)
    return (*expiry_hashes, HexBytes(valuation_tx_hash), HexBytes(settlement_tx_hash))
