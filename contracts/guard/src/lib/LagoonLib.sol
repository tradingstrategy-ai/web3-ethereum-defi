// Lagoon v0.5 settlement guard logic as an external Forge library.
//
// Background
// ----------
//
// A Lagoon vault collects pending deposit assets in a Silo and pending redeem
// shares in the vault's accounting. Once a valuation has been posted, the Safe
// calls settleDeposit(uint256) or settleRedeem(uint256) on the vault. Stock
// Lagoon v0.5 settles the whole snapshotted epoch; its public interface does
// not offer the asset manager a partial-settlement amount argument.
//
// This guard deliberately uses a reject approach instead of attempting to
// reproduce Lagoon's private queue accounting. GuardV0Base call validation
// takes a snapshot before the module executes the Safe call, and the module
// asks this library to validate the resulting ERC-20 balance changes
// afterwards. If the gross movement is over the configured limit, verification
// reverts. EVM atomicity then rolls back the Safe call, Lagoon state updates,
// token transfers and event logs.
// Thus the epoch is either settled in full below the limit or not settled at
// all; this library never creates a partial settlement.
//
// Balance invariant
// -----------------
//
// For Lagoon v0.5, a deposit settlement moves underlying assets from the
// pending Silo to the Safe, while a redeem settlement moves underlying assets
// from the Safe to the vault contract. The Safe itself is the module's avatar,
// so its balance is not needed to distinguish the two flows. The guarded gross
// settlement amount is instead calculated as:
//
//   deposit assets = Silo balance before - Silo balance after
//   redeem assets  = vault balance after - vault balance before
//   gross amount   = deposit assets + redeem assets
//
// The monotonicity checks are intentional. An increased Silo balance or a
// decreased vault balance does not match stock Lagoon v0.5 settlement and is
// rejected instead of being interpreted as a negative delta. The gross sum
// also prevents simultaneous deposit and redemption flows from netting each
// other out. settleDeposit(uint256) can perform both flows in one call.
//
// The configured amount is expressed in the underlying token's raw units. The
// implementation assumes a conventional ERC-20 whose balanceOf() reflects the
// transferred quantity. Fee-on-transfer, rebasing and callback-driven tokens
// are outside the intended Lagoon vault asset model.
//
// Library deployment and storage
// ------------------------------
//
// External library functions execute with DELEGATECALL. Library code therefore
// stays outside the calling guard/module's EIP-170 bytecode budget, while state
// reads and writes happen in the caller's storage. A deterministic diamond
// storage slot isolates Lagoon configuration from GuardV0Base storage.
//
// The caller must verify isDeployed() before invoking this library. A linked
// zero address can otherwise make a void-returning DELEGATECALL look successful
// while doing nothing. GuardV0Base and TradingStrategyModuleV0 both perform
// this fail-closed check on their respective validation and execution paths.

pragma solidity ^0.8.0;

import {IERC20} from "./IERC20.sol";

// ----- Lagoon settlement selectors -----

// Keep both legacy no-argument selectors and the v0.5 valuation-argument
// selectors. This preserves the existing Lagoon allowlist surface while the
// same post-execution limit is applied to every recognised settlement call.
bytes4 constant SEL_SETTLE_DEPOSIT = 0x559ec80d; // settleDeposit()
bytes4 constant SEL_SETTLE_REDEEM = 0xa03d55e3; // settleRedeem()
bytes4 constant SEL_SETTLE_DEPOSIT_UINT = 0xd24ca58a; // settleDeposit(uint256)
bytes4 constant SEL_SETTLE_REDEEM_UINT = 0xa627df66; // settleRedeem(uint256)

/// Minimal stock Lagoon v0.5 interface needed to bind a configured asset.
///
/// Reading asset() from the vault prevents governance configuration from
/// accidentally measuring an unrelated ERC-20 contract. No larger Lagoon ABI
/// is imported because the library does not call settlement itself.
interface ILagoonVaultV05 {
    function asset() external view returns (address);
}

/// Store the paired Lagoon vault configuration and enforce settlement limits.
///
/// Configuration and pre-execution snapshot functions are called through
/// GuardV0Base. Post-call validation is called by TradingStrategyModuleV0
/// after execution of an asset-manager transaction through the Safe.
///
/// A Lagoon deployment always pairs exactly one vault, one Safe and one
/// TradingStrategyModuleV0 guard. Unlike protocol libraries which allow many
/// routers or markets, Lagoon configuration is therefore a singleton. The
/// vault address remains an argument to validation functions so the library
/// can reject calls whose target is not the paired vault.
library LagoonLib {

    // ----- Diamond storage -----

    // Namespace version v1 describes this library's storage layout. It is
    // independent of GuardV0Base.getInternalVersion(), which describes the
    // public guard implementation version. Never change the meaning or order
    // of existing LagoonStorage fields without migrating this namespace.
    bytes32 constant STORAGE_SLOT = keccak256("eth_defi.lagoon.v1");

    /// Singleton Lagoon configuration stored in the calling guard/module.
    struct LagoonStorage {
        // The only Lagoon vault paired with this guard module and Safe.
        address vault;

        // Whether captureSettlement()/verifySettlement() enforce the cap.
        // Legacy whitelistLagoon() entries leave this false.
        bool limitEnabled;

        // Lagoon underlying token whose raw balance deltas are measured.
        address asset;

        // Lagoon Silo holding assets queued for the snapshotted deposit epoch.
        address pendingSilo;

        // Maximum accepted gross settlement in raw underlying-token units.
        // Zero is a valid strict cap: only a zero-asset settlement can pass.
        uint256 maxSettlementAmount;
    }

    /// Transient pre-execution values used for atomic post-call verification.
    ///
    /// This struct is passed through module memory/calldata only. It is not
    /// persisted in diamond storage and cannot leak between Safe transactions.
    struct SettlementSnapshot {
        // False for backwards-compatible uncapped vaults, allowing a cheap
        // no-op verification path.
        bool limitEnabled;

        // ERC-20 balance source copied from LagoonStorage before execution.
        address asset;

        // Deposit queue balance holder copied from LagoonStorage.
        address pendingSilo;

        // Cap copied before execution, so verification uses one coherent
        // configuration even if the implementation evolves later.
        uint256 maxSettlementAmount;

        // Underlying asset balance held by pendingSilo before settlement.
        uint256 siloBalanceBefore;

        // Underlying asset balance held by the Lagoon vault before settlement.
        uint256 vaultBalanceBefore;
    }

    // ----- Configuration and validation errors -----

    /// A required Lagoon configuration address was the zero address.
    error LagoonInvalidAddress(address invalidAddress);

    /// A configured vault, token or Silo address was not a contract.
    error LagoonAddressHasNoCode(address invalidAddress);

    /// The configured token did not equal the vault's canonical asset().
    error LagoonAssetMismatch(address configuredAsset, address vaultAsset);

    /// The Silo did not approve the vault to pull queued deposit assets.
    error LagoonSiloAllowanceMissing(address pendingSilo, address vault);

    /// A settlement was attempted against a Lagoon vault not on the allowlist.
    error LagoonVaultNotAllowed(address vault);

    /// The measured deposit-plus-redemption movement was above the cap.
    error LagoonSettlementLimitExceeded(uint256 actualAmount, uint256 maxAmount);

    /// The Silo moved in the opposite direction to a stock v0.5 settlement.
    error LagoonSiloBalanceIncreased(uint256 beforeBalance, uint256 afterBalance);

    /// The vault moved in the opposite direction to a stock v0.5 settlement.
    error LagoonVaultBalanceDecreased(uint256 beforeBalance, uint256 afterBalance);

    // ----- Configuration and audit events -----

    /// Preserve the original GuardV0 Lagoon approval event for indexers.
    event LagoonVaultApproved(address vault, string notes);

    /// Record the full settlement-limit configuration in an indexable event.
    event LagoonSettlementLimitSet(
        address indexed vault,
        address indexed asset,
        address indexed pendingSilo,
        uint256 maxSettlementAmount,
        bool enabled,
        string notes
    );

    /// Record a successful post-execution settlement measurement.
    ///
    /// This event is absent for rejected settlements because the outer EVM
    /// revert rolls back all logs. Rejection is observable through the custom
    /// LagoonSettlementLimitExceeded error and failed transaction receipt.
    event LagoonSettlementValidated(
        address indexed vault,
        uint256 depositAssets,
        uint256 redeemAssets,
        uint256 grossSettlementAmount,
        uint256 maxSettlementAmount
    );

    /// Resolve this library's namespaced storage in the caller's context.
    ///
    /// Solidity libraries invoked externally use DELEGATECALL, so s.slot
    /// points into GuardV0Base/TradingStrategyModuleV0 storage rather than the
    /// deployed LagoonLib contract. The keccak namespace avoids collisions.
    ///
    /// @return s Lagoon diamond-storage root.
    function _storage() private pure returns (LagoonStorage storage s) {
        bytes32 slot = STORAGE_SLOT;
        assembly { s.slot := slot }
    }

    // ----- Deployment check -----

    /// Prove that executable LagoonLib bytecode exists at the linked address.
    ///
    /// Guard callers require the true return value before any important
    /// delegatecall. Calling the same selector on an address without bytecode
    /// returns no ABI-decodable bool and therefore fails closed.
    ///
    /// @return Always true when this function executes from deployed code.
    function isDeployed() external pure returns (bool) {
        return true;
    }

    // ----- Governance configuration -----

    /// Allowlist a Lagoon vault without a settlement amount limit.
    ///
    /// This is the backwards-compatible route used by existing deployments.
    /// Reapplying it to a previously capped vault intentionally clears all cap
    /// metadata, providing an explicit governance-controlled way to disable
    /// enforcement while preserving the original whitelist API and event.
    ///
    /// @param vault Lagoon vault whose settlement selectors will be permitted.
    /// @param notes Human-readable governance audit note.
    function whitelistVault(address vault, string calldata notes) external {
        if (vault == address(0)) revert LagoonInvalidAddress(vault);

        LagoonStorage storage config = _storage();
        config.vault = vault;
        config.limitEnabled = false;
        config.asset = address(0);
        config.pendingSilo = address(0);
        config.maxSettlementAmount = 0;
        emit LagoonVaultApproved(vault, notes);
    }

    /// Allowlist a Lagoon vault and enable a raw underlying-asset cap.
    ///
    /// The function validates the relationship between vault, asset and Silo
    /// before storing configuration. GuardV0Base separately allowlists all
    /// supported settlement call sites after this delegatecall succeeds.
    ///
    /// @param vault Stock Lagoon vault to allowlist.
    /// @param asset Vault underlying ERC-20 returned by vault.asset().
    /// @param pendingSilo Vault-specific Lagoon Silo holding queued deposits.
    /// @param maxSettlementAmount Maximum gross asset movement in raw units.
    /// @param notes Human-readable governance audit note.
    function whitelistVaultWithSettlementLimit(
        address vault,
        address asset,
        address pendingSilo,
        uint256 maxSettlementAmount,
        string calldata notes
    ) external {
        _validateConfiguration(vault, asset, pendingSilo);

        LagoonStorage storage config = _storage();
        config.vault = vault;
        config.limitEnabled = true;
        config.asset = asset;
        config.pendingSilo = pendingSilo;
        config.maxSettlementAmount = maxSettlementAmount;

        emit LagoonVaultApproved(vault, notes);
        emit LagoonSettlementLimitSet(
            vault,
            asset,
            pendingSilo,
            maxSettlementAmount,
            true,
            notes
        );
    }

    // ----- Configuration reads -----

    /// Return whether a Lagoon vault is on the settlement allowlist.
    ///
    /// @param vault Lagoon vault address to inspect.
    /// @return True when settlement calls have been allowed by governance.
    function isAllowedVault(address vault) external view returns (bool) {
        address configuredVault = _storage().vault;
        return configuredVault != address(0) && configuredVault == vault;
    }

    /// Return the complete stored Lagoon configuration for a vault.
    ///
    /// Keeping this as an explicit getter preserves access to namespaced
    /// library state and preserves the existing GuardV0Base query ABI.
    ///
    /// @param vault Lagoon vault address to inspect.
    /// @return allowed Whether the vault is allowlisted.
    /// @return limitEnabled Whether atomic settlement validation is enabled.
    /// @return asset Underlying ERC-20 measured by the validator.
    /// @return pendingSilo Lagoon pending-deposit Silo being measured.
    /// @return maxSettlementAmount Gross cap in raw asset units.
    function getVaultConfig(
        address vault
    ) external view returns (
        bool allowed,
        bool limitEnabled,
        address asset,
        address pendingSilo,
        uint256 maxSettlementAmount
    ) {
        LagoonStorage storage config = _storage();
        bool isAllowed = config.vault != address(0) && config.vault == vault;
        if (!isAllowed) {
            return (false, false, address(0), address(0), 0);
        }
        return (
            true,
            config.limitEnabled,
            config.asset,
            config.pendingSilo,
            config.maxSettlementAmount
        );
    }

    // ----- Atomic settlement validation -----

    /// Capture relevant token balances immediately before Safe execution.
    ///
    /// Uncapped legacy configurations return the zero-valued snapshot. Capped
    /// configurations copy both immutable-for-this-call configuration and the
    /// two balance baselines required by the v0.5 invariant. The module must
    /// obtain this from GuardV0Base validation before invoking the Lagoon vault.
    ///
    /// @param vault Allowlisted Lagoon vault about to receive a settlement call.
    /// @return snapshot Configuration and balance baselines for verification.
    function captureSettlement(
        address vault
    ) external view returns (SettlementSnapshot memory snapshot) {
        LagoonStorage storage config = _storage();
        if (config.vault == address(0) || config.vault != vault) {
            revert LagoonVaultNotAllowed(vault);
        }
        if (!config.limitEnabled) return snapshot;

        snapshot.limitEnabled = true;
        snapshot.asset = config.asset;
        snapshot.pendingSilo = config.pendingSilo;
        snapshot.maxSettlementAmount = config.maxSettlementAmount;
        snapshot.siloBalanceBefore = IERC20(config.asset).balanceOf(config.pendingSilo);
        snapshot.vaultBalanceBefore = IERC20(config.asset).balanceOf(vault);
    }

    /// Validate Lagoon token movement immediately after Safe execution.
    ///
    /// The function rejects unexpected balance directions, calculates deposit
    /// and redemption deltas independently, and compares their gross sum with
    /// the configured inclusive cap. Equality is accepted; only actual amounts
    /// strictly greater than maxSettlementAmount revert.
    ///
    /// A revert propagates through TradingStrategyModuleV0 and Safe execution,
    /// atomically undoing the settlement. This property is the enforcement
    /// mechanism: stock Lagoon v0.5 is not modified and no queue item is split.
    ///
    /// @param vault Lagoon vault that has just executed its settlement call.
    /// @param snapshot Values returned by captureSettlement() before execution.
    /// @return grossSettlementAmount Deposit plus redemption assets in raw units.
    function verifySettlement(
        address vault,
        SettlementSnapshot calldata snapshot
    ) external returns (uint256 grossSettlementAmount) {
        if (!snapshot.limitEnabled) return 0;

        uint256 siloBalanceAfter = IERC20(snapshot.asset).balanceOf(snapshot.pendingSilo);
        uint256 vaultBalanceAfter = IERC20(snapshot.asset).balanceOf(vault);

        // Stock v0.5 consumes deposit assets from the Silo. An increase means
        // the configured addresses/token do not exhibit the expected protocol
        // behaviour, so fail closed rather than undercounting with signed maths.
        if (siloBalanceAfter > snapshot.siloBalanceBefore) {
            revert LagoonSiloBalanceIncreased(snapshot.siloBalanceBefore, siloBalanceAfter);
        }
        // Stock v0.5 sends redeem assets from the Safe into the vault. A vault
        // balance decrease is likewise outside the supported invariant.
        if (vaultBalanceAfter < snapshot.vaultBalanceBefore) {
            revert LagoonVaultBalanceDecreased(snapshot.vaultBalanceBefore, vaultBalanceAfter);
        }

        // Measure the directions separately. Using a gross sum is essential:
        // settleDeposit(uint256) may also settle redemptions, and a net balance
        // calculation could let two large opposite flows evade a small cap.
        uint256 depositAssets = snapshot.siloBalanceBefore - siloBalanceAfter;
        uint256 redeemAssets = vaultBalanceAfter - snapshot.vaultBalanceBefore;
        grossSettlementAmount = depositAssets + redeemAssets;
        if (grossSettlementAmount > snapshot.maxSettlementAmount) {
            revert LagoonSettlementLimitExceeded(
                grossSettlementAmount,
                snapshot.maxSettlementAmount
            );
        }

        emit LagoonSettlementValidated(
            vault,
            depositAssets,
            redeemAssets,
            grossSettlementAmount,
            snapshot.maxSettlementAmount
        );
    }

    // ----- Configuration validation helpers -----

    /// Check that configured addresses describe one stock Lagoon vault setup.
    ///
    /// Lagoon v0.5 does expose pendingSilo(), but the guard intentionally takes
    /// the Silo as an explicit governance input to avoid adding another Lagoon
    /// interface dependency to the execution path. The non-zero allowance from
    /// Silo to vault binds the supplied address to the protocol's deposit pull
    /// relationship; asset() binds the supplied ERC-20 to the vault.
    ///
    /// These checks are performed at configuration time. A later Lagoon upgrade
    /// or token mutation that breaks the balance invariant will fail closed in
    /// capture/verification or in the Lagoon settlement call itself.
    ///
    /// @param vault Lagoon vault being configured.
    /// @param asset Expected underlying ERC-20 contract.
    /// @param pendingSilo Expected pending-deposit Silo contract.
    function _validateConfiguration(
        address vault,
        address asset,
        address pendingSilo
    ) private view {
        if (vault == address(0)) revert LagoonInvalidAddress(vault);
        if (asset == address(0)) revert LagoonInvalidAddress(asset);
        if (pendingSilo == address(0)) revert LagoonInvalidAddress(pendingSilo);
        if (vault.code.length == 0) revert LagoonAddressHasNoCode(vault);
        if (asset.code.length == 0) revert LagoonAddressHasNoCode(asset);
        if (pendingSilo.code.length == 0) revert LagoonAddressHasNoCode(pendingSilo);

        // Prevent a valid token contract unrelated to this vault from being
        // chosen, which would make every observed settlement delta appear zero.
        address vaultAsset = ILagoonVaultV05(vault).asset();
        if (vaultAsset != asset) revert LagoonAssetMismatch(asset, vaultAsset);

        // In stock Lagoon v0.5, _settleDeposit() calls transferFrom() to pull
        // underlying from the Silo. A configured Silo without this relationship
        // is almost certainly the wrong address and must not be accepted.
        if (IERC20(asset).allowance(pendingSilo, vault) == 0) {
            revert LagoonSiloAllowanceMissing(pendingSilo, vault);
        }
    }
}
