"""Fetch GMX volumes by using onchain data.

Example how position events in GMX are emitted:

.. code-block:: solidity

    function emitPositionIncrease(PositionIncreaseParams memory params) external {
        EventUtils.EventLogData memory eventData;

        eventData.addressItems.initItems(3);
        eventData.addressItems.setItem(0, "account", params.position.account());
        eventData.addressItems.setItem(1, "market", params.position.market());
        eventData.addressItems.setItem(2, "collateralToken", params.position.collateralToken());

        eventData.uintItems.initItems(16);
        eventData.uintItems.setItem(0, "sizeInUsd", params.position.sizeInUsd());
        eventData.uintItems.setItem(1, "sizeInTokens", params.position.sizeInTokens());
        eventData.uintItems.setItem(2, "collateralAmount", params.position.collateralAmount());
        eventData.uintItems.setItem(3, "borrowingFactor", params.position.borrowingFactor());
        eventData.uintItems.setItem(4, "fundingFeeAmountPerSize", params.position.fundingFeeAmountPerSize());
        eventData.uintItems.setItem(5, "longTokenClaimableFundingAmountPerSize", params.position.longTokenClaimableFundingAmountPerSize());
        eventData.uintItems.setItem(6, "shortTokenClaimableFundingAmountPerSize", params.position.shortTokenClaimableFundingAmountPerSize());
        eventData.uintItems.setItem(7, "executionPrice", params.executionPrice);
        eventData.uintItems.setItem(8, "indexTokenPrice.max", params.indexTokenPrice.max);
        eventData.uintItems.setItem(9, "indexTokenPrice.min", params.indexTokenPrice.min);
        eventData.uintItems.setItem(10, "collateralTokenPrice.max", params.collateralTokenPrice.max);
        eventData.uintItems.setItem(11, "collateralTokenPrice.min", params.collateralTokenPrice.min);
        eventData.uintItems.setItem(12, "sizeDeltaUsd", params.sizeDeltaUsd);
        eventData.uintItems.setItem(13, "sizeDeltaInTokens", params.sizeDeltaInTokens);
        eventData.uintItems.setItem(14, "orderType", uint256(params.orderType));
        eventData.uintItems.setItem(15, "increasedAtTime", uint256(params.position.increasedAtTime()));

        eventData.intItems.initItems(3);
        eventData.intItems.setItem(0, "collateralDeltaAmount", params.collateralDeltaAmount);
        eventData.intItems.setItem(1, "pendingPriceImpactUsd", params.priceImpactUsd);
        eventData.intItems.setItem(2, "pendingPriceImpactAmount", params.priceImpactAmount);

        eventData.boolItems.initItems(1);
        eventData.boolItems.setItem(0, "isLong", params.position.isLong());

        eventData.bytes32Items.initItems(2);
        eventData.bytes32Items.setItem(0, "orderKey", params.orderKey);
        eventData.bytes32Items.setItem(1, "positionKey", params.positionKey);

        params.eventEmitter.emitEventLog1(
            "PositionIncrease",
            Cast.toBytes32(params.position.account()),
            eventData
        );
    }

"""
from dataclasses import dataclass
from typing import TypeAlias

_event_structure = {
    "addressItems": {
        0: "account",
        1: "market",
        2: "collateralToken"
    },
    "uintItems": {
        0: "sizeInUsd",
        1: "sizeInTokens",
        2: "collateralAmount",
        3: "borrowingFactor",
        4: "fundingFeeAmountPerSize",
        5: "longTokenClaimableFundingAmountPerSize",
        6: "shortTokenClaimableFundingAmountPerSize",
        7: "executionPrice",
        8: "indexTokenPrice.max",
        9: "indexTokenPrice.min",
        10: "collateralTokenPrice.max",
        11: "collateralTokenPrice.min",
        12: "sizeDeltaUsd",
        13: "sizeDeltaInTokens",
        14: "orderType",
        15: "increasedAtTime"
    },
    "intItems": {
        0: "collateralDeltaAmount",
        1: "pendingPriceImpactUsd",
        2: "pendingPriceImpactAmount"
    },
    "bytes32Items": {
        0: "orderKey",
        1: "positionKey"
    },
    "boolItems": {
        0: "isLong"
    }
}



HexAddress: TypeAlias = str  # Assuming checksummed Ethereum address
SolidityUint: TypeAlias = int  # Unsigned integer, but Python int is fine
SolidityInt: TypeAlias = int   # Signed integer
HexBytes: TypeAlias = bytes    # Hex-encoded bytes


@dataclass(slots=True, frozen=True)
class PositionIncreaseEvent:
    account: HexAddress
    market: HexAddress
    collateral_token: HexAddress
    size_in_usd: SolidityUint
    size_in_tokens: SolidityUint
    collateral_amount: SolidityUint
    borrowing_factor: SolidityUint
    funding_fee_amount_per_size: SolidityUint
    long_token_claimable_funding_amount_per_size: SolidityUint
    short_token_claimable_funding_amount_per_size: SolidityUint
    execution_price: SolidityUint
    index_token_price_max: SolidityUint
    index_token_price_min: SolidityUint
    collateral_token_price_max: SolidityUint
    collateral_token_price_min: SolidityUint
    size_delta_usd: SolidityUint
    size_delta_in_tokens: SolidityUint
    order_type: SolidityUint
    increased_at_time: SolidityUint
    collateral_delta_amount: SolidityInt
    pending_price_impact_usd: SolidityInt
    pending_price_impact_amount: SolidityInt
    order_key: HexBytes
    position_key: HexBytes
    is_long: bool


