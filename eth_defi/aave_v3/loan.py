"""Aave v3 loan"""

from eth_typing import HexAddress
from web3.contract.contract import Contract, ContractFunction

from eth_defi.aave_v3.constants import AaveV3InterestRateMode


def supply(
    aave_v3_deployment,
    *,
    token: Contract,
    amount: int,
    wallet_address: HexAddress,
) -> tuple[ContractFunction, ContractFunction]:
    """
    Opens a loan position in Aave v3 by depositing any Aave v3 reserve token and receiving aToken back.

    Example:

    .. code-block:: python

        # build transactions to supply USDC to Aave v3
        approve_fn, supply_fn = supply(
            aave_v3_deployment=aave_v3_deployment,
            token=usdc.contract,
            amount=amount,
            wallet_address=hot_wallet.address,
        )

        # approve
        tx = approve_fn.build_transaction({"from": hot_wallet.address})
        signed = hot_wallet.sign_transaction_with_new_nonce(tx)
        raw_bytes = get_tx_broadcast_data(signed_tx)
        tx_hash = web3.eth.send_raw_transaction(raw_bytes)
        assert_transaction_success_with_explanation(web3, tx_hash)

        # supply
        tx = supply_fn.build_transaction({"from": hot_wallet.address})
        signed = hot_wallet_account.sign_transaction_with_new_nonce(tx)
        raw_bytes = get_tx_broadcast_data(signed_tx)
        tx_hash = web3.eth.send_raw_transaction(raw_bytes)
        assert_transaction_success_with_explanation(web3, tx_hash)

    :param aave_v3_deployment:
        Instance of :py:class:`eth_defi.aave_v3.deployment.AaveV3Deployment`.
    :param token:
        Aave v3 reserve token you want to supply.
    :param amount:
        The amount of token to supply.
    :param wallet_address:
        Your wallet address.
    :return:
        A tuple of 2 contract functions for approve and supply transaction.
    """

    pool = aave_v3_deployment.pool

    # TODO: maybe validate if Aave v3 actually supports this asset reserve

    # approve to supply
    approve_function = token.functions.approve(pool.address, amount)

    # https://github.com/aave/aave-v3-core/blob/e0bfed13240adeb7f05cb6cbe5e7ce78657f0621/contracts/protocol/pool/Pool.sol#L145
    # address asset
    # uint256 amount
    # address onBehalfOf
    # uint16 referralCode
    supply_function = pool.functions.supply(token.address, amount, wallet_address, 0)

    return approve_function, supply_function


def withdraw(
    aave_v3_deployment,
    *,
    token: Contract,
    amount: int,
    wallet_address: HexAddress,
) -> ContractFunction:
    """
    Withdraw the deposit from Aave v3.

    Example:

    .. code-block:: python

        # build transactions to withdraw all USDC you deposited from Aave v3
        withdraw_fn = withdraw(
            aave_v3_deployment=aave_v3_deployment,
            token=usdc.contract,
            amount=MAX_AMOUNT,
            wallet_address=hot_wallet.address,
        )

        tx = withdraw_fn.build_transaction({"from": hot_wallet.address})
        signed = hot_wallet_account.sign_transaction_with_new_nonce(tx)
        raw_bytes = get_tx_broadcast_data(signed_tx)
        tx_hash = web3.eth.send_raw_transaction(raw_bytes)
        assert_transaction_success_with_explanation(web3, tx_hash)

    :param aave_v3_deployment:
        Instance of :py:class:`eth_defi.aave_v3.deployment.AaveV3Deployment`.
    :param token:
        Aave v3 reserve token you want to withdraw.
    :param amount:
        The amount of token to withdraw. Set `MAX_AMOUNT` if you want to withdraw everything.
    :param wallet_address:
        Your wallet address.
    :return:
        Withdraw contract function.
    """
    pool = aave_v3_deployment.pool

    # https://github.com/aave/aave-v3-core/blob/e0bfed13240adeb7f05cb6cbe5e7ce78657f0621/contracts/protocol/pool/Pool.sol#L198
    # address asset
    # uint256 amount
    # address to
    return pool.functions.withdraw(token.address, amount, wallet_address)


def borrow(
    aave_v3_deployment,
    *,
    token: Contract,
    amount: int,
    wallet_address: HexAddress,
    interest_rate_mode: AaveV3InterestRateMode = AaveV3InterestRateMode.VARIABLE,
) -> ContractFunction:
    """
    Borrow asset from Aave v3. Requires you have already supplied asset as
    collateral in advanced.

    Example:

    .. code-block:: python

        # build transactions to borrow WETH from Aave v3
        borrow_fn = borrow(
            aave_v3_deployment=aave_v3_deployment,
            token=weth.contract,
            amount=amount,
            wallet_address=hot_wallet.address,
        )

        tx = borrow_fn.build_transaction({"from": hot_wallet.address})
        signed = hot_wallet_account.sign_transaction_with_new_nonce(tx)
        raw_bytes = get_tx_broadcast_data(signed_tx)
        tx_hash = web3.eth.send_raw_transaction(raw_bytes)
        assert_transaction_success_with_explanation(web3, tx_hash)

    :param aave_v3_deployment:
        Instance of :py:class:`eth_defi.aave_v3.deployment.AaveV3Deployment`.
    :param token:
        Aave v3 reserve token you want to borrow.
    :param amount:
        The amount of token to borrow.
    :param wallet_address:
        Your wallet address.
    :param interest_rate_mode:
        Stable or variable borrow rate mode, default to variable borrow rate.
    :return:
        Borrow contract function.
    """
    pool = aave_v3_deployment.pool

    # https://github.com/aave/aave-v3-core/blob/e0bfed13240adeb7f05cb6cbe5e7ce78657f0621/contracts/protocol/pool/Pool.sol#L221
    # address asset,
    # uint256 amount,
    # uint256 interestRateMode,
    # uint16 referralCode,
    # address onBehalfOf
    return pool.functions.borrow(token.address, amount, interest_rate_mode, 0, wallet_address)


def repay(
    aave_v3_deployment,
    *,
    token: Contract,
    amount: int,
    wallet_address: HexAddress,
    interest_rate_mode: AaveV3InterestRateMode = AaveV3InterestRateMode.VARIABLE,
) -> tuple[ContractFunction, ContractFunction]:
    """
    Pay back the asset you owned in Aave v3.

    Example:

    .. code-block:: python

        # build transactions to pay back USDC to Aave v3
        approve_fn, repay_fn = repay(
            aave_v3_deployment=aave_v3_deployment,
            token=usdc.contract,
            amount=amount,
            wallet_address=hot_wallet.address,
        )

        # approve
        tx = approve_fn.build_transaction({"from": hot_wallet.address})
        signed = hot_wallet.sign_transaction_with_new_nonce(tx)
        raw_bytes = get_tx_broadcast_data(signed_tx)
        tx_hash = web3.eth.send_raw_transaction(raw_bytes)
        assert_transaction_success_with_explanation(web3, tx_hash)

        # repay
        tx = repay_fn.build_transaction({"from": hot_wallet.address})
        signed = hot_wallet_account.sign_transaction_with_new_nonce(tx)
        raw_bytes = get_tx_broadcast_data(signed_tx)
        tx_hash = web3.eth.send_raw_transaction(raw_bytes)
        assert_transaction_success_with_explanation(web3, tx_hash)

    :param aave_v3_deployment:
        Instance of :py:class:`eth_defi.aave_v3.deployment.AaveV3Deployment`.
    :param token:
        Aave v3 reserve token you want to pay back.
    :param amount:
        The amount of token to pay back. Set to `MAX_AMOUNT` to fully repay your loan.
    :param wallet_address:
        Your wallet address.
    :param interest_rate_mode:
        Stable or variable borrow rate mode, default to variable borrow rate.
    :return:
        A tuple of 2 contract functions for approve and repay transaction.
    """
    pool = aave_v3_deployment.pool

    # approve repay amount
    approve_function = token.functions.approve(pool.address, amount)

    # https://github.com/aave/aave-v3-core/blob/e0bfed13240adeb7f05cb6cbe5e7ce78657f0621/contracts/protocol/pool/Pool.sol#L251
    # address asset,
    # uint256 amount,
    # uint256 interestRateMode,
    # address onBehalfOf
    repay_function = pool.functions.repay(token.address, amount, interest_rate_mode, wallet_address)

    return approve_function, repay_function
