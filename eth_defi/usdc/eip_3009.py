"""EIP-3009 transferWithAuthorization() support for Python.

- Support `transferWithAuthorization()` and `receiveWithAuthorization()` ERC-20 single-click token transfers

- `EIP-3009 spec <https://github.com/ethereum/EIPs/issues/3010>`__.

- `JavaScript example <https://github.com/ZooWallet/safe-contracts/blob/b2abebd0fdd7f8a846dfc2d59233e41487b659cf/scripts/usdc-biconomy-transferWithAuthorization.js#L87>`__.

- `Canonical examples to construct USDC EIP-712 messages <https://github.com/centrehq/centre-tokens/blob/master/test/v2/GasAbstraction/helpers.ts>`__.

- See `USDC payment forwarder for Enzyme protocol as an example contract <https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/contracts/in-house/src/VaultUSDCPaymentForwarder.sol>`__

- `See how to deploy the payment forwarder contract <https://github.com/tradingstrategy-ai/web3-ethereum-defi/tree/master/contracts/in-house>`__

Internally we use :py:mod:`eth_defi.eip_712` module for managing the messages here.

.. warning::

    Polygon bridged tokens' `transferWithAuthorization()` is not compatible
    with EIP-3009. This includes *USD Coin (PoS)*, or USDC on Polygon.
    See :py:func:`make_eip_3009_transfer` for workarounds.

"""
import datetime
import enum
import secrets
import warnings

from eth_account._utils.signing import to_bytes32
from eth_account.signers.local import LocalAccount
from web3.contract.contract import ContractFunction

from eth_defi.eip_712 import eip712_encode_hash
from eth_defi.token import TokenDetails
from eth_typing import HexAddress


class EIP3009AuthorizationType(enum.Enum):
    """EIP-3009 message types.

    - Note that some contracts e.g. Polygon's bridged USDC
      only implement `TransferWithAuthorization` support.
      Always check your target token first for supported methods.

    - Use `ReceiveWithAuthorization` when possible as it is safer due
      to front running protection

    See :py:func:`make_eip_3009_transfer` for more information.
    """

    #: Used with USDC
    TransferWithAuthorization = "TransferWithAuthorization"

    #: Used with USDC
    #:
    #: Not avaible on Polygon bridged tokens
    #:
    ReceiveWithAuthorization = "ReceiveWithAuthorization"


def construct_eip_3009_authorization_message(
    chain_id: int,
    token: TokenDetails,
    from_,
    to,
    value,
    valid_before=0,
    valid_after=1,
    duration_seconds=0,
    authorization_type=EIP3009AuthorizationType.TransferWithAuthorization,
) -> dict:
    """Create EIP-712 message for EIP-3009 transfers.

    - Used to construct the message that then needs to be signed

    - The signature will be verified by `receiveWithAuthorization()` or `transferWithAuthorization`
      function in the token contract

    .. note ::

      For Polygon USDC `transferWithAuthorization()` you need to use a different message structure,
      because Polygon bridged tokens are not EIP-3009 compatible. This function cannot
      work with Polygon bridged tokens.

    Polygon version is:

    .. code-block:: text

        EIP712Domain: [
          { name: 'name', type: 'string' },
          { name: 'version', type: 'string' },
          { name: 'verifyingContract', type: 'address' },
          { name: 'salt', type: 'bytes32' }
        ]

    More on Polygon incompatibility

    - `Ethers.js disucssion <https://github.com/ethers-io/ethers.js/discussions/2886>`__

    - `Stackexchange discussion <https://ethereum.stackexchange.com/q/141968/620>`__.

    :return:
        JSON message for EIP-712 signing.
    """

    assert duration_seconds or valid_before, "You need to give either duration_seconds or valid_before"

    # Relative to the current time
    if duration_seconds:
        assert not valid_before, "You cannot give valid_before with duration_seconds"
        assert duration_seconds > 0
        valid_before = int(datetime.datetime.utcnow().timestamp() + duration_seconds)

    data = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            authorization_type.value: [
                {"name": "from", "type": "address"},
                {"name": "to", "type": "address"},
                {"name": "value", "type": "uint256"},
                {"name": "validAfter", "type": "uint256"},
                {"name": "validBefore", "type": "uint256"},
                {"name": "nonce", "type": "bytes32"},
            ],
        },
        # domainSeparator = makeDomainSeparator(
        #   "USD Coin",
        #   "2",
        #   1, // hardcoded to 1 because of ganache bug: https://github.com/trufflesuite/ganache/issues/1643
        #   getFiatToken().address
        # );
        "domain": {
            "name": token.name,
            "version": "2",  # TODO: Read from USDC contract?
            "chainId": chain_id,
            "verifyingContract": token.address,
        },
        "primaryType": authorization_type.value,
        "message": {"from": from_, "to": to, "value": value, "validAfter": valid_after, "validBefore": valid_before, "nonce": secrets.token_bytes(32)},  # 256-bit random nonce
    }
    return data


def make_eip_3009_transfer(
    token: TokenDetails,
    from_: LocalAccount,
    to: HexAddress,
    func: ContractFunction,
    value: int,
    valid_before: int = 0,
    valid_after: int = 1,
    duration_seconds: int = 0,
    extra_args=(),
    authorization_type=EIP3009AuthorizationType.TransferWithAuthorization,
) -> ContractFunction:
    """Perform an EIP-3009 transferWithAuthorization() and receiveWithAuthorization() transaction.

    - Constructs the EIP-3009 / EIP-712 payload

    - Signs the message

    - Builds a transaction against `receiveWithAuthorization` in USDC using Web3.py API,
      assuming there is a target smart contract function `func` to be called with this messge

    - The caller can then execute this by building a transaction from the resulting bound
      function call

    .. note ::

        This currently supports only `LocalAccount` because of
        `missing features in web3.py <https://github.com/ethereum/web3.py/issues/2180#issuecomment-943590192>`__.

    Example:

    .. code-block:: python

        # Deploy some contract that supports
        payment_forwarder = deploy_contract(
            web3,
            "VaultUSDCPaymentForwarder.json",
            deployer,
            usdc.address,
            comptroller.address,
        )

        # Construct bounded ContractFunction instance
        # that will transact with MockEIP3009Receiver.deposit()
        # smart contract function.
        bound_func = make_receive_with_authorization_transfer(
            token=usdc,
            from_=user,
            to=payment_forwarder.address,
            func=payment_forwarder.functions.buySharesOnBehalf,
            value=500 * 10**6,  # 500 USD,
            valid_before=valid_before,
            extra_args=(1,),  # minSharesQuantity
        )

        # Sign and broadcast the tx
        tx_hash = bound_func.transact({"from": user.address})

    The `receiveAuthorization()` signature is:

    .. code-block:: text

        function receiveWithAuthorization(
            address from,
            address to,
            uint256 value,
            uint256 validAfter,
            uint256 validBefore,
            bytes32 nonce,
            uint8 v,
            bytes32 r,
            bytes32 s)

    :param token:
        USDC token details

    :param from_:
        The local account that signs the EIP-3009 transfer.

    :param to:
        To which contract USDC is transferred.

    :param func:
        The contract function that is verifying the transfer.

        A smart contract function with the same call signature as receiveAuthorization().
        However, the spec does not specify what kind of a signature of a function this is:
        you can transfer `receiveWithAuthorization()` payload in any form, with extra parameters,
        byte packed, etc.

    :param value:
        How many tokens in raw value

    :param valid_before:
        Transfer timeout control

    :param valid_after:
        Transfer timeout control

    :param duration_seconds:
        Automatically set valid_before based on this

    :param extra_args:
        Arguments added after the standard receiveWithAuthorization() prologue.

    :param authorization_type:
        Is this `transferWithAuthorization` or `receiveWithAuthorization` style transaction.

    :return:
        Bound contract function for transferWithAuthorization

    """

    assert isinstance(token, TokenDetails)
    assert isinstance(func, ContractFunction)
    assert isinstance(from_, LocalAccount)
    assert to.startswith("0x")
    assert value > 0

    web3 = token.contract.w3
    chain_id = web3.eth.chain_id

    data = construct_eip_3009_authorization_message(
        chain_id=chain_id,
        token=token,
        from_=from_.address,
        to=to,
        value=value,
        valid_before=valid_before,
        valid_after=valid_after,
        duration_seconds=duration_seconds,
        authorization_type=authorization_type,
    )

    # The message payload is receiveAuthorization arguments, tightly encoded,
    # without the function selector
    message_hash = eip712_encode_hash(data)

    # TODO: There is no public Web3.py method to sign raw hashes
    # Mute DeprecationWarning
    with warnings.catch_warnings():
        warnings.filterwarnings(action="ignore", category=DeprecationWarning)
        signed_message = from_.signHash(message_hash)

    # Should come in the order defined for the dict,
    # as Python 3.10+ does ordered dicts
    args = list(data["message"].values())  # from, to, value, validAfter, validBefore, nonce
    args += [signed_message.v, to_bytes32(signed_message.r), to_bytes32(signed_message.s)]
    args += list(extra_args)

    return func(*args)
