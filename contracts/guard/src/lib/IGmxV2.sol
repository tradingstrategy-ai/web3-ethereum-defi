// SPDX-License-Identifier: BUSL-1.1
pragma solidity ^0.8.0;

// GMX Synthetics V2 - CreateOrderParams struct definitions
//
// Copied from GMX ExchangeRouter.createOrder parameter types.
// Source: https://github.com/gmx-io/gmx-synthetics
//
// These structs must match the exact ABI layout of ExchangeRouter.createOrder().
// Selector: 0xf59c48eb
//
// Defined at file-level (not inside an interface) so they can be used
// directly with abi.decode() in the Guard contract.

struct CreateOrderParamsAddresses {
    address receiver;
    address cancellationReceiver;
    address callbackContract;
    address uiFeeReceiver;
    address market;
    address initialCollateralToken;
    address[] swapPath;
}

struct CreateOrderParamsNumbers {
    uint256 sizeDeltaUsd;
    uint256 initialCollateralDeltaAmount;
    uint256 triggerPrice;
    uint256 acceptablePrice;
    uint256 executionFee;
    uint256 callbackGasLimit;
    uint256 minOutputAmount;
    uint256 validFromTime;
}

struct CreateOrderParams {
    CreateOrderParamsAddresses addresses;
    CreateOrderParamsNumbers numbers;
    uint8 orderType;
    uint8 decreasePositionSwapType;
    bool isLong;
    bool shouldUnwrapNativeToken;
    bool autoCancel;
    bytes32 referralCode;
    bytes32[] dataList;
}
