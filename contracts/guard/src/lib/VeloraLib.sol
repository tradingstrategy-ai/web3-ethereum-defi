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
//
// --- Security model ---
//
// Augustus calldata is opaque: the Velora API can return any of ~16 different
// Augustus function types (multiSwap, simpleSwap, megaSwap, protectedMultiSwap,
// swapOnUniswap, etc.) across V5/V6.2 with incompatible ABIs. On-chain
// decoding of all variants is impractical and fragile.
//
// Instead we use a balance-envelope approach:
//   - Pre-swap: record balances of BOTH tokenIn and tokenOut on the Safe
//   - Post-swap: verify tokenIn decreased by at most amountIn AND
//     tokenOut increased by at least minAmountOut (which must be > 0)
//   - This caps the maximum loss per transaction to amountIn of tokenIn
//     and ensures the Safe receives meaningful output
//
// Compare with CowSwap which constructs orders from validated params
// (no opaque blob) — Velora cannot do this due to the API-driven calldata.

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

    // Emitted after a successful atomic swap via Augustus Swapper.
    //
    // Includes BOTH declared parameters and actual balance changes so that
    // off-chain systems can measure slippage (declaredAmountIn vs actualAmountIn,
    // minAmountOut vs actualAmountOut).
    //
    // Actual values are measured from balance changes — they reflect what
    // really happened regardless of what the opaque Augustus calldata did.
    event VeloraSwapExecuted(
        uint256 indexed timestamp,
        address indexed augustusSwapper,
        address tokenIn,
        address tokenOut,
        uint256 declaredAmountIn,
        uint256 actualAmountIn,
        uint256 actualAmountOut,
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

    // ----- Pre-swap validation and balance snapshot -----

    /// Validate permissions and record pre-swap balances of both tokens.
    ///
    /// Consolidates sender/receiver/token/swapper validation and balance
    /// lookups into a single library function to reduce the calling
    /// contract's bytecode (EIP-170). Uses IGuardChecks callbacks via
    /// address(this) to check permissions on the calling contract's storage.
    ///
    /// Returns both balances so the post-swap check can verify:
    ///   - tokenIn did not decrease by more than the declared amountIn
    ///   - tokenOut increased by at least minAmountOut
    function validateAndGetPreBalances(
        address safeAddress,
        address augustusSwapper,
        address receiver,
        address tokenIn,
        address tokenOut
    ) external view returns (uint256 preBalanceIn, uint256 preBalanceOut) {
        require(_storage().allowedVeloraSwappers[augustusSwapper], "Velora swapper not enabled");

        IGuardChecks guard = IGuardChecks(address(this));
        require(guard.isAllowedSender(msg.sender), "Sender not allowed");
        require(guard.isAllowedReceiver(receiver), "Receiver not allowed");
        require(guard.isAllowedAsset(tokenIn), "tokenIn not allowed");
        require(guard.isAllowedAsset(tokenOut), "tokenOut not allowed");

        preBalanceIn = IERC20(tokenIn).balanceOf(safeAddress);
        preBalanceOut = IERC20(tokenOut).balanceOf(safeAddress);
    }

    // ----- Post-swap balance envelope verification -----

    /// Verify balance envelope after Velora swap execution and emit event.
    ///
    /// This is the core security check for opaque Augustus calldata.
    /// Because the calldata is not decoded (see file header for rationale),
    /// we verify the swap's effect by checking balance changes:
    ///
    ///   1. minAmountOut > 0: prevents vacuous checks where the calldata
    ///      routes funds to an attacker with zero accountability
    ///   2. tokenOut balance increased by at least minAmountOut: ensures
    ///      the Safe actually received meaningful swap output
    ///   3. tokenIn balance decreased by at most amountIn: caps the
    ///      maximum loss per transaction — the calldata cannot pull more
    ///      tokens than the caller declared
    ///
    /// The emitted event uses ACTUAL balance changes (not declared params)
    /// for amountIn and amountOut, ensuring accurate audit trails.
    function verifyBalancesAndEmit(
        address safeAddress,
        address augustusSwapper,
        address tokenIn,
        address tokenOut,
        uint256 amountIn,
        uint256 minAmountOut,
        uint256 preBalanceIn,
        uint256 preBalanceOut
    ) external {
        require(minAmountOut > 0, "minAmountOut must be positive");

        uint256 postBalanceOut = IERC20(tokenOut).balanceOf(safeAddress);
        require(
            postBalanceOut >= preBalanceOut + minAmountOut,
            "Insufficient output amount"
        );

        uint256 postBalanceIn = IERC20(tokenIn).balanceOf(safeAddress);
        require(
            preBalanceIn - postBalanceIn <= amountIn,
            "Token in overspent"
        );

        emit VeloraSwapExecuted(
            block.timestamp,
            augustusSwapper,
            tokenIn,
            tokenOut,
            amountIn,                       // declared by caller
            preBalanceIn - postBalanceIn,    // actual spent
            postBalanceOut - preBalanceOut,  // actual received
            minAmountOut                    // declared minimum
        );
    }
}
