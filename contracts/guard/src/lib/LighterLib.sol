// Lighter (zk-rollup perps DEX on Ethereum L1) guard logic - library contract.
//
// Same diamond-storage + DELEGATECALL pattern as GmxLib / HypercoreVaultLib:
//   - Code lives in the deployed library (does NOT count toward the guard's
//     24 KB EIP-170 limit)
//   - Storage reads/writes happen in the calling guard's context
//   - Validation that needs guard state (isAllowedReceiver) uses IGuardChecks
//     callbacks via address(this) — exactly like GmxLib
//
// Protocol-specific allow-sets (allowed ZkLighter contract, allowed asset
// index) live in the library's own diamond storage — analogous to GmxLib's
// allowedRouters / allowedMarkets.
//
// Scope: the on-chain L1 custody flow only (deposit / withdraw /
// withdrawPendingBalance). Account registration is off-chain (EIP-712 /
// EIP-1271 Safe signature). changePubKey (trading-key rotation) and createOrder
// (on-book trading) are intentionally out of scope.
//
// See eth_defi/lighter/README-lighter-guard.md for full documentation.

pragma solidity ^0.8.0;

import {IGuardChecks} from "./IGuardChecks.sol";

// Pre-computed Lighter L1 (ZkLighter) function selectors
bytes4 constant SEL_LIGHTER_DEPOSIT = 0x8a857083; // deposit(address,uint16,uint8,uint256)
bytes4 constant SEL_LIGHTER_WITHDRAW = 0xd20191bd; // withdraw(uint48,uint16,uint8,uint64)
bytes4 constant SEL_LIGHTER_WITHDRAW_PENDING = 0x2f25807e; // withdrawPendingBalance(address,uint16,uint128)
// SEL_LIGHTER_CHANGE_PUBKEY (0x17010c68) intentionally NOT defined/whitelisted
// here — trading-key rotation is a follow-up needing an account-index policy.

library LighterLib {
    // ----- Diamond storage -----

    bytes32 constant STORAGE_SLOT = keccak256("eth_defi.lighter.v1");

    struct LighterStorage {
        // ZkLighter L1 contract(s)            (cf. GmxLib.allowedRouters)
        mapping(address => bool) allowedContracts;
        // Allowed deposit/withdraw asset index, e.g. USDC (cf. GmxLib.allowedMarkets)
        mapping(uint16 => bool) allowedAssetIndices;
    }

    function _storage() private pure returns (LighterStorage storage s) {
        bytes32 slot = STORAGE_SLOT;
        assembly {
            s.slot := slot
        }
    }

    // ----- Events -----

    event LighterContractApproved(address zkLighter, uint16 assetIndex, string notes);

    // ----- Deployment check -----

    /// @dev See IGuardLib.isDeployed()
    function isDeployed() external pure returns (bool) {
        return true;
    }

    // ----- Whitelisting functions (called via delegatecall from guard) -----

    function whitelistLighter(
        address zkLighter,
        uint16 assetIndex,
        string calldata notes
    ) external {
        LighterStorage storage s = _storage();
        s.allowedContracts[zkLighter] = true;
        s.allowedAssetIndices[assetIndex] = true;
        emit LighterContractApproved(zkLighter, assetIndex, notes);
    }

    // ----- View functions -----

    function isAllowedLighter(address zkLighter) external view returns (bool) {
        return _storage().allowedContracts[zkLighter];
    }

    // anyAsset is the guard-wide "allow all assets" joker — same semantics as
    // GmxLib.isAllowedMarket(market, anyAsset).
    function isAllowedAssetIndex(uint16 assetIndex, bool anyAsset) external view returns (bool) {
        return anyAsset || _storage().allowedAssetIndices[assetIndex];
    }

    // ----- Validation -----

    /// Validate a Lighter L1 deposit/withdraw call.
    ///
    /// Dispatched from GuardV0Base for the three in-scope Lighter selectors.
    /// Receiver checks go through IGuardChecks (the guard's global receiver
    /// allowlist, set by allowReceiver(safe)) — same pattern as GmxLib.
    /// Asset-index checks are skipped when anyAsset is set (the "allow all
    /// assets" joker), exactly like GmxLib gates allowedMarkets on !anyAsset.
    ///
    /// @param selector The 4-byte function selector being called
    /// @param target The ZkLighter contract address
    /// @param callData The call payload without the selector
    /// @param anyAsset Whether all assets are allowed (skip asset-index check)
    function validateCall(
        bytes4 selector,
        address target,
        bytes calldata callData,
        bool anyAsset
    ) external view {
        LighterStorage storage s = _storage();
        require(s.allowedContracts[target], "Lighter contract not allowed");
        IGuardChecks guard = IGuardChecks(address(this));

        if (selector == SEL_LIGHTER_DEPOSIT) {
            // deposit(address _to, uint16 _assetIndex, uint8, uint256)
            (address to, uint16 assetIndex, , ) = abi.decode(callData, (address, uint16, uint8, uint256));
            require(guard.isAllowedReceiver(to), "Lighter deposit: receiver not whitelisted");
            if (!anyAsset) {
                require(s.allowedAssetIndices[assetIndex], "Lighter deposit: asset not allowed");
            }
        } else if (selector == SEL_LIGHTER_WITHDRAW_PENDING) {
            // withdrawPendingBalance(address _owner, uint16 _assetIndex, uint128)
            (address owner, uint16 assetIndex, ) = abi.decode(callData, (address, uint16, uint128));
            require(guard.isAllowedReceiver(owner), "Lighter withdraw: owner not whitelisted");
            if (!anyAsset) {
                require(s.allowedAssetIndices[assetIndex], "Lighter withdraw: asset not allowed");
            }
        } else if (selector == SEL_LIGHTER_WITHDRAW) {
            // withdraw(uint48 _accountIndex, uint16 _assetIndex, uint8, uint64)
            //
            // Not a fund-egress vector: moves the account's balance into its own
            // pending balance, no recipient parameter. The only L1 egress is
            // withdrawPendingBalance (above), which is receiver-checked to the
            // Safe. So _accountIndex is intentionally left unbound here.
            //
            // The ZkLighter contract binds the withdrawal to msg.sender, not to
            // the supplied _accountIndex: withdraw() sets
            //   masterAccountIndex = validateAndGetAccountIndexFromAddress(msg.sender)
            // (reverting AdditionalZkLighter_AccountIsNotRegistered() if the
            // caller has no account). So a compromised asset manager cannot
            // withdraw a foreign account through the guard. Verified end-to-end
            // by tests/guard/test_guard_lighter_lagoon.py
            // ::test_guard_lighter_withdraw_account_index_bound_by_protocol,
            // which asserts the guard permits the call but the protocol reverts
            // with that exact error.
            (, uint16 assetIndex, , ) = abi.decode(callData, (uint48, uint16, uint8, uint64));
            if (!anyAsset) {
                require(s.allowedAssetIndices[assetIndex], "Lighter withdraw: asset not allowed");
            }
        } else {
            revert("Lighter: unknown selector");
        }
    }
}
