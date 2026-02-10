// Velora (ParaSwap) atomic swap execution
//
// Unlike CowSwap which uses offchain order books and presigning,
// Velora executes atomically by calling Augustus Swapper directly.
//
// https://developers.velora.xyz

pragma solidity ^0.8.13;

import {IERC20} from "./IERC20.sol";


// Velora swap validation and execution helper
//
// This library contains the validation logic for Velora swaps.
// The actual execution must happen through the Safe/Module.
//
library SwapVelora {

    // Velora swap execution event - emitted after successful atomic swap
    event VeloraSwapExecuted(
        uint256 indexed timestamp,
        address indexed augustusSwapper,
        address tokenIn,
        address tokenOut,
        uint256 amountIn,
        uint256 amountOut,
        uint256 minAmountOut
    );

    // Data structure for Velora swap parameters
    struct VeloraSwapParams {
        address augustusSwapper;
        address tokenIn;
        address tokenOut;
        uint256 amountIn;
        uint256 minAmountOut;
        bytes augustusCalldata;
    }

    // Validate and compute pre-swap balance
    //
    // Returns the pre-swap balance of tokenOut for slippage verification
    function validateAndGetPreBalance(
        address safeAddress,
        address tokenOut
    ) internal view returns (uint256) {
        return IERC20(tokenOut).balanceOf(safeAddress);
    }

    // Verify slippage after swap execution and emit event
    //
    // Call this after executing the swap calldata on the Safe
    function verifyAndEmit(
        address safeAddress,
        VeloraSwapParams memory params,
        uint256 preBalance
    ) internal {
        uint256 postBalance = IERC20(params.tokenOut).balanceOf(safeAddress);
        require(
            postBalance >= preBalance + params.minAmountOut,
            "Insufficient output amount"
        );

        emit VeloraSwapExecuted(
            block.timestamp,
            params.augustusSwapper,
            params.tokenIn,
            params.tokenOut,
            params.amountIn,
            postBalance - preBalance,
            params.minAmountOut
        );
    }
}
