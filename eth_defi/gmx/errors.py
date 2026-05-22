"""GMX V2 custom error selector decoder.

GMX V2 contracts revert with Solidity custom errors. The on-chain log
records only the 4-byte function selector — keccak256 of the error
signature, e.g. ``keccak256("InvalidCollateralTokenForMarket(address,address)")[:4]``.

This module maps known selectors back to a human-readable error name +
description so operators see real failure reasons instead of opaque hex
(e.g. ``0x839c693e``).

The selector registry is generated from
`gmx-io/gmx-synthetics/contracts/error/Errors.sol`_ — every
``error Name(...);`` declaration is hashed to produce its 4-byte selector.
Curated human descriptions are attached to the operationally-relevant
errors (collateral, position, order, price, pool, oracle, fee categories).

.. _gmx-io/gmx-synthetics/contracts/error/Errors.sol:
    https://github.com/gmx-io/gmx-synthetics/blob/main/contracts/error/Errors.sol

:see: :mod:`eth_defi.gmx.ccxt.exchange` — uses :func:`decode_gmx_revert_selector`
    when constructing keeper-cancel error messages so logs read e.g.
    ``"InvalidCollateralTokenForMarket — The collateral token is not
    accepted by this market (selector: 0x839c693e)"`` instead of
    ``"Unknown error (selector: 0x839c693e)"``.

Sub-investigation flag (2026-05-22): the live ``0x839c693e`` revert on
``BTC/USDC:USDC`` long with USDC collateral suggests the adapter may be
selecting a BTC ``market_token_address`` whose accepted-collateral set
excludes USDC. This module only improves the diagnostic surface —
investigating and fixing the market-selection logic is tracked as a
follow-up in the multi-sleeve plan
(``docs/superpowers/plans/2026-05-22-orchestrator-multi-sleeve-followups.md``).
"""

from __future__ import annotations

from typing import NamedTuple


class GmxError(NamedTuple):
    """A decoded GMX V2 custom error.

    :ivar selector: 4-byte hex string with ``0x`` prefix, lowercase
        (e.g. ``"0x839c693e"``).
    :ivar name: Solidity error name (e.g. ``"InvalidCollateralTokenForMarket"``).
    :ivar description: Plain-English description of when this error is raised.
        Empty string for errors without a curated description.
    :ivar params: Solidity parameter type signature
        (e.g. ``("address", "address")``).
    """

    selector: str
    name: str
    description: str
    params: tuple[str, ...]



_GMX_ERROR_REGISTRY: dict[str, GmxError] = {
    "0xb244a107": GmxError(
        selector="0xb244a107",
        name="ActionAlreadySignalled",
        description="",
        params=(),
    ),
    "0x94fdaea2": GmxError(
        selector="0x94fdaea2",
        name="ActionNotSignalled",
        description="",
        params=(),
    ),
    "0x3285dc57": GmxError(
        selector="0x3285dc57",
        name="AdlNotEnabled",
        description="",
        params=(),
    ),
    "0xd06ed8be": GmxError(
        selector="0xd06ed8be",
        name="AdlNotRequired",
        description="",
        params=("int256", "uint256"),
    ),
    "0x70657e04": GmxError(
        selector="0x70657e04",
        name="ArrayOutOfBoundsBytes",
        description="",
        params=("bytes[]", "uint256", "string"),
    ),
    "0x9d18e63b": GmxError(
        selector="0x9d18e63b",
        name="ArrayOutOfBoundsUint256",
        description="",
        params=("uint256[]", "uint256", "string"),
    ),
    "0x97a3eeff": GmxError(
        selector="0x97a3eeff",
        name="AttemptedBridgeAmountTooHigh",
        description="",
        params=("uint256", "uint256", "uint256"),
    ),
    "0x60c5e472": GmxError(
        selector="0x60c5e472",
        name="AvailableFeeAmountIsZero",
        description="",
        params=("address", "address", "uint256"),
    ),
    "0x11aeaf6b": GmxError(
        selector="0x11aeaf6b",
        name="BlockNumbersNotSorted",
        description="",
        params=("uint256", "uint256"),
    ),
    "0x4708f070": GmxError(
        selector="0x4708f070",
        name="BridgeOutNotSupportedDuringShift",
        description="",
        params=(),
    ),
    "0xa5123802": GmxError(
        selector="0xa5123802",
        name="BridgedAmountNotSufficient",
        description="",
        params=("uint256", "uint256"),
    ),
    "0xbd3b23af": GmxError(
        selector="0xbd3b23af",
        name="BridgingBalanceArrayMismatch",
        description="",
        params=("uint256", "uint256"),
    ),
    "0x98984b37": GmxError(
        selector="0x98984b37",
        name="BridgingTransactionFailed",
        description="",
        params=("bytes",),
    ),
    "0xec775484": GmxError(
        selector="0xec775484",
        name="BuybackAndFeeTokenAreEqual",
        description="",
        params=("address", "address"),
    ),
    "0xd6b52b60": GmxError(
        selector="0xd6b52b60",
        name="ChainlinkPriceFeedNotUpdated",
        description="",
        params=("address", "uint256", "uint256"),
    ),
    "0xec6d89c8": GmxError(
        selector="0xec6d89c8",
        name="CollateralAlreadyClaimed",
        description="",
        params=("uint256", "uint256"),
    ),
    "0xbdec9c0d": GmxError(
        selector="0xbdec9c0d",
        name="CompactedArrayOutOfBounds",
        description="",
        params=("uint256[]", "uint256", "uint256", "string"),
    ),
    "0x5ebb87c9": GmxError(
        selector="0x5ebb87c9",
        name="ConfigValueExceedsAllowedRange",
        description="",
        params=("bytes32", "uint256"),
    ),
    "0xc92f6438": GmxError(
        selector="0xc92f6438",
        name="DataListLengthExceeded",
        description="",
        params=(),
    ),
    "0x413f9a54": GmxError(
        selector="0x413f9a54",
        name="DataStreamIdAlreadyExistsForToken",
        description="",
        params=("address",),
    ),
    "0x83f2ba20": GmxError(
        selector="0x83f2ba20",
        name="DeadlinePassed",
        description="",
        params=("uint256", "uint256"),
    ),
    "0x43e30ca8": GmxError(
        selector="0x43e30ca8",
        name="DepositNotFound",
        description="",
        params=("bytes32",),
    ),
    "0xdd70e0c9": GmxError(
        selector="0xdd70e0c9",
        name="DisabledFeature",
        description="",
        params=("bytes32",),
    ),
    "0x09f8c937": GmxError(
        selector="0x09f8c937",
        name="DisabledMarket",
        description="The market is disabled (paused or delisted)",
        params=("address",),
    ),
    "0xfd795fc1": GmxError(
        selector="0xfd795fc1",
        name="DuplicateClaimTerms",
        description="",
        params=("uint256",),
    ),
    "0xd4064737": GmxError(
        selector="0xd4064737",
        name="DuplicatedIndex",
        description="",
        params=("uint256", "string"),
    ),
    "0x91c78b78": GmxError(
        selector="0x91c78b78",
        name="DuplicatedMarketInSwapPath",
        description="Same market appears more than once in the swap path",
        params=("address",),
    ),
    "0x3f677c2e": GmxError(
        selector="0x3f677c2e",
        name="EdgeDataStreamIdAlreadyExistsForToken",
        description="",
        params=("address",),
    ),
    "0xdd7016a2": GmxError(
        selector="0xdd7016a2",
        name="EmptyAccount",
        description="Account address is the zero address",
        params=(),
    ),
    "0xe474a425": GmxError(
        selector="0xe474a425",
        name="EmptyAddressInMarketTokenBalanceValidation",
        description="",
        params=("address", "address"),
    ),
    "0x0d143458": GmxError(
        selector="0x0d143458",
        name="EmptyAmount",
        description="",
        params=(),
    ),
    "0x8db88ccf": GmxError(
        selector="0x8db88ccf",
        name="EmptyChainlinkPriceFeed",
        description="",
        params=("address",),
    ),
    "0xb86fffef": GmxError(
        selector="0xb86fffef",
        name="EmptyChainlinkPriceFeedMultiplier",
        description="",
        params=("address",),
    ),
    "0x616daf1f": GmxError(
        selector="0x616daf1f",
        name="EmptyClaimFeesMarket",
        description="",
        params=(),
    ),
    "0x7c8cdbf9": GmxError(
        selector="0x7c8cdbf9",
        name="EmptyClaimableAmount",
        description="",
        params=("address",),
    ),
    "0x62e402cc": GmxError(
        selector="0x62e402cc",
        name="EmptyDataStreamFeedId",
        description="",
        params=("address",),
    ),
    "0x088405c6": GmxError(
        selector="0x088405c6",
        name="EmptyDataStreamMultiplier",
        description="",
        params=("address",),
    ),
    "0x95b66fe9": GmxError(
        selector="0x95b66fe9",
        name="EmptyDeposit",
        description="",
        params=(),
    ),
    "0x01af8c24": GmxError(
        selector="0x01af8c24",
        name="EmptyDepositAmounts",
        description="",
        params=(),
    ),
    "0xd1c3d5bd": GmxError(
        selector="0xd1c3d5bd",
        name="EmptyDepositAmountsAfterSwap",
        description="",
        params=(),
    ),
    "0x9ab5d127": GmxError(
        selector="0x9ab5d127",
        name="EmptyFundingAccount",
        description="",
        params=(),
    ),
    "0xa14e1b3d": GmxError(
        selector="0xa14e1b3d",
        name="EmptyGlv",
        description="",
        params=("address",),
    ),
    "0xbd192971": GmxError(
        selector="0xbd192971",
        name="EmptyGlvDeposit",
        description="",
        params=(),
    ),
    "0x03251ce6": GmxError(
        selector="0x03251ce6",
        name="EmptyGlvDepositAmounts",
        description="",
        params=(),
    ),
    "0x94409f52": GmxError(
        selector="0x94409f52",
        name="EmptyGlvMarketAmount",
        description="",
        params=(),
    ),
    "0x93856b1a": GmxError(
        selector="0x93856b1a",
        name="EmptyGlvTokenSupply",
        description="",
        params=(),
    ),
    "0x0e5be78f": GmxError(
        selector="0x0e5be78f",
        name="EmptyGlvWithdrawal",
        description="",
        params=(),
    ),
    "0x402a866f": GmxError(
        selector="0x402a866f",
        name="EmptyGlvWithdrawalAmount",
        description="",
        params=(),
    ),
    "0xe9b78bd4": GmxError(
        selector="0xe9b78bd4",
        name="EmptyHoldingAddress",
        description="",
        params=(),
    ),
    "0x05fbc1ae": GmxError(
        selector="0x05fbc1ae",
        name="EmptyMarket",
        description="",
        params=(),
    ),
    "0xeb1947dd": GmxError(
        selector="0xeb1947dd",
        name="EmptyMarketPrice",
        description="",
        params=("address",),
    ),
    "0x2ee3d69c": GmxError(
        selector="0x2ee3d69c",
        name="EmptyMarketTokenSupply",
        description="",
        params=(),
    ),
    "0x14c35d93": GmxError(
        selector="0x14c35d93",
        name="EmptyMultichainTransferInAmount",
        description="",
        params=("address", "address"),
    ),
    "0x7a29de11": GmxError(
        selector="0x7a29de11",
        name="EmptyMultichainTransferOutAmount",
        description="",
        params=("address", "address"),
    ),
    "0x16307797": GmxError(
        selector="0x16307797",
        name="EmptyOrder",
        description="The order is empty or has been removed",
        params=(),
    ),
    "0xb2db7048": GmxError(
        selector="0xb2db7048",
        name="EmptyPeer",
        description="",
        params=("uint32",),
    ),
    "0x4dfbbff3": GmxError(
        selector="0x4dfbbff3",
        name="EmptyPosition",
        description="The position does not exist or has zero size",
        params=(),
    ),
    "0x0d1bbc95": GmxError(
        selector="0x0d1bbc95",
        name="EmptyPositionImpactWithdrawalAmount",
        description="",
        params=(),
    ),
    "0xcd64a025": GmxError(
        selector="0xcd64a025",
        name="EmptyPrimaryPrice",
        description="Oracle has no primary price for the token",
        params=("address",),
    ),
    "0xd551823d": GmxError(
        selector="0xd551823d",
        name="EmptyReceiver",
        description="Receiver address is the zero address",
        params=(),
    ),
    "0xb3d35539": GmxError(
        selector="0xb3d35539",
        name="EmptyReduceLentAmount",
        description="",
        params=(),
    ),
    "0x64174bbc": GmxError(
        selector="0x64174bbc",
        name="EmptyRelayFeeAddress",
        description="",
        params=(),
    ),
    "0x6af5e96f": GmxError(
        selector="0x6af5e96f",
        name="EmptyShift",
        description="",
        params=(),
    ),
    "0x60d5e84a": GmxError(
        selector="0x60d5e84a",
        name="EmptyShiftAmount",
        description="",
        params=(),
    ),
    "0x3df42531": GmxError(
        selector="0x3df42531",
        name="EmptySizeDeltaInTokens",
        description="Size delta in tokens is zero",
        params=(),
    ),
    "0x9cdc6daa": GmxError(
        selector="0x9cdc6daa",
        name="EmptyTarget",
        description="",
        params=(),
    ),
    "0x066f53b1": GmxError(
        selector="0x066f53b1",
        name="EmptyToken",
        description="",
        params=(),
    ),
    "0x9fc297fa": GmxError(
        selector="0x9fc297fa",
        name="EmptyTokenTranferGasLimit",
        description="",
        params=("address",),
    ),
    "0x9231be69": GmxError(
        selector="0x9231be69",
        name="EmptyValidatedPrices",
        description="",
        params=(),
    ),
    "0x6d4bb5e9": GmxError(
        selector="0x6d4bb5e9",
        name="EmptyWithdrawal",
        description="",
        params=(),
    ),
    "0x01d6f7b1": GmxError(
        selector="0x01d6f7b1",
        name="EmptyWithdrawalAmount",
        description="",
        params=(),
    ),
    "0x4e48dcda": GmxError(
        selector="0x4e48dcda",
        name="EndOfOracleSimulation",
        description="",
        params=(),
    ),
    "0xeeadc89d": GmxError(
        selector="0xeeadc89d",
        name="EventItemNotFound",
        description="",
        params=("string",),
    ),
    "0x59afd6c6": GmxError(
        selector="0x59afd6c6",
        name="ExternalCallFailed",
        description="",
        params=("bytes",),
    ),
    "0x2df6dc23": GmxError(
        selector="0x2df6dc23",
        name="FeeBatchNotFound",
        description="",
        params=("bytes32",),
    ),
    "0x53d1caca": GmxError(
        selector="0x53d1caca",
        name="FeeDistributionAlreadyCompleted",
        description="",
        params=("uint256", "uint256"),
    ),
    "0xe44992d0": GmxError(
        selector="0xe44992d0",
        name="GlvAlreadyExists",
        description="",
        params=("bytes32", "address"),
    ),
    "0x057058b6": GmxError(
        selector="0x057058b6",
        name="GlvDepositNotFound",
        description="",
        params=("bytes32",),
    ),
    "0x30b8a225": GmxError(
        selector="0x30b8a225",
        name="GlvDisabledMarket",
        description="",
        params=("address", "address"),
    ),
    "0x8da31161": GmxError(
        selector="0x8da31161",
        name="GlvEnabledMarket",
        description="",
        params=("address", "address"),
    ),
    "0xc8b70b2c": GmxError(
        selector="0xc8b70b2c",
        name="GlvInsufficientMarketTokenBalance",
        description="",
        params=("address", "address", "uint256", "uint256"),
    ),
    "0x80ad6831": GmxError(
        selector="0x80ad6831",
        name="GlvInvalidLongToken",
        description="",
        params=("address", "address", "address"),
    ),
    "0x9673a10b": GmxError(
        selector="0x9673a10b",
        name="GlvInvalidShortToken",
        description="",
        params=("address", "address", "address"),
    ),
    "0x3aa9fc91": GmxError(
        selector="0x3aa9fc91",
        name="GlvMarketAlreadyExists",
        description="",
        params=("address", "address"),
    ),
    "0xaf7d3787": GmxError(
        selector="0xaf7d3787",
        name="GlvMaxMarketCountExceeded",
        description="",
        params=("address", "uint256"),
    ),
    "0xd859f947": GmxError(
        selector="0xd859f947",
        name="GlvMaxMarketTokenBalanceAmountExceeded",
        description="",
        params=("address", "address", "uint256", "uint256"),
    ),
    "0x66560e7d": GmxError(
        selector="0x66560e7d",
        name="GlvMaxMarketTokenBalanceUsdExceeded",
        description="",
        params=("address", "address", "uint256", "uint256"),
    ),
    "0x155712e1": GmxError(
        selector="0x155712e1",
        name="GlvNameTooLong",
        description="",
        params=(),
    ),
    "0x2e3780e5": GmxError(
        selector="0x2e3780e5",
        name="GlvNegativeMarketPoolValue",
        description="",
        params=("address", "address"),
    ),
    "0x3afc5e65": GmxError(
        selector="0x3afc5e65",
        name="GlvNonZeroMarketBalance",
        description="",
        params=("address", "address"),
    ),
    "0x6c00ed8a": GmxError(
        selector="0x6c00ed8a",
        name="GlvNotFound",
        description="",
        params=("address",),
    ),
    "0x232d7165": GmxError(
        selector="0x232d7165",
        name="GlvShiftIntervalNotYetPassed",
        description="",
        params=("uint256", "uint256", "uint256"),
    ),
    "0xf4dfe85d": GmxError(
        selector="0xf4dfe85d",
        name="GlvShiftMaxLossExceeded",
        description="",
        params=("uint256", "uint256"),
    ),
    "0xde45e162": GmxError(
        selector="0xde45e162",
        name="GlvShiftNotFound",
        description="",
        params=("bytes32",),
    ),
    "0x9cb4f5c5": GmxError(
        selector="0x9cb4f5c5",
        name="GlvSymbolTooLong",
        description="",
        params=(),
    ),
    "0x07e9c4d5": GmxError(
        selector="0x07e9c4d5",
        name="GlvUnsupportedMarket",
        description="",
        params=("address", "address"),
    ),
    "0x20dcb068": GmxError(
        selector="0x20dcb068",
        name="GlvWithdrawalNotFound",
        description="",
        params=("bytes32",),
    ),
    "0xd90abe06": GmxError(
        selector="0xd90abe06",
        name="GmEmptySigner",
        description="",
        params=("uint256",),
    ),
    "0xee6e8ecf": GmxError(
        selector="0xee6e8ecf",
        name="GmInvalidBlockNumber",
        description="",
        params=("uint256", "uint256"),
    ),
    "0xb8aaa455": GmxError(
        selector="0xb8aaa455",
        name="GmInvalidMinMaxBlockNumber",
        description="",
        params=("uint256", "uint256"),
    ),
    "0xc7b44b28": GmxError(
        selector="0xc7b44b28",
        name="GmMaxOracleSigners",
        description="",
        params=("uint256", "uint256"),
    ),
    "0x0f885e52": GmxError(
        selector="0x0f885e52",
        name="GmMaxPricesNotSorted",
        description="",
        params=("address", "uint256", "uint256"),
    ),
    "0x5b1250e7": GmxError(
        selector="0x5b1250e7",
        name="GmMaxSignerIndex",
        description="",
        params=("uint256", "uint256"),
    ),
    "0xdc2a99e7": GmxError(
        selector="0xdc2a99e7",
        name="GmMinOracleSigners",
        description="",
        params=("uint256", "uint256"),
    ),
    "0xcc7bbd5b": GmxError(
        selector="0xcc7bbd5b",
        name="GmMinPricesNotSorted",
        description="",
        params=("address", "uint256", "uint256"),
    ),
    "0xa581f648": GmxError(
        selector="0xa581f648",
        name="InsufficientBuybackOutputAmount",
        description="",
        params=("address", "address", "uint256", "uint256"),
    ),
    "0x74cc815b": GmxError(
        selector="0x74cc815b",
        name="InsufficientCollateralAmount",
        description="Collateral amount is below the minimum required",
        params=("uint256", "int256"),
    ),
    "0x2159b161": GmxError(
        selector="0x2159b161",
        name="InsufficientCollateralUsd",
        description="Remaining collateral USD is below the minimum required",
        params=("int256",),
    ),
    "0x5dac504d": GmxError(
        selector="0x5dac504d",
        name="InsufficientExecutionFee",
        description="Execution fee provided is below the required minimum",
        params=("uint256", "uint256"),
    ),
    "0xbb416f93": GmxError(
        selector="0xbb416f93",
        name="InsufficientExecutionGas",
        description="Caller did not forward enough gas to execute the handler",
        params=("uint256", "uint256", "uint256"),
    ),
    "0x79293964": GmxError(
        selector="0x79293964",
        name="InsufficientExecutionGasForErrorHandling",
        description="",
        params=("uint256", "uint256"),
    ),
    "0xa458261b": GmxError(
        selector="0xa458261b",
        name="InsufficientFee",
        description="",
        params=("uint256", "uint256"),
    ),
    "0x9fc47b77": GmxError(
        selector="0x9fc47b77",
        name="InsufficientFunds",
        description="",
        params=("address",),
    ),
    "0x19d50093": GmxError(
        selector="0x19d50093",
        name="InsufficientFundsToPayForCosts",
        description="",
        params=("uint256", "string"),
    ),
    "0xe73a05d5": GmxError(
        selector="0xe73a05d5",
        name="InsufficientGasForAutoCancellation",
        description="",
        params=("uint256", "uint256"),
    ),
    "0xd3dacaac": GmxError(
        selector="0xd3dacaac",
        name="InsufficientGasForCancellation",
        description="Caller did not forward enough gas to cancel the order on error",
        params=("uint256", "uint256"),
    ),
    "0xf50ce733": GmxError(
        selector="0xf50ce733",
        name="InsufficientGasLeft",
        description="",
        params=("uint256", "uint256"),
    ),
    "0x79a2abad": GmxError(
        selector="0x79a2abad",
        name="InsufficientGasLeftForCallback",
        description="",
        params=("uint256", "uint256"),
    ),
    "0x3083b9e5": GmxError(
        selector="0x3083b9e5",
        name="InsufficientHandleExecutionErrorGas",
        description="",
        params=("uint256", "uint256"),
    ),
    "0x8643d20a": GmxError(
        selector="0x8643d20a",
        name="InsufficientImpactPoolValueForWithdrawal",
        description="",
        params=("uint256", "uint256", "int256"),
    ),
    "0x82c8828a": GmxError(
        selector="0x82c8828a",
        name="InsufficientMarketTokens",
        description="",
        params=("uint256", "uint256"),
    ),
    "0x4ac6d095": GmxError(
        selector="0x4ac6d095",
        name="InsufficientMultichainBalance",
        description="",
        params=("address", "address", "uint256", "uint256"),
    ),
    "0x77f8f169": GmxError(
        selector="0x77f8f169",
        name="InsufficientMultichainNativeFee",
        description="",
        params=("uint256",),
    ),
    "0xb5749baf": GmxError(
        selector="0xb5749baf",
        name="InsufficientNativeTokenAmount",
        description="",
        params=("uint256", "uint256"),
    ),
    "0xd28d3eb5": GmxError(
        selector="0xd28d3eb5",
        name="InsufficientOutputAmount",
        description="Output amount is below the minimum acceptable",
        params=("uint256", "uint256"),
    ),
    "0x23090a31": GmxError(
        selector="0x23090a31",
        name="InsufficientPoolAmount",
        description="Pool does not have enough tokens to fill the order",
        params=("uint256", "uint256"),
    ),
    "0x9cd76295": GmxError(
        selector="0x9cd76295",
        name="InsufficientRelayFee",
        description="",
        params=("uint256", "uint256"),
    ),
    "0x315276c9": GmxError(
        selector="0x315276c9",
        name="InsufficientReserve",
        description="Reserve is insufficient to back the open interest",
        params=("uint256", "uint256"),
    ),
    "0xb98c6179": GmxError(
        selector="0xb98c6179",
        name="InsufficientReserveForOpenInterest",
        description="Reserve is insufficient relative to open interest",
        params=("uint256", "uint256"),
    ),
    "0xa7aebadc": GmxError(
        selector="0xa7aebadc",
        name="InsufficientSwapOutputAmount",
        description="Swap output is below the minimum acceptable",
        params=("uint256", "uint256"),
    ),
    "0x3a78cd7e": GmxError(
        selector="0x3a78cd7e",
        name="InsufficientWntAmountForExecutionFee",
        description="WNT (wrapped ETH) provided for execution fee is insufficient",
        params=("uint256", "uint256"),
    ),
    "0x1d4fc3c0": GmxError(
        selector="0x1d4fc3c0",
        name="InvalidAdl",
        description="",
        params=("int256", "int256"),
    ),
    "0x8ac146e6": GmxError(
        selector="0x8ac146e6",
        name="InvalidAmountInForFeeBatch",
        description="",
        params=("uint256", "uint256"),
    ),
    "0xeb19d3f5": GmxError(
        selector="0xeb19d3f5",
        name="InvalidBaseKey",
        description="",
        params=("bytes32",),
    ),
    "0x25e5dc07": GmxError(
        selector="0x25e5dc07",
        name="InvalidBlockRangeSet",
        description="",
        params=("uint256", "uint256"),
    ),
    "0x2877599b": GmxError(
        selector="0x2877599b",
        name="InvalidBridgeOutToken",
        description="",
        params=("address",),
    ),
    "0x752fdb63": GmxError(
        selector="0x752fdb63",
        name="InvalidBuybackToken",
        description="",
        params=("address",),
    ),
    "0x89736584": GmxError(
        selector="0x89736584",
        name="InvalidCancellationReceiverForSubaccountOrder",
        description="",
        params=("address", "address"),
    ),
    "0x5b3043dd": GmxError(
        selector="0x5b3043dd",
        name="InvalidClaimAffiliateRewardsInput",
        description="",
        params=("uint256", "uint256"),
    ),
    "0x42c0d1f2": GmxError(
        selector="0x42c0d1f2",
        name="InvalidClaimCollateralInput",
        description="",
        params=("uint256", "uint256", "uint256"),
    ),
    "0x7363cfa5": GmxError(
        selector="0x7363cfa5",
        name="InvalidClaimFundingFeesInput",
        description="",
        params=("uint256", "uint256"),
    ),
    "0x6ac60b4a": GmxError(
        selector="0x6ac60b4a",
        name="InvalidClaimTermsSignature",
        description="",
        params=("address", "address"),
    ),
    "0x500016f0": GmxError(
        selector="0x500016f0",
        name="InvalidClaimTermsSignatureForContract",
        description="",
        params=("address",),
    ),
    "0x74cee48d": GmxError(
        selector="0x74cee48d",
        name="InvalidClaimUiFeesInput",
        description="",
        params=("uint256", "uint256"),
    ),
    "0x6c2738d3": GmxError(
        selector="0x6c2738d3",
        name="InvalidClaimableFactor",
        description="",
        params=("uint256",),
    ),
    "0x7cf9eb07": GmxError(
        selector="0x7cf9eb07",
        name="InvalidClaimableReductionFactor",
        description="",
        params=("uint256",),
    ),
    "0x839c693e": GmxError(
        selector="0x839c693e",
        name="InvalidCollateralTokenForMarket",
        description="The collateral token is not accepted by this market",
        params=("address", "address"),
    ),
    "0x4a591309": GmxError(
        selector="0x4a591309",
        name="InvalidContributorToken",
        description="",
        params=("address",),
    ),
    "0x8d56bea1": GmxError(
        selector="0x8d56bea1",
        name="InvalidDataStreamBidAsk",
        description="",
        params=("address", "int192", "int192"),
    ),
    "0xa4949e25": GmxError(
        selector="0xa4949e25",
        name="InvalidDataStreamFeedId",
        description="",
        params=("address", "bytes32", "bytes32"),
    ),
    "0x2a74194d": GmxError(
        selector="0x2a74194d",
        name="InvalidDataStreamPrices",
        description="",
        params=("address", "int192", "int192"),
    ),
    "0x6e0c29ed": GmxError(
        selector="0x6e0c29ed",
        name="InvalidDataStreamSpreadReductionFactor",
        description="",
        params=("address", "uint256"),
    ),
    "0x9fbe2cbc": GmxError(
        selector="0x9fbe2cbc",
        name="InvalidDecreaseOrderSize",
        description="Decrease order size exceeds the remaining position size",
        params=("uint256", "uint256"),
    ),
    "0x751951f9": GmxError(
        selector="0x751951f9",
        name="InvalidDecreasePositionSwapType",
        description="",
        params=("uint256",),
    ),
    "0xc3776a2c": GmxError(
        selector="0xc3776a2c",
        name="InvalidDestinationChainId",
        description="",
        params=("uint256",),
    ),
    "0x8695f464": GmxError(
        selector="0x8695f464",
        name="InvalidDistributionState",
        description="",
        params=("uint256",),
    ),
    "0xe75fc463": GmxError(
        selector="0xe75fc463",
        name="InvalidEdgeDataStreamBidAsk",
        description="",
        params=("address", "uint256", "uint256"),
    ),
    "0x8bb5c4bf": GmxError(
        selector="0x8bb5c4bf",
        name="InvalidEdgeDataStreamExpo",
        description="",
        params=("int256",),
    ),
    "0x4234439c": GmxError(
        selector="0x4234439c",
        name="InvalidEdgeDataStreamPrices",
        description="",
        params=("address", "uint256", "uint256"),
    ),
    "0x545e155f": GmxError(
        selector="0x545e155f",
        name="InvalidEdgeSignature",
        description="",
        params=("uint256",),
    ),
    "0x8a1cc36b": GmxError(
        selector="0x8a1cc36b",
        name="InvalidEdgeSigner",
        description="",
        params=(),
    ),
    "0x3ee39805": GmxError(
        selector="0x3ee39805",
        name="InvalidEid",
        description="",
        params=("uint256",),
    ),
    "0x9b867f31": GmxError(
        selector="0x9b867f31",
        name="InvalidExecutionFee",
        description="",
        params=("uint256", "uint256", "uint256"),
    ),
    "0x99e26b44": GmxError(
        selector="0x99e26b44",
        name="InvalidExecutionFeeForMigration",
        description="",
        params=("uint256", "uint256"),
    ),
    "0x831e9f11": GmxError(
        selector="0x831e9f11",
        name="InvalidExternalCallInput",
        description="",
        params=("uint256", "uint256"),
    ),
    "0xbe55c895": GmxError(
        selector="0xbe55c895",
        name="InvalidExternalCallTarget",
        description="",
        params=("address",),
    ),
    "0xec7fd385": GmxError(
        selector="0xec7fd385",
        name="InvalidExternalCalls",
        description="",
        params=("uint256", "uint256"),
    ),
    "0xe15f2701": GmxError(
        selector="0xe15f2701",
        name="InvalidExternalReceiversInput",
        description="",
        params=("uint256", "uint256"),
    ),
    "0xfa804399": GmxError(
        selector="0xfa804399",
        name="InvalidFeeBatchTokenIndex",
        description="",
        params=("uint256", "uint256"),
    ),
    "0xcb9339d5": GmxError(
        selector="0xcb9339d5",
        name="InvalidFeeReceiver",
        description="",
        params=("address",),
    ),
    "0xbe6514b6": GmxError(
        selector="0xbe6514b6",
        name="InvalidFeedPrice",
        description="Oracle price feed returned an invalid (zero/negative) value",
        params=("address", "int256"),
    ),
    "0xfc90fcc3": GmxError(
        selector="0xfc90fcc3",
        name="InvalidGlpAmount",
        description="",
        params=("uint256", "uint256"),
    ),
    "0xbf16cb0a": GmxError(
        selector="0xbf16cb0a",
        name="InvalidGlvDepositInitialLongToken",
        description="",
        params=("address",),
    ),
    "0xdf0f9a23": GmxError(
        selector="0xdf0f9a23",
        name="InvalidGlvDepositInitialShortToken",
        description="",
        params=("address",),
    ),
    "0x055ab8b9": GmxError(
        selector="0x055ab8b9",
        name="InvalidGlvDepositSwapPath",
        description="",
        params=("uint256", "uint256"),
    ),
    "0x993417d5": GmxError(
        selector="0x993417d5",
        name="InvalidGmMedianMinMaxPrice",
        description="",
        params=("uint256", "uint256"),
    ),
    "0xa54d4339": GmxError(
        selector="0xa54d4339",
        name="InvalidGmOraclePrice",
        description="",
        params=("address",),
    ),
    "0x8d648a7f": GmxError(
        selector="0x8d648a7f",
        name="InvalidGmSignature",
        description="",
        params=("address", "address"),
    ),
    "0xb21c863e": GmxError(
        selector="0xb21c863e",
        name="InvalidGmSignerMinMaxPrice",
        description="",
        params=("uint256", "uint256"),
    ),
    "0x7bb9d8f8": GmxError(
        selector="0x7bb9d8f8",
        name="InvalidHoldingAddress",
        description="",
        params=("address",),
    ),
    "0xadc06ae7": GmxError(
        selector="0xadc06ae7",
        name="InvalidInitializer",
        description="",
        params=(),
    ),
    "0xe5feddc0": GmxError(
        selector="0xe5feddc0",
        name="InvalidKeeperForFrozenOrder",
        description="The caller is not authorized to execute frozen orders",
        params=("address",),
    ),
    "0x33a1ea6b": GmxError(
        selector="0x33a1ea6b",
        name="InvalidMarketTokenBalance",
        description="",
        params=("address", "address", "uint256", "uint256"),
    ),
    "0x9dd026db": GmxError(
        selector="0x9dd026db",
        name="InvalidMarketTokenBalanceForClaimableFunding",
        description="",
        params=("address", "address", "uint256", "uint256"),
    ),
    "0x808c464f": GmxError(
        selector="0x808c464f",
        name="InvalidMarketTokenBalanceForCollateralAmount",
        description="",
        params=("address", "address", "uint256", "uint256"),
    ),
    "0xc08bb8a0": GmxError(
        selector="0xc08bb8a0",
        name="InvalidMinGlvTokensForFirstGlvDeposit",
        description="",
        params=("uint256", "uint256"),
    ),
    "0x3f9c06ab": GmxError(
        selector="0x3f9c06ab",
        name="InvalidMinMarketTokensForFirstDeposit",
        description="",
        params=("uint256", "uint256"),
    ),
    "0x1608d41a": GmxError(
        selector="0x1608d41a",
        name="InvalidMinMaxForPrice",
        description="",
        params=("address", "uint256", "uint256"),
    ),
    "0x9c9a99db": GmxError(
        selector="0x9c9a99db",
        name="InvalidMultichainEndpoint",
        description="",
        params=("address",),
    ),
    "0x2314a6e3": GmxError(
        selector="0x2314a6e3",
        name="InvalidMultichainProvider",
        description="",
        params=("address",),
    ),
    "0xe71a51be": GmxError(
        selector="0xe71a51be",
        name="InvalidNativeTokenSender",
        description="",
        params=("address",),
    ),
    "0x05d102a2": GmxError(
        selector="0x05d102a2",
        name="InvalidOracleProvider",
        description="",
        params=("address",),
    ),
    "0x68b49e6c": GmxError(
        selector="0x68b49e6c",
        name="InvalidOracleProviderForToken",
        description="",
        params=("address", "address"),
    ),
    "0xf9996e9f": GmxError(
        selector="0xf9996e9f",
        name="InvalidOracleSetPricesDataParam",
        description="",
        params=("uint256", "uint256"),
    ),
    "0xdd51dc73": GmxError(
        selector="0xdd51dc73",
        name="InvalidOracleSetPricesProvidersParam",
        description="",
        params=("uint256", "uint256"),
    ),
    "0xc1b14c91": GmxError(
        selector="0xc1b14c91",
        name="InvalidOracleSigner",
        description="",
        params=("address",),
    ),
    "0x0481a15a": GmxError(
        selector="0x0481a15a",
        name="InvalidOrderPrices",
        description="Order price bounds are invalid for this order type",
        params=("uint256", "uint256", "uint256", "uint256"),
    ),
    "0x253c8c02": GmxError(
        selector="0x253c8c02",
        name="InvalidOutputToken",
        description="",
        params=("address", "address"),
    ),
    "0xa8c278dd": GmxError(
        selector="0xa8c278dd",
        name="InvalidParams",
        description="",
        params=("string",),
    ),
    "0x3c0ac199": GmxError(
        selector="0x3c0ac199",
        name="InvalidPermitSpender",
        description="",
        params=("address", "address"),
    ),
    "0xadaa688d": GmxError(
        selector="0xadaa688d",
        name="InvalidPoolValueForDeposit",
        description="",
        params=("int256",),
    ),
    "0x90a6af3b": GmxError(
        selector="0x90a6af3b",
        name="InvalidPoolValueForWithdrawal",
        description="",
        params=("int256",),
    ),
    "0x15a1e249": GmxError(
        selector="0x15a1e249",
        name="InvalidPositionImpactPoolDistributionRate",
        description="",
        params=("uint256", "uint256"),
    ),
    "0x182e30e3": GmxError(
        selector="0x182e30e3",
        name="InvalidPositionMarket",
        description="The market is invalid for the position",
        params=("address",),
    ),
    "0xbff65b3f": GmxError(
        selector="0xbff65b3f",
        name="InvalidPositionSizeValues",
        description="Position size and collateral are inconsistent (e.g. size != 0 with zero collateral)",
        params=("uint256", "uint256"),
    ),
    "0x663de023": GmxError(
        selector="0x663de023",
        name="InvalidPrimaryPricesForSimulation",
        description="",
        params=("uint256", "uint256"),
    ),
    "0x9cfea583": GmxError(
        selector="0x9cfea583",
        name="InvalidReceiver",
        description="",
        params=("address",),
    ),
    "0x77e8e698": GmxError(
        selector="0x77e8e698",
        name="InvalidReceiverForFirstDeposit",
        description="",
        params=("address", "address"),
    ),
    "0x6eedac2f": GmxError(
        selector="0x6eedac2f",
        name="InvalidReceiverForFirstGlvDeposit",
        description="",
        params=("address", "address"),
    ),
    "0x4baab816": GmxError(
        selector="0x4baab816",
        name="InvalidReceiverForSubaccountOrder",
        description="",
        params=("address", "address"),
    ),
    "0x2416afa9": GmxError(
        selector="0x2416afa9",
        name="InvalidRecoveredSigner",
        description="",
        params=("string", "address", "address", "address"),
    ),
    "0xbfb09088": GmxError(
        selector="0xbfb09088",
        name="InvalidReferralRewardToken",
        description="",
        params=("address",),
    ),
    "0x530b2590": GmxError(
        selector="0x530b2590",
        name="InvalidSetContributorPaymentInput",
        description="",
        params=("uint256", "uint256"),
    ),
    "0x29a93dc4": GmxError(
        selector="0x29a93dc4",
        name="InvalidSetMaxTotalContributorTokenAmountInput",
        description="",
        params=("uint256", "uint256"),
    ),
    "0x2a34f7fe": GmxError(
        selector="0x2a34f7fe",
        name="InvalidSignature",
        description="",
        params=("string",),
    ),
    "0x720bb461": GmxError(
        selector="0x720bb461",
        name="InvalidSizeDeltaForAdl",
        description="",
        params=("uint256", "uint256"),
    ),
    "0x2c9bcbdd": GmxError(
        selector="0x2c9bcbdd",
        name="InvalidSrcChainId",
        description="",
        params=("uint256",),
    ),
    "0x7db6c745": GmxError(
        selector="0x7db6c745",
        name="InvalidSubaccountApprovalDesChainId",
        description="",
        params=("uint256",),
    ),
    "0x3044992f": GmxError(
        selector="0x3044992f",
        name="InvalidSubaccountApprovalNonce",
        description="",
        params=("uint256", "uint256"),
    ),
    "0x545e8f2b": GmxError(
        selector="0x545e8f2b",
        name="InvalidSubaccountApprovalSubaccount",
        description="",
        params=(),
    ),
    "0xcb9bd134": GmxError(
        selector="0xcb9bd134",
        name="InvalidSwapMarket",
        description="",
        params=("address",),
    ),
    "0x6ba3dd8b": GmxError(
        selector="0x6ba3dd8b",
        name="InvalidSwapOutputToken",
        description="",
        params=("address", "address"),
    ),
    "0x672e4fba": GmxError(
        selector="0x672e4fba",
        name="InvalidSwapPathForV1",
        description="",
        params=("address[]", "address"),
    ),
    "0xe6b0ddb6": GmxError(
        selector="0xe6b0ddb6",
        name="InvalidTimelockDelay",
        description="",
        params=("uint256",),
    ),
    "0x961c9a4f": GmxError(
        selector="0x961c9a4f",
        name="InvalidToken",
        description="",
        params=("address",),
    ),
    "0x53f81711": GmxError(
        selector="0x53f81711",
        name="InvalidTokenIn",
        description="Swap input token is invalid for the chosen market",
        params=("address", "address"),
    ),
    "0xb0731c3f": GmxError(
        selector="0xb0731c3f",
        name="InvalidTransferRequestsLength",
        description="",
        params=(),
    ),
    "0x9e5d5cf3": GmxError(
        selector="0x9e5d5cf3",
        name="InvalidTrustedSignerAddress",
        description="",
        params=(),
    ),
    "0x81468139": GmxError(
        selector="0x81468139",
        name="InvalidUiFeeFactor",
        description="",
        params=("uint256", "uint256"),
    ),
    "0x1041c08a": GmxError(
        selector="0x1041c08a",
        name="InvalidUserDigest",
        description="",
        params=("bytes32",),
    ),
    "0x1de2bca4": GmxError(
        selector="0x1de2bca4",
        name="InvalidVersion",
        description="",
        params=("uint256",),
    ),
    "0x32aedc9f": GmxError(
        selector="0x32aedc9f",
        name="JitEmptyShiftParams",
        description="",
        params=(),
    ),
    "0xf5489e5e": GmxError(
        selector="0xf5489e5e",
        name="JitInvalidToMarket",
        description="",
        params=("address", "address"),
    ),
    "0x262be6a6": GmxError(
        selector="0x262be6a6",
        name="JitUnsupportedOrderType",
        description="",
        params=("uint256",),
    ),
    "0x088e379a": GmxError(
        selector="0x088e379a",
        name="KeeperAmountMismatch",
        description="",
        params=("uint256", "uint256"),
    ),
    "0x1f983722": GmxError(
        selector="0x1f983722",
        name="KeeperArrayLengthMismatch",
        description="",
        params=("uint256", "uint256", "uint256"),
    ),
    "0xbc121108": GmxError(
        selector="0xbc121108",
        name="LiquidatablePosition",
        description="Position is liquidatable",
        params=("string", "int256", "int256", "int256"),
    ),
    "0xa38dfb2a": GmxError(
        selector="0xa38dfb2a",
        name="LongTokensAreNotEqual",
        description="",
        params=("address", "address"),
    ),
    "0x25e34fa1": GmxError(
        selector="0x25e34fa1",
        name="MarketAlreadyExists",
        description="",
        params=("bytes32", "address"),
    ),
    "0x6918f9bf": GmxError(
        selector="0x6918f9bf",
        name="MarketNotFound",
        description="",
        params=("address",),
    ),
    "0x143e2156": GmxError(
        selector="0x143e2156",
        name="MaskIndexOutOfBounds",
        description="",
        params=("uint256", "string"),
    ),
    "0xf0794a60": GmxError(
        selector="0xf0794a60",
        name="MaxAutoCancelOrdersExceeded",
        description="Too many auto-cancel orders attached to this position",
        params=("uint256", "uint256"),
    ),
    "0x4e3f62a8": GmxError(
        selector="0x4e3f62a8",
        name="MaxBuybackPriceAgeExceeded",
        description="",
        params=("uint256", "uint256", "uint256"),
    ),
    "0x10aeb692": GmxError(
        selector="0x10aeb692",
        name="MaxCallbackGasLimitExceeded",
        description="",
        params=("uint256", "uint256"),
    ),
    "0xd1a942ab": GmxError(
        selector="0xd1a942ab",
        name="MaxCollateralSumExceeded",
        description="Maximum collateral sum exceeded for this market",
        params=("uint256", "uint256"),
    ),
    "0xa0629236": GmxError(
        selector="0xa0629236",
        name="MaxDataListLengthExceeded",
        description="",
        params=("uint256", "uint256"),
    ),
    "0xb96d6372": GmxError(
        selector="0xb96d6372",
        name="MaxEsGmxReferralRewardsAmountExceeded",
        description="",
        params=("uint256", "uint256"),
    ),
    "0x4f82a998": GmxError(
        selector="0x4f82a998",
        name="MaxFundingFactorPerSecondLimitExceeded",
        description="",
        params=("uint256", "uint256"),
    ),
    "0xca42750e": GmxError(
        selector="0xca42750e",
        name="MaxLendableFactorForWithdrawalsExceeded",
        description="",
        params=("uint256", "uint256", "uint256"),
    ),
    "0x2bf127cf": GmxError(
        selector="0x2bf127cf",
        name="MaxOpenInterestExceeded",
        description="Maximum total open interest exceeded for this market",
        params=("uint256", "uint256"),
    ),
    "0xdd9c6b9a": GmxError(
        selector="0xdd9c6b9a",
        name="MaxOracleTimestampRangeExceeded",
        description="",
        params=("uint256", "uint256"),
    ),
    "0x6429ff3f": GmxError(
        selector="0x6429ff3f",
        name="MaxPoolAmountExceeded",
        description="Pool amount cap exceeded",
        params=("uint256", "uint256"),
    ),
    "0x46169f04": GmxError(
        selector="0x46169f04",
        name="MaxPoolUsdForDepositExceeded",
        description="Pool USD cap for deposits exceeded",
        params=("uint256", "uint256"),
    ),
    "0x2b6e7c3f": GmxError(
        selector="0x2b6e7c3f",
        name="MaxPriceAgeExceeded",
        description="Oracle price is too stale to be used",
        params=("uint256", "uint256"),
    ),
    "0x3d1986f7": GmxError(
        selector="0x3d1986f7",
        name="MaxRefPriceDeviationExceeded",
        description="",
        params=("address", "uint256", "uint256", "uint256"),
    ),
    "0xc1fa6843": GmxError(
        selector="0xc1fa6843",
        name="MaxReferralRewardsExceeded",
        description="",
        params=("address", "uint256", "uint256"),
    ),
    "0xc0471bf8": GmxError(
        selector="0xc0471bf8",
        name="MaxRelayFeeSwapForSubaccountExceeded",
        description="",
        params=("uint256", "uint256"),
    ),
    "0x519ba753": GmxError(
        selector="0x519ba753",
        name="MaxSubaccountActionCountExceeded",
        description="",
        params=("address", "address", "uint256", "uint256"),
    ),
    "0x9da36043": GmxError(
        selector="0x9da36043",
        name="MaxSwapPathLengthExceeded",
        description="",
        params=("uint256", "uint256"),
    ),
    "0xfaf66f0c": GmxError(
        selector="0xfaf66f0c",
        name="MaxTimelockDelayExceeded",
        description="",
        params=("uint256",),
    ),
    "0xc10ceac7": GmxError(
        selector="0xc10ceac7",
        name="MaxTotalCallbackGasLimitForAutoCancelOrdersExceeded",
        description="",
        params=("uint256", "uint256"),
    ),
    "0x043038f0": GmxError(
        selector="0x043038f0",
        name="MaxTotalContributorTokenAmountExceeded",
        description="",
        params=("address", "uint256", "uint256"),
    ),
    "0x89a90794": GmxError(
        selector="0x89a90794",
        name="MaxWntFromTreasuryExceeded",
        description="",
        params=("uint256", "uint256"),
    ),
    "0xe6685115": GmxError(
        selector="0xe6685115",
        name="MaxWntReferralRewardsInUsdAmountExceeded",
        description="",
        params=("uint256", "uint256"),
    ),
    "0x30ae0954": GmxError(
        selector="0x30ae0954",
        name="MaxWntReferralRewardsInUsdExceeded",
        description="",
        params=("uint256", "uint256"),
    ),
    "0x961b4025": GmxError(
        selector="0x961b4025",
        name="MinContributorPaymentIntervalBelowAllowedRange",
        description="",
        params=("uint256",),
    ),
    "0xb9dc7b9d": GmxError(
        selector="0xb9dc7b9d",
        name="MinContributorPaymentIntervalNotYetPassed",
        description="",
        params=("uint256",),
    ),
    "0x966fea10": GmxError(
        selector="0x966fea10",
        name="MinGlvTokens",
        description="",
        params=("uint256", "uint256"),
    ),
    "0xf442c0bc": GmxError(
        selector="0xf442c0bc",
        name="MinLongTokens",
        description="Received long tokens below the minimum acceptable",
        params=("uint256", "uint256"),
    ),
    "0x6ce23460": GmxError(
        selector="0x6ce23460",
        name="MinMarketTokens",
        description="Received market tokens below the minimum acceptable",
        params=("uint256", "uint256"),
    ),
    "0x85efb31a": GmxError(
        selector="0x85efb31a",
        name="MinPositionSize",
        description="Position size delta is below the minimum allowed",
        params=("uint256", "uint256"),
    ),
    "0xb4a196af": GmxError(
        selector="0xb4a196af",
        name="MinShortTokens",
        description="Received short tokens below the minimum acceptable",
        params=("uint256", "uint256"),
    ),
    "0xcc32db99": GmxError(
        selector="0xcc32db99",
        name="NegativeExecutionPrice",
        description="Computed execution price went negative (price impact too large)",
        params=("int256", "uint256", "uint256", "int256", "uint256"),
    ),
    "0x53410c43": GmxError(
        selector="0x53410c43",
        name="NonAtomicOracleProvider",
        description="",
        params=("address",),
    ),
    "0x28f773e9": GmxError(
        selector="0x28f773e9",
        name="NonEmptyExternalCallsForSubaccountOrder",
        description="",
        params=(),
    ),
    "0xef2df9b5": GmxError(
        selector="0xef2df9b5",
        name="NonEmptyTokensWithPrices",
        description="",
        params=("uint256",),
    ),
    "0x730293fd": GmxError(
        selector="0x730293fd",
        name="OpenInterestCannotBeUpdatedForSwapOnlyMarket",
        description="",
        params=("address",),
    ),
    "0x48afc38e": GmxError(
        selector="0x48afc38e",
        name="OraclePriceOutdated",
        description="",
        params=(),
    ),
    "0xa6013d30": GmxError(
        selector="0xa6013d30",
        name="OracleProviderAlreadyExistsForToken",
        description="",
        params=("address", "address"),
    ),
    "0x73f9981d": GmxError(
        selector="0x73f9981d",
        name="OracleProviderMinChangeDelayNotYetPassed",
        description="",
        params=("address", "address"),
    ),
    "0xd84b8ee8": GmxError(
        selector="0xd84b8ee8",
        name="OracleTimestampsAreLargerThanRequestExpirationTime",
        description="",
        params=("uint256", "uint256", "uint256"),
    ),
    "0x7d677abf": GmxError(
        selector="0x7d677abf",
        name="OracleTimestampsAreSmallerThanRequired",
        description="Oracle timestamps are older than the required floor",
        params=("uint256", "uint256"),
    ),
    "0x730d44b1": GmxError(
        selector="0x730d44b1",
        name="OrderAlreadyFrozen",
        description="The order is already in the frozen state",
        params=(),
    ),
    "0x59485ed9": GmxError(
        selector="0x59485ed9",
        name="OrderNotFound",
        description="No order found for the given key",
        params=("bytes32",),
    ),
    "0xe09ad0e9": GmxError(
        selector="0xe09ad0e9",
        name="OrderNotFulfillableAtAcceptablePrice",
        description="Execution price exceeds the acceptable price slippage bound",
        params=("uint256", "uint256"),
    ),
    "0x9aba92cb": GmxError(
        selector="0x9aba92cb",
        name="OrderNotUpdatable",
        description="",
        params=("uint256",),
    ),
    "0x8a4bd513": GmxError(
        selector="0x8a4bd513",
        name="OrderTypeCannotBeCreated",
        description="",
        params=("uint256",),
    ),
    "0xcf9319d6": GmxError(
        selector="0xcf9319d6",
        name="OrderValidFromTimeNotReached",
        description="Order validFrom timestamp has not been reached yet",
        params=("uint256", "uint256"),
    ),
    "0xef84cb99": GmxError(
        selector="0xef84cb99",
        name="OutdatedReadResponse",
        description="",
        params=("uint256",),
    ),
    "0xb92fb250": GmxError(
        selector="0xb92fb250",
        name="PnlFactorExceededForLongs",
        description="",
        params=("int256", "uint256"),
    ),
    "0xb0010694": GmxError(
        selector="0xb0010694",
        name="PnlFactorExceededForShorts",
        description="",
        params=("int256", "uint256"),
    ),
    "0x9f0bc7de": GmxError(
        selector="0x9f0bc7de",
        name="PnlOvercorrected",
        description="",
        params=("int256", "uint256"),
    ),
    "0x426cfff0": GmxError(
        selector="0x426cfff0",
        name="PositionNotFound",
        description="No position found for the given key",
        params=("bytes32",),
    ),
    "0xee919dd9": GmxError(
        selector="0xee919dd9",
        name="PositionShouldNotBeLiquidated",
        description="Position should not be liquidated",
        params=("string", "int256", "int256", "int256"),
    ),
    "0xded099de": GmxError(
        selector="0xded099de",
        name="PriceAlreadySet",
        description="",
        params=("address", "uint256", "uint256"),
    ),
    "0xd4141298": GmxError(
        selector="0xd4141298",
        name="PriceFeedAlreadyExistsForToken",
        description="",
        params=("address",),
    ),
    "0xf0641c92": GmxError(
        selector="0xf0641c92",
        name="PriceImpactLargerThanOrderSize",
        description="Price impact exceeds the order size",
        params=("int256", "uint256"),
    ),
    "0xeef4e171": GmxError(
        selector="0xeef4e171",
        name="ReductionExceedsLentAmount",
        description="",
        params=("uint256", "uint256"),
    ),
    "0x519e91bb": GmxError(
        selector="0x519e91bb",
        name="ReferralCodeAlreadyExists",
        description="",
        params=("bytes32",),
    ),
    "0xb09ace9a": GmxError(
        selector="0xb09ace9a",
        name="RelayCalldataTooLong",
        description="",
        params=("uint256",),
    ),
    "0x25eeb47a": GmxError(
        selector="0x25eeb47a",
        name="RelayEmptyBatch",
        description="",
        params=(),
    ),
    "0x7290c82f": GmxError(
        selector="0x7290c82f",
        name="RemovalShouldNotBeSkipped",
        description="",
        params=("bytes32", "bytes32"),
    ),
    "0xe8266438": GmxError(
        selector="0xe8266438",
        name="RequestNotYetCancellable",
        description="",
        params=("uint256", "uint256", "string"),
    ),
    "0xe70f9152": GmxError(
        selector="0xe70f9152",
        name="SelfTransferNotSupported",
        description="",
        params=("address",),
    ),
    "0xf0b8da75": GmxError(
        selector="0xf0b8da75",
        name="SendEthToKeeperFailed",
        description="",
        params=("address", "uint256", "bytes"),
    ),
    "0x032b3d00": GmxError(
        selector="0x032b3d00",
        name="SequencerDown",
        description="",
        params=(),
    ),
    "0x113cfc03": GmxError(
        selector="0x113cfc03",
        name="SequencerGraceDurationNotYetPassed",
        description="",
        params=("uint256", "uint256"),
    ),
    "0x950227bb": GmxError(
        selector="0x950227bb",
        name="ShiftFromAndToMarketAreEqual",
        description="",
        params=("address",),
    ),
    "0xb611f297": GmxError(
        selector="0xb611f297",
        name="ShiftNotFound",
        description="",
        params=("bytes32",),
    ),
    "0xf54d8776": GmxError(
        selector="0xf54d8776",
        name="ShortTokensAreNotEqual",
        description="",
        params=("address", "address"),
    ),
    "0x20b23584": GmxError(
        selector="0x20b23584",
        name="SignalTimeNotYetPassed",
        description="",
        params=("uint256",),
    ),
    "0x26025b4e": GmxError(
        selector="0x26025b4e",
        name="SubaccountApprovalDeadlinePassed",
        description="",
        params=("uint256", "uint256"),
    ),
    "0x9b539f07": GmxError(
        selector="0x9b539f07",
        name="SubaccountApprovalExpired",
        description="",
        params=("address", "address", "uint256", "uint256"),
    ),
    "0x34e5c9e2": GmxError(
        selector="0x34e5c9e2",
        name="SubaccountIntegrationIdDisabled",
        description="",
        params=("bytes32",),
    ),
    "0x9be0a43c": GmxError(
        selector="0x9be0a43c",
        name="SubaccountNotAuthorized",
        description="",
        params=("address", "address"),
    ),
    "0x75885d69": GmxError(
        selector="0x75885d69",
        name="SwapPriceImpactExceedsAmountIn",
        description="Swap price impact exceeds the input amount",
        params=("uint256", "int256"),
    ),
    "0xd2e229e6": GmxError(
        selector="0xd2e229e6",
        name="SwapsNotAllowedForAtomicWithdrawal",
        description="",
        params=("uint256", "uint256"),
    ),
    "0x7bf8d2b3": GmxError(
        selector="0x7bf8d2b3",
        name="SyncConfigInvalidInputLengths",
        description="",
        params=("uint256", "uint256"),
    ),
    "0x624b5b13": GmxError(
        selector="0x624b5b13",
        name="SyncConfigInvalidMarketFromData",
        description="",
        params=("address", "address"),
    ),
    "0x8b3d4655": GmxError(
        selector="0x8b3d4655",
        name="SyncConfigUpdatesDisabledForMarket",
        description="",
        params=("address",),
    ),
    "0x0798d283": GmxError(
        selector="0x0798d283",
        name="SyncConfigUpdatesDisabledForMarketParameter",
        description="",
        params=("address", "string"),
    ),
    "0x8ea7eb18": GmxError(
        selector="0x8ea7eb18",
        name="SyncConfigUpdatesDisabledForParameter",
        description="",
        params=("string",),
    ),
    "0xb783c88a": GmxError(
        selector="0xb783c88a",
        name="ThereMustBeAtLeastOneRoleAdmin",
        description="",
        params=(),
    ),
    "0x282b5b70": GmxError(
        selector="0x282b5b70",
        name="ThereMustBeAtLeastOneTimelockMultiSig",
        description="",
        params=(),
    ),
    "0x7344d981": GmxError(
        selector="0x7344d981",
        name="TokenPermitsNotAllowedForMultichain",
        description="",
        params=(),
    ),
    "0x979dc780": GmxError(
        selector="0x979dc780",
        name="TokenTransferError",
        description="",
        params=("address", "address", "uint256"),
    ),
    "0x0e92b837": GmxError(
        selector="0x0e92b837",
        name="Uint256AsBytesLengthExceeds32Bytes",
        description="",
        params=("uint256",),
    ),
    "0x6afad778": GmxError(
        selector="0x6afad778",
        name="UnableToGetBorrowingFactorEmptyPoolUsd",
        description="",
        params=(),
    ),
    "0xbe4729a2": GmxError(
        selector="0xbe4729a2",
        name="UnableToGetCachedTokenPrice",
        description="",
        params=("address", "address"),
    ),
    "0x11423d95": GmxError(
        selector="0x11423d95",
        name="UnableToGetFundingFactorEmptyOpenInterest",
        description="",
        params=(),
    ),
    "0x7a0ca681": GmxError(
        selector="0x7a0ca681",
        name="UnableToGetOppositeToken",
        description="",
        params=("address", "address"),
    ),
    "0x68fb0fed": GmxError(
        selector="0x68fb0fed",
        name="UnableToPayOrderFee",
        description="",
        params=(),
    ),
    "0xde27e626": GmxError(
        selector="0xde27e626",
        name="UnableToPayOrderFeeFromCollateral",
        description="",
        params=(),
    ),
    "0x3a61a4a9": GmxError(
        selector="0x3a61a4a9",
        name="UnableToWithdrawCollateral",
        description="Cannot withdraw the requested collateral amount",
        params=("int256",),
    ),
    "0xa35b150b": GmxError(
        selector="0xa35b150b",
        name="Unauthorized",
        description="Caller is not authorized to perform this action",
        params=("address", "string"),
    ),
    "0x99b2d582": GmxError(
        selector="0x99b2d582",
        name="UnexpectedBorrowingFactor",
        description="",
        params=("uint256", "uint256"),
    ),
    "0xcc3459ff": GmxError(
        selector="0xcc3459ff",
        name="UnexpectedMarket",
        description="",
        params=(),
    ),
    "0x3b42e952": GmxError(
        selector="0x3b42e952",
        name="UnexpectedPoolValue",
        description="",
        params=("int256",),
    ),
    "0x814991c3": GmxError(
        selector="0x814991c3",
        name="UnexpectedPositionState",
        description="",
        params=(),
    ),
    "0xe949114e": GmxError(
        selector="0xe949114e",
        name="UnexpectedRelayFeeToken",
        description="",
        params=("address", "address"),
    ),
    "0xa9721241": GmxError(
        selector="0xa9721241",
        name="UnexpectedRelayFeeTokenAfterSwap",
        description="",
        params=("address", "address"),
    ),
    "0x785ee469": GmxError(
        selector="0x785ee469",
        name="UnexpectedTokenForVirtualInventory",
        description="",
        params=("address", "address"),
    ),
    "0x3af14617": GmxError(
        selector="0x3af14617",
        name="UnexpectedValidFromTime",
        description="",
        params=("uint256",),
    ),
    "0x3784f834": GmxError(
        selector="0x3784f834",
        name="UnsupportedOrderType",
        description="Order type is not supported",
        params=("uint256",),
    ),
    "0x31f47690": GmxError(
        selector="0x31f47690",
        name="UnsupportedOrderTypeForAutoCancellation",
        description="",
        params=("uint256",),
    ),
    "0x0d0fcc0b": GmxError(
        selector="0x0d0fcc0b",
        name="UnsupportedRelayFeeToken",
        description="",
        params=("address", "address"),
    ),
    "0xeadaf93a": GmxError(
        selector="0xeadaf93a",
        name="UsdDeltaExceedsLongOpenInterest",
        description="",
        params=("int256", "uint256"),
    ),
    "0x2e949409": GmxError(
        selector="0x2e949409",
        name="UsdDeltaExceedsPoolValue",
        description="",
        params=("int256", "uint256"),
    ),
    "0x8af0d140": GmxError(
        selector="0x8af0d140",
        name="UsdDeltaExceedsShortOpenInterest",
        description="",
        params=("int256", "uint256"),
    ),
    "0x60737bc0": GmxError(
        selector="0x60737bc0",
        name="WithdrawalNotFound",
        description="",
        params=("bytes32",),
    ),
    "0xa3b900da": GmxError(
        selector="0xa3b900da",
        name="ZeroTreasuryAddress",
        description="",
        params=(),
    ),
}


def decode_gmx_revert_selector(selector: str | None) -> GmxError | None:
    """Decode a 4-byte GMX V2 revert selector to its named error.

    Looks up the 4-byte selector in :data:`_GMX_ERROR_REGISTRY` and returns
    the matching :class:`GmxError`. The input may be either:

    * A bare selector hex string with or without ``0x`` prefix
      (``"0x839c693e"``, ``"839c693e"``).
    * A longer revert-data hex string — only the first 4 bytes after the
      optional ``0x`` are inspected (the rest is ABI-encoded parameters).
    * Mixed case (``"0x839C693E"``) — input is lower-cased before lookup.

    :param selector: 4-byte hex string, case-insensitive. May also be a
        longer revert-data hex string; only the first 4 bytes after the
        optional ``0x`` are inspected.
    :returns: :class:`GmxError` if the selector is known; ``None`` for any
        unknown / falsy / malformed input.
    """
    if not selector:
        return None
    s = selector.lower()
    if s.startswith("0x"):
        s = s[2:]
    if len(s) < 8:
        return None
    return _GMX_ERROR_REGISTRY.get("0x" + s[:8])


__all__ = ["GmxError", "decode_gmx_revert_selector"]
