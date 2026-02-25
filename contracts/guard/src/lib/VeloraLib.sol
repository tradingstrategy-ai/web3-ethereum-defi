// Velora (ParaSwap) atomic swap guard logic as an external Forge library.
//
// Extracts Velora-specific storage, whitelisting, and slippage verification
// out of the main guard contract to reduce its deployed bytecode size
// (EIP-170 limit). Uses diamond storage for the Velora whitelist state.
//
// External library functions are called via DELEGATECALL, meaning:
//   - Code lives in the deployed library (does NOT count toward the
//     calling contract's 24 KB EIP-170 limit)
//   - Storage reads/writes happen in the calling contract's context
//
// Validation functions that need main-contract state (isAllowedReceiver,
// isAllowedAsset, isAllowedSender) use IGuardChecks callbacks via
// address(this) to check permissions on the calling contract's storage.

pragma solidity ^0.8.0;

import {IERC20} from "./IERC20.sol";
import {IGuardChecks} from "./IGuardChecks.sol";

library VeloraLib {

    // ----- Diamond storage -----

    bytes32 constant STORAGE_SLOT = keccak256("eth_defi.velora.v1");

    struct VeloraStorage {
        mapping(address => bool) allowedVeloraSwappers;
    }

    // ----- Events -----

    event VeloraSwapperApproved(address augustusSwapper, string notes);

    // Emitted after a successful atomic swap via Augustus Swapper
    event VeloraSwapExecuted(
        uint256 indexed timestamp,
        address indexed augustusSwapper,
        address tokenIn,
        address tokenOut,
        uint256 amountIn,
        uint256 amountOut,
        uint256 minAmountOut
    );

    function _storage() internal pure returns (VeloraStorage storage s) {
        bytes32 slot = STORAGE_SLOT;
        assembly { s.slot := slot }
    }

    // ----- Deployment check -----

    /// Returns true when the library is properly linked.
    /// A DELEGATECALL to ZERO_ADDRESS silently returns zero bytes,
    /// so calling this function and requiring a true result catches
    /// missing library links at runtime with a human-readable error.
    function isDeployed() external pure returns (bool) {
        return true;
    }

    // ----- Whitelisting functions -----

    function whitelistVelora(
        address augustusSwapper,
        string calldata notes
    ) external {
        _storage().allowedVeloraSwappers[augustusSwapper] = true;
        emit VeloraSwapperApproved(augustusSwapper, notes);
    }

    function isAllowedVeloraSwapper(address swapper) external view returns (bool) {
        return _storage().allowedVeloraSwappers[swapper];
    }

    // ----- Post-swap verification -----

    /// Validate permissions and compute pre-swap balance in one call.
    ///
    /// Consolidates sender/receiver/token/swapper validation and balance
    /// lookup into a single library function to reduce the calling
    /// contract's bytecode (EIP-170). Uses IGuardChecks callbacks via
    /// address(this) to check permissions on the calling contract's storage.
    function validateAndGetPreBalance(
        address safeAddress,
        address augustusSwapper,
        address receiver,
        address tokenIn,
        address tokenOut
    ) external view returns (uint256) {
        require(_storage().allowedVeloraSwappers[augustusSwapper], "Velora swapper not enabled");

        IGuardChecks guard = IGuardChecks(address(this));
        require(guard.isAllowedSender(msg.sender), "Sender not allowed");
        require(guard.isAllowedReceiver(receiver), "Receiver not allowed");
        require(guard.isAllowedAsset(tokenIn), "tokenIn not allowed");
        require(guard.isAllowedAsset(tokenOut), "tokenOut not allowed");

        return IERC20(tokenOut).balanceOf(safeAddress);
    }

    /// Verify slippage after Velora swap execution and emit event.
    ///
    /// Call this after executing the swap calldata on the Safe.
    /// Checks that the Safe received at least minAmountOut of tokenOut.
    function verifySlippageAndEmit(
        address safeAddress,
        address augustusSwapper,
        address tokenIn,
        address tokenOut,
        uint256 amountIn,
        uint256 minAmountOut,
        uint256 preBalance
    ) external {
        uint256 postBalance = IERC20(tokenOut).balanceOf(safeAddress);
        require(
            postBalance >= preBalance + minAmountOut,
            "Insufficient output amount"
        );

        emit VeloraSwapExecuted(
            block.timestamp,
            augustusSwapper,
            tokenIn,
            tokenOut,
            amountIn,
            postBalance - preBalance,
            minAmountOut
        );
    }
}
