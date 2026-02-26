// GMX V2 perpetuals guard logic as an external Forge library.
//
// Extracts GMX-specific storage, whitelisting, and validation out of the
// main guard contract to reduce its deployed bytecode size (EIP-170 limit).
// Uses diamond storage for the GMX whitelist state.
//
// External library functions are called via DELEGATECALL, meaning:
//   - Code lives in the deployed library (does NOT count toward the
//     calling contract's 24 KB EIP-170 limit)
//   - Storage reads/writes happen in the calling contract's context
//
// Validation functions that need main-contract state (isAllowedAsset,
// isAllowedReceiver) use IGuardChecks callbacks via address(this) to
// check permissions on the calling contract's storage.

pragma solidity ^0.8.0;

import "./IGmxV2.sol";
import {BytesLib} from "./BytesLib.sol";
import {IGuardChecks} from "./IGuardChecks.sol";

// Pre-computed GMX function selectors
bytes4 constant SEL_GMX_MULTICALL = 0xac9650d8;  // multicall(bytes[])
bytes4 constant SEL_GMX_SEND_WNT = 0x7d39aaf1;  // sendWnt(address,uint256)
bytes4 constant SEL_GMX_SEND_TOKENS = 0xe6d66ac8;  // sendTokens(address,address,uint256)
bytes4 constant SEL_GMX_CREATE_ORDER = 0xf59c48eb;  // createOrder(tuple)

library GmxLib {

    using BytesLib for bytes;

    // Diamond storage slot for GMX state
    bytes32 constant STORAGE_SLOT = keccak256("eth_defi.gmx.v1");

    struct GmxStorage {
        mapping(address => bool) allowedRouters;
        mapping(address => address) orderVaults;
        mapping(address => bool) allowedMarkets;
    }

    // ----- Events -----

    event GMXRouterApproved(address exchangeRouter, address syntheticsRouter, string notes);
    event GMXMarketApproved(address market, string notes);
    event GMXMarketRemoved(address market, string notes);

    function _storage() private pure returns (GmxStorage storage s) {
        bytes32 slot = STORAGE_SLOT;
        assembly { s.slot := slot }
    }

    // ----- Deployment check -----

    /// @dev See IGuardLib.isDeployed()
    function isDeployed() external pure returns (bool) {
        return true;
    }

    // ----- Whitelisting functions -----

    function whitelistRouter(
        address exchangeRouter,
        address syntheticsRouter,
        address orderVault,
        string calldata notes
    ) external {
        GmxStorage storage s = _storage();
        s.allowedRouters[exchangeRouter] = true;
        s.orderVaults[exchangeRouter] = orderVault;
        emit GMXRouterApproved(exchangeRouter, syntheticsRouter, notes);
    }

    function whitelistMarket(
        address market,
        string calldata notes
    ) external {
        _storage().allowedMarkets[market] = true;
        emit GMXMarketApproved(market, notes);
    }

    function removeMarket(
        address market,
        string calldata notes
    ) external {
        _storage().allowedMarkets[market] = false;
        emit GMXMarketRemoved(market, notes);
    }

    // ----- View functions -----

    function isAllowedRouter(address router) external view returns (bool) {
        return _storage().allowedRouters[router];
    }

    function isAllowedMarket(address market, bool anyAsset) external view returns (bool) {
        return anyAsset || _storage().allowedMarkets[market];
    }

    function getOrderVault(address exchangeRouter) external view returns (address) {
        return _storage().orderVaults[exchangeRouter];
    }

    // ----- Validation -----

    /// Validate a GMX multicall payload with full permission checks.
    ///
    /// Decodes the multicall bytes[], validates each inner call, and checks
    /// assets/receivers/markets using IGuardChecks callbacks via address(this).
    /// This consolidates all GMX validation into the library, reducing the
    /// calling contract's bytecode (EIP-170).
    ///
    /// @param exchangeRouter The GMX ExchangeRouter address
    /// @param callData The multicall calldata (bytes[])
    /// @param anyAsset Whether all assets are allowed
    function validateMulticall(
        address exchangeRouter,
        bytes calldata callData,
        bool anyAsset
    ) external view {
        GmxStorage storage s = _storage();
        require(s.allowedRouters[exchangeRouter], "GMX router not allowed");

        address orderVault = s.orderVaults[exchangeRouter];
        require(orderVault != address(0), "GMX orderVault not configured");

        IGuardChecks guard = IGuardChecks(address(this));

        // Decode multicall bytes array
        bytes[] memory calls = abi.decode(callData, (bytes[]));

        for (uint256 i = 0; i < calls.length; i++) {
            require(calls[i].length >= 4, "GMX: call too short");
            bytes4 selector = bytes4(calls[i][0]) | (bytes4(calls[i][1]) >> 8) | (bytes4(calls[i][2]) >> 16) | (bytes4(calls[i][3]) >> 24);
            bytes memory innerCallData = calls[i].slice(4, calls[i].length - 4);

            if (selector == SEL_GMX_SEND_WNT) {
                (address receiver, ) = abi.decode(innerCallData, (address, uint256));
                require(receiver == orderVault, "GMX sendWnt: invalid receiver");
            } else if (selector == SEL_GMX_SEND_TOKENS) {
                (address token, address receiver, ) = abi.decode(innerCallData, (address, address, uint256));
                require(receiver == orderVault, "GMX sendTokens: invalid receiver");
                if (!anyAsset) {
                    require(guard.isAllowedAsset(token), "GMX: asset not allowed");
                }
            } else if (selector == SEL_GMX_CREATE_ORDER) {
                CreateOrderParams memory params = abi.decode(innerCallData, (CreateOrderParams));
                require(guard.isAllowedReceiver(params.addresses.receiver), "GMX: receiver not allowed");
                require(guard.isAllowedReceiver(params.addresses.cancellationReceiver), "GMX: receiver not allowed");
                if (!anyAsset) {
                    require(s.allowedMarkets[params.addresses.market], "GMX: market not allowed");
                    require(guard.isAllowedAsset(params.addresses.initialCollateralToken), "GMX: asset not allowed");
                    for (uint256 j = 0; j < params.addresses.swapPath.length; j++) {
                        require(s.allowedMarkets[params.addresses.swapPath[j]], "GMX: market not allowed");
                    }
                }
            } else {
                revert("GMX: Unknown function in multicall");
            }
        }
    }
}
