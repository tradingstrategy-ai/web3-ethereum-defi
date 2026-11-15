// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

/// Verified Vault V2 surface, matching eth_defi/abi/morpho/VaultV2.json.
interface IMorphoRecoveryVault {
    function forceDeallocate(address adapter, bytes calldata data, uint256 assets, address onBehalf)
        external returns (uint256);
    function forceDeallocatePenalty(address adapter) external view returns (uint256);
    function isAdapter(address adapter) external view returns (bool);
    function balanceOf(address owner) external view returns (uint256);
    function previewRedeem(uint256 shares) external view returns (uint256);
}
interface IMorphoRecoveryAdapter {
    function parentVault() external view returns (address);
}

/**
 * Public liquidity recovery is a fee-paying share-owner operation, not an
 * allocator permission. Api3 and Purinta's whole-vault share price can exceed
 * their configured exit-market position (Blue Panic(0x11)); another registered
 * market can release existing assets into vault idle balance. See the detailed
 * case study in tradeexecutor/strategy/vault_protocol/morpho.py and research PR
 * https://github.com/tradingstrategy-ai/vault-notebooks/pull/25.
 *
 * Keep this external library outside the almost-full Safe module bytecode.
 * DELEGATECALL stores only owner-authorised per-vault rate caps in an isolated
 * guard storage slot. A return value makes an unlinked zero library fail closed.
 * The cap is per call, not cumulative: a compromised asset-manager key can
 * repeat a permitted fee-paying operation. This is not a loss-budget policy.
 * Revoking the call site disables execution but retains its configured cap;
 * governance should reconfigure the cap before deliberately re-enabling it.
 */
library MorphoLib {
    bytes32 private constant SLOT = keccak256("eth_defi.morpho.recovery.v1");
    struct Policy { mapping(address => uint256) capPlusOne; }
    function policy() private pure returns (Policy storage p) {
        bytes32 slot = SLOT;
        assembly { p.slot := slot }
    }
    function configure(address vault, uint256 maxFeeBps) external returns (bool) {
        require(maxFeeBps <= 10_000, "Invalid recovery cap");
        policy().capPlusOne[vault] = maxFeeBps + 1;
        return true;
    }
    function validate(address target, bytes calldata callData) external view returns (address owner) {
        (address adapter,, uint256 assets, address onBehalf) = abi.decode(callData, (address, bytes, uint256, address));
        uint256 cap = policy().capPlusOne[target];
        require(cap != 0, "Recovery not configured");
        IMorphoRecoveryVault vault = IMorphoRecoveryVault(target);
        require(vault.isAdapter(adapter) && IMorphoRecoveryAdapter(adapter).parentVault() == target, "Invalid recovery adapter");
        require(vault.forceDeallocatePenalty(adapter) <= (cap - 1) * 1e14, "Recovery fee exceeds cap");
        // Do not let the manager charge this custody owner to rebalance a
        // portfolio larger than its own position. Integer fee rounding remains
        // the protocol's responsibility and is also checked in receipt analysis.
        require(assets > 0 && assets <= vault.previewRedeem(vault.balanceOf(onBehalf)), "Recovery exceeds owner position");
        return onBehalf;
    }
}
