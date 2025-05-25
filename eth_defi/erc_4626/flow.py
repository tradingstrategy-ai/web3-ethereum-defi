"""Deposit and redemption from ERC-4626 vaults."""
import logging
from decimal import Decimal

from eth_typing import HexAddress
from web3.contract.contract import ContractFunction

from eth_defi.erc_4626.vault import ERC4626Vault


logger = logging.getLogger(__name__)



def deposit_4626(
    vault: ERC4626Vault,
    from_: HexAddress,
    amount: Decimal,
    check_max_deposit=True,
    check_enough_token=True,
    receiver=None,
) -> ContractFunction:
    """Craft a transaction for ERC-4626 vault deposit.

    - The resulting payload must be signed by a wallet/vault

    - The resulting transaction can be analysed with :py:func:`eth_defi.erc_4626.analysis.analyse_4626_flow_transaction`

    Example:

    .. code-block:: python

        amount = Decimal(100)

        tx_hash = base_usdc.approve(
            vault.address,
            amount,
        ).transact({"from": depositor})
        assert_transaction_success_with_explanation(web3, tx_hash)

        bound_func = deposit_4626(
            vault,
            depositor,
            amount,
        )
        tx_hash = bound_func.transact({"from": depositor})
        assert_transaction_success_with_explanation(web3, tx_hash)
        tx_receipt = web3.eth.get_transaction_receipt(tx_hash)

        # Analyse the ERC-4626 deposit transaction
        analysis = analyse_4626_flow_transaction(
            vault=vault,
            tx_hash=tx_hash,
            tx_receipt=tx_receipt,
            direction="deposit",
        )
        assert analysis.path == [base_usdc.address_lower, vault.share_token.address_lower]
        assert analysis.price == pytest.approx(Decimal("1.033566972663402121955991264"))

    Another example how to use this with Lagoon, where from (TradingStrategyModuleV0) and receiver (Safe multisig)
    are different contracts:

    .. code-block:: python

        fn_calls = approve_and_deposit_4626(
            vault=erc4626_vault,  # IPOR vault we trade
            amount=usdc_amount,
            from_=vault.address,  # Our Lagoon vault
            check_enough_token=False,
            receiver=vault.safe_address,  # Safe multisig address of our Lagoon vault
        )

    :param check_enough_token:
        Assume from address holds the token and do live check.

        Must be disabled e.g. for Lagoon as TradingStrategyModuleV0  calls are performed from a d different address than the vault address.

    """

    assert isinstance(vault, ERC4626Vault)
    assert isinstance(amount, Decimal)
    assert from_.startswith("0x")
    assert amount > 0

    if receiver is None:
        receiver = from_

    logger.info(
        "Depositing to vault %s, amount %s, from %s",
        vault.address,
        amount,
        from_,
    )

    contract = vault.vault_contract

    raw_amount = vault.denomination_token.convert_to_raw(amount)

    if check_enough_token:
        actual_balance = vault.denomination_token.fetch_raw_balance_of(from_)
        assert actual_balance >= raw_amount, f"Not enough token in {from_} to deposit {amount} to {vault.address}, has {actual_balance}"

    if check_max_deposit:
        max_deposit = contract.functions.maxDeposit(receiver).call()
        if max_deposit != 0:
            assert raw_amount <= max_deposit, f"Max deposit {max_deposit} is less than {raw_amount}"

    call = contract.functions.deposit(raw_amount, receiver)
    return call


def redeem_4626(
    vault: ERC4626Vault,
    owner: HexAddress,
    amount: Decimal,
    check_enough_token=True,
    check_max_redeem=True,
    receiver=None,
) -> ContractFunction:
    """Craft a transaction for ERC-4626 vault deposit.

    - The resulting payload must be signed by a wallet/vault

    - The resulting transaction can be analysed with :py:func:`eth_defi.erc_4626.analysis.analyse_4626_flow_transaction`

    Example:

    .. code-block:: python

        shares = vault.share_token.fetch_balance_of(depositor, "latest")
        assert shares == pytest.approx(Decimal('96.7523176'))

        # See how much we get after all this time
        estimated_usdc = estimate_4626_redeem(
            vault,
            depositor,
            shares,
        )
        assert estimated_usdc == pytest.approx(Decimal("99.084206"))

        tx_hash = vault.share_token.approve(vault.address, shares).transact({"from": depositor})
        assert_transaction_success_with_explanation(web3, tx_hash)

        tx_hash = redeem_4626(vault, depositor, shares).transact({"from": depositor})
        assert_transaction_success_with_explanation(web3, tx_hash)

        # Analyse the ERC-4626 deposit transaction
        analysis = analyse_4626_flow_transaction(
            vault=vault,
            tx_hash=tx_hash,
            tx_receipt=tx_receipt,
            direction="redeem",
        )
        assert isinstance(analysis, TradeSuccess)

        assert analysis.path == [vault.share_token.address_lower, base_usdc.address_lower]
        assert analysis.amount_in == pytest.approx(9675231765)
        assert analysis.amount_out == pytest.approx(100000000)
        assert analysis.amount_in_decimals == 8  # IPOR has 8 decimals
        assert analysis.price == pytest.approx(Decimal("1.033566972663402121955991264"))

    :param vault:
        ERC-4626 vault from where we redeem.

    :param amount:
        Share token mount in human readable form.

    :param owner:
        The hot wallet/vault storage contract which will receive the tokens.

        Matters in complex vault setups. Like in the case of Lagoon vault,
        the receiver is the Safe multisig address of the vault.

    """

    assert isinstance(vault, ERC4626Vault)
    assert isinstance(amount, Decimal)
    assert owner.startswith("0x")
    assert amount > 0

    if receiver is None:
        receiver = owner

    logger.info(
        "Redeeming from vault %s, amount %s, from %s",
        vault.address,
        amount,
        owner,
    )

    contract = vault.vault_contract

    raw_amount = vault.share_token.convert_to_raw(amount)

    if check_enough_token:
        actual_balance = vault.share_token.fetch_raw_balance_of(owner)
        assert actual_balance >= raw_amount, f"Not enough token in {owner} to deposit {amount} to {vault.address}, has {actual_balance}"

    if check_max_redeem:
        max_redeem = contract.functions.maxRedeem(receiver).call()
        if max_redeem != 0:
            assert raw_amount <= max_redeem, f"Max redeem {max_redeem} is less than {raw_amount}"

    call = contract.functions.redeem(raw_amount, owner, receiver)
    return call


def approve_and_deposit_4626(
    vault: ERC4626Vault,
    from_: HexAddress,
    amount: Decimal,
    check_max_deposit=True,
    check_enough_token=True,
    receiver=None,
) -> tuple[ContractFunction, ContractFunction]:
    """two ERC-20 calls needed to deposit.

    For documentation see :py:func:`deposit_4626`.
    """
    approve_call = vault.denomination_token.approve(vault.address, amount)
    deposit_call = deposit_4626(
        vault,
        from_,
        amount,
        check_max_deposit=check_max_deposit,
        check_enough_token=check_enough_token,
        receiver=receiver,
    )
    return approve_call, deposit_call


def approve_and_redeem_4626(
    vault: ERC4626Vault,
    from_: HexAddress,
    amount: Decimal,
    check_enough_token=True,
    check_max_redeem=True,
    receiver=None,
) -> tuple[ContractFunction, ContractFunction]:
    """two ERC-20 calls needed to deposit.

    For documentation see :py:func:`redeem_4626`.
    """
    approve_call = vault.denomination_token.approve(vault.address, amount)
    redeem_call = redeem_4626(
        vault,
        from_,
        amount,
        check_enough_token=check_enough_token,
        check_max_redeem=check_max_redeem,
        receiver=receiver,
    )
    return approve_call, redeem_call

