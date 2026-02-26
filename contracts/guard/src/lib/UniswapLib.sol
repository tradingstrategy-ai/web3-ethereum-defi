// Uniswap V2/V3 swap guard logic as an external Forge library.
//
// Extracts Uniswap-specific validation out of the main guard contract
// to reduce its deployed bytecode size (EIP-170 limit).
//
// External library functions are called via DELEGATECALL, meaning:
//   - Code lives in the deployed library (does NOT count toward the
//     calling contract's 24 KB EIP-170 limit)
//   - Storage reads/writes happen in the calling contract's context
//
// Validation functions that need main-contract state (isAllowedReceiver,
// isAllowedAsset) use IGuardChecks callbacks via address(this) to check
// permissions on the calling contract's storage.

pragma solidity ^0.8.0;

import "./Path.sol";
import {BytesLib} from "./BytesLib.sol";
import {IGuardChecks} from "./IGuardChecks.sol";

library UniswapLib {

    using Path for bytes;
    using BytesLib for bytes;

    // ----- Deployment check -----

    /// @dev See IGuardLib.isDeployed()
    function isDeployed() external pure returns (bool) {
        return true;
    }

    // ----- Structs (match Uniswap router ABIs) -----

    struct ExactInputParams {
        bytes path;
        address recipient;
        uint256 deadline;
        uint256 amountIn;
        uint256 amountOutMinimum;
    }

    struct ExactOutputParams {
        bytes path;
        address recipient;
        uint256 deadline;
        uint256 amountOut;
        uint256 amountInMaximum;
    }

    // SwapRouter02 (IV3SwapRouter) uses a different struct layout
    // with no `deadline` field, unlike the original SwapRouter.
    struct ExactInputParamsRouter02 {
        bytes path;
        address recipient;
        uint256 amountIn;
        uint256 amountOutMinimum;
    }

    // ----- Validation -----

    /// Validate a Uniswap V2 swapExactTokensForTokens call.
    ///
    /// Checks the swap recipient and every token in the path.
    function validateSwapV2(bytes calldata callData) external view {
        IGuardChecks guard = IGuardChecks(address(this));
        (, , address[] memory path, address to, ) = abi.decode(
            callData,
            (uint256, uint256, address[], address, uint256)
        );
        require(guard.isAllowedReceiver(to), "Receiver not whitelisted");
        for (uint256 i = 0; i < path.length; i++) {
            require(guard.isAllowedAsset(path[i]), "Token not allowed");
        }
    }

    /// Validate a Uniswap V3 exactInput call (original SwapRouter).
    function validateExactInput(bytes calldata callData) external view {
        IGuardChecks guard = IGuardChecks(address(this));
        (ExactInputParams memory params) = abi.decode(
            callData,
            (ExactInputParams)
        );
        require(guard.isAllowedReceiver(params.recipient), "Receiver not whitelisted");
        _validateV3Path(guard, params.path);
    }

    /// Validate a Uniswap V3 exactOutput call (original SwapRouter).
    function validateExactOutput(bytes calldata callData) external view {
        IGuardChecks guard = IGuardChecks(address(this));
        (ExactOutputParams memory params) = abi.decode(
            callData,
            (ExactOutputParams)
        );
        require(guard.isAllowedReceiver(params.recipient), "Receiver not whitelisted");
        _validateV3Path(guard, params.path);
    }

    /// Validate a SwapRouter02 exactInput call (no deadline field).
    ///
    /// When anyAsset is true, token path validation is skipped (the caller
    /// has already verified that anyAsset mode is enabled).
    function validateExactInputRouter02(
        bytes calldata callData,
        bool anyAsset
    ) external view {
        IGuardChecks guard = IGuardChecks(address(this));
        (ExactInputParamsRouter02 memory params) = abi.decode(
            callData,
            (ExactInputParamsRouter02)
        );
        require(guard.isAllowedReceiver(params.recipient), "Receiver not whitelisted");
        if (!anyAsset) {
            _validateV3Path(guard, params.path);
        }
    }

    /// Validate every token in a Uniswap V3 encoded path.
    function _validateV3Path(IGuardChecks guard, bytes memory path) private view {
        while (true) {
            (address tokenOut, address tokenIn, ) = path.decodeFirstPool();
            require(guard.isAllowedAsset(tokenIn), "Token not allowed");
            require(guard.isAllowedAsset(tokenOut), "Token not allowed");
            if (path.hasMultiplePools()) {
                path = path.skipToken();
            } else {
                break;
            }
        }
    }
}
