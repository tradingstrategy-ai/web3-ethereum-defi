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
// asks this library for an opaque snapshot before the module executes the Safe
// call. GuardV0Base routes the same payload back to this library afterwards.
// The maximum gross amount is an asset-manager safety feature, not merely a
// settlement preference. If the movement is over the configured limit,
// validation reverts. EVM atomicity then rolls back the Safe call, Lagoon state
// updates, token transfers and event logs.
// Thus the epoch is either settled in full below the limit or not settled at
// all; this library never creates a partial settlement.
//
// Amount caps alone are insufficient because an asset manager could submit
// several individually valid settlements in quick succession. Every enabled
// cap is therefore paired with a positive cooldown. A successful automated
// settlement which moves a non-zero gross amount records its block timestamp,
// and another non-zero asset-manager settlement cannot complete until the
// cooldown has elapsed. Empty settlements neither start nor extend a cooldown
// and remain callable while one is active. The default is 24 hours. Direct Safe
// governance calls bypass this module policy for deliberate recovery.
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
// while doing nothing. GuardV0Base performs this fail-closed check on both the
// pre-call capture and hardcoded post-call dispatch paths.

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

// Default delay between non-zero asset-manager settlements. Keep this as a
// top-level constant so GuardV0Base can preserve its existing public
// whitelistLagoonWithSettlementLimit() ABI while applying the safe default.
uint256 constant DEFAULT_LAGOON_SETTLEMENT_COOLDOWN = 1 days;

/// Minimal stock Lagoon v0.5 interface needed to bind a configured asset.
///
/// Reading asset() from the vault prevents governance configuration from
/// accidentally measuring an unrelated ERC-20 contract. No larger Lagoon ABI
/// is imported because the library does not call settlement itself.
interface ILagoonVaultV05 {
    function asset() external view returns (address);
}

/// Store the paired Lagoon vault configuration and enforce settlement safety.
///
/// Configuration, pre-execution capture and post-call validation are all
/// routed through GuardV0Base. TradingStrategyModuleV0 only carries an opaque
/// generic context around execution and has no Lagoon-specific dependency.
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

        // Whether capturePostCallContext()/validatePostCall() enforce the cap.
        // Legacy whitelistLagoon() entries leave this false.
        bool limitEnabled;

        // Lagoon underlying token whose raw balance deltas are measured.
        address asset;

        // Lagoon Silo holding assets queued for the snapshotted deposit epoch.
        address pendingSilo;

        // Maximum accepted gross settlement in raw underlying-token units.
        // Zero is a valid strict cap: only a zero-asset settlement can pass.
        uint256 maxSettlementAmount;

        // Minimum delay in seconds between successful asset-manager
        // settlements. This is non-zero whenever limitEnabled is true.
        uint256 settlementCooldown;

        // Block timestamp of the latest non-zero capped asset-manager
        // settlement. Rejected and direct-governance calls never update it.
        uint256 lastSettlementTimestamp;
    }

    /// Transient pre-execution values used for atomic post-call verification.
    ///
    /// Only LagoonLib encodes or decodes this structure. GuardV0Base and the
    /// execution module carry its ABI encoding as opaque bytes, preventing the
    /// generic post-call lifecycle from depending on Lagoon-specific fields.
    /// The snapshot remains in EVM memory for one performCall() transaction and
    /// cannot leak into another call.
    struct SettlementSnapshot {
        // ERC-20 balance source copied from LagoonStorage before execution.
        address asset;

        // Deposit queue balance holder copied from LagoonStorage.
        address pendingSilo;

        // Cap copied before execution, so verification uses one coherent
        // configuration even if the implementation evolves later.
        uint256 maxSettlementAmount;

        // Cooldown copied before execution for the success event and next
        // allowed timestamp after a non-zero settlement.
        uint256 settlementCooldown;

        // Earliest timestamp for another non-zero settlement, or zero before
        // the first one. Post-call validation needs the measured gross amount
        // before deciding whether this restriction applies.
        uint256 nextSettlementTimestamp;

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

    /// Governance attempted to repoint a guard already paired with a vault.
    ///
    /// Lagoon deployments have a one-vault, one-Safe and one-guard topology.
    /// Refusing a second vault makes this deployment invariant explicit and
    /// avoids leaving stale call-site permissions or indexer records behind.
    error LagoonVaultAlreadyConfigured(address configuredVault, address requestedVault);

    /// The measured deposit-plus-redemption movement was above the cap.
    error LagoonSettlementLimitExceeded(uint256 actualAmount, uint256 maxAmount);

    /// Governance supplied a zero cooldown for an enabled safety policy.
    error LagoonInvalidSettlementCooldown(uint256 settlementCooldown);

    /// An asset manager attempted another settlement before the safety delay.
    error LagoonSettlementCooldownActive(
        uint256 currentTimestamp,
        uint256 nextSettlementTimestamp
    );

    /// The Silo moved in the opposite direction to a stock v0.5 settlement.
    error LagoonSiloBalanceIncreased(uint256 beforeBalance, uint256 afterBalance);

    /// The vault moved in the opposite direction to a stock v0.5 settlement.
    error LagoonVaultBalanceDecreased(uint256 beforeBalance, uint256 afterBalance);

    // ----- Configuration and audit events -----

    /// Preserve the original GuardV0 Lagoon approval event for indexers.
    event LagoonVaultApproved(address vault, string notes);

    /// Record the amount half of settlement safety in an indexable event.
    event LagoonSettlementLimitSet(
        address indexed vault,
        address indexed asset,
        address indexed pendingSilo,
        uint256 maxSettlementAmount,
        bool enabled,
        string notes
    );

    /// Record the time-based half of the settlement safety configuration.
    ///
    /// This separate event preserves the existing LagoonSettlementLimitSet
    /// signature for indexers. Older limit events imply the 24-hour default;
    /// this event records an explicit default or governance override.
    event LagoonSettlementCooldownSet(
        address indexed vault,
        uint256 settlementCooldown,
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

    /// Record when a successful non-zero automated settlement starts cooldown.
    event LagoonSettlementCooldownStarted(
        address indexed vault,
        uint256 settlementTimestamp,
        uint256 nextSettlementTimestamp
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

    /// Allowlist a Lagoon vault without settlement safety controls.
    ///
    /// This is the backwards-compatible route used by existing deployments.
    /// Reapplying it to a safety-configured vault intentionally clears all
    /// amount and cooldown metadata, providing a governance-controlled way to
    /// disable enforcement while preserving the original whitelist API and
    /// event. A different vault cannot replace the vault paired during deployment.
    ///
    /// @param vault Lagoon vault whose settlement selectors will be permitted.
    /// @param notes Human-readable governance audit note.
    function whitelistVault(address vault, string calldata notes) external {
        _validateVaultAssignment(vault);

        LagoonStorage storage config = _storage();
        config.vault = vault;
        config.limitEnabled = false;
        config.asset = address(0);
        config.pendingSilo = address(0);
        config.maxSettlementAmount = 0;
        config.settlementCooldown = 0;
        config.lastSettlementTimestamp = 0;
        emit LagoonVaultApproved(vault, notes);
    }

    /// Allowlist a Lagoon vault with the default 24-hour cooldown.
    ///
    /// This entry point preserves the first settlement-limit library ABI. The
    /// amount was originally the only enforced dimension; applying the safe
    /// default here ensures old Guard callers gain rate limiting without a new
    /// argument. New callers needing an override use the explicit function
    /// below.
    ///
    /// @param vault Stock Lagoon vault to allowlist.
    /// @param asset Vault underlying ERC-20 returned by vault.asset().
    /// @param pendingSilo Vault-specific Lagoon Silo holding queued deposits.
    /// @param maxSettlementAmount Maximum gross asset-manager settlement in raw units.
    /// @param notes Human-readable governance audit note.
    function whitelistVaultWithSettlementLimit(
        address vault,
        address asset,
        address pendingSilo,
        uint256 maxSettlementAmount,
        string calldata notes
    ) external {
        _whitelistVaultWithSettlementSafety(
            vault,
            asset,
            pendingSilo,
            maxSettlementAmount,
            DEFAULT_LAGOON_SETTLEMENT_COOLDOWN,
            notes
        );
    }

    /// Allowlist a Lagoon vault and enable custom settlement safety controls.
    ///
    /// The function validates the relationship between vault, asset and Silo
    /// before storing configuration. GuardV0Base separately allowlists all
    /// supported settlement call sites after this delegatecall succeeds.
    ///
    /// @param vault Stock Lagoon vault to allowlist.
    /// @param asset Vault underlying ERC-20 returned by vault.asset().
    /// @param pendingSilo Vault-specific Lagoon Silo holding queued deposits.
    /// @param maxSettlementAmount Maximum gross asset-manager settlement in raw units.
    /// @param settlementCooldown Minimum seconds between non-zero settlements.
    /// @param notes Human-readable governance audit note.
    function whitelistVaultWithSettlementLimitAndCooldown(
        address vault,
        address asset,
        address pendingSilo,
        uint256 maxSettlementAmount,
        uint256 settlementCooldown,
        string calldata notes
    ) external {
        _whitelistVaultWithSettlementSafety(
            vault,
            asset,
            pendingSilo,
            maxSettlementAmount,
            settlementCooldown,
            notes
        );
    }

    /// Validate and store one amount-and-cooldown safety policy.
    ///
    /// Centralising the write path keeps the default and explicit public
    /// library entry points behaviourally identical except for the duration.
    function _whitelistVaultWithSettlementSafety(
        address vault,
        address asset,
        address pendingSilo,
        uint256 maxSettlementAmount,
        uint256 settlementCooldown,
        string calldata notes
    ) private {
        _validateConfiguration(vault, asset, pendingSilo);
        if (settlementCooldown == 0) {
            revert LagoonInvalidSettlementCooldown(settlementCooldown);
        }

        LagoonStorage storage config = _storage();
        config.vault = vault;
        config.limitEnabled = true;
        config.asset = asset;
        config.pendingSilo = pendingSilo;
        config.maxSettlementAmount = maxSettlementAmount;
        config.settlementCooldown = settlementCooldown;

        emit LagoonVaultApproved(vault, notes);
        emit LagoonSettlementLimitSet(
            vault,
            asset,
            pendingSilo,
            maxSettlementAmount,
            true,
            notes
        );
        emit LagoonSettlementCooldownSet(vault, settlementCooldown, notes);
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

    /// Return the time-based settlement safety state without changing the
    /// backwards-compatible getVaultConfig() return shape.
    ///
    /// A configured capped vault always reports a positive cooldown. A zero
    /// stored value is interpreted as the 24-hour default so a guard upgraded
    /// from the first amount-only implementation fails safe. Unlimited and
    /// unknown vaults return three zero values.
    ///
    /// @param vault Lagoon vault address to inspect.
    /// @return settlementCooldown Minimum seconds between non-zero settlements.
    /// @return lastSettlementTimestamp Latest non-zero automated settlement.
    /// @return nextSettlementTimestamp Earliest next non-zero settlement time.
    function getSettlementCooldownConfig(
        address vault
    ) external view returns (
        uint256 settlementCooldown,
        uint256 lastSettlementTimestamp,
        uint256 nextSettlementTimestamp
    ) {
        LagoonStorage storage config = _storage();
        if (
            config.vault == address(0) ||
            config.vault != vault ||
            !config.limitEnabled
        ) {
            return (0, 0, 0);
        }

        settlementCooldown = _effectiveSettlementCooldown(config);
        lastSettlementTimestamp = config.lastSettlementTimestamp;
        if (lastSettlementTimestamp != 0) {
            nextSettlementTimestamp = lastSettlementTimestamp + settlementCooldown;
        }
    }

    /// Return the complete amount-and-cooldown safety state in one call.
    ///
    /// GuardV0Base exposes this convenience read to offchain deployment and
    /// monitoring tools. Keeping the storage aggregation here avoids two
    /// separate external-library delegatecalls and duplicate ABI decoding in
    /// the size-constrained module. The older focused getters remain available
    /// unchanged for backwards compatibility.
    ///
    /// @param vault Lagoon vault address to inspect.
    /// @return allowed Whether the singleton vault is allowlisted.
    /// @return limitEnabled Whether amount-and-cooldown safety is enabled.
    /// @return asset Underlying ERC-20 measured by the validator.
    /// @return pendingSilo Pending-deposit Silo measured by the validator.
    /// @return maxSettlementAmount Inclusive gross amount safety limit.
    /// @return settlementCooldown Delay between non-zero settlements in seconds.
    /// @return lastSettlementTimestamp Latest non-zero settlement Unix timestamp.
    /// @return nextSettlementTimestamp Earliest next non-zero settlement Unix timestamp.
    function getSettlementSafetyConfig(
        address vault
    ) external view returns (
        bool allowed,
        bool limitEnabled,
        address asset,
        address pendingSilo,
        uint256 maxSettlementAmount,
        uint256 settlementCooldown,
        uint256 lastSettlementTimestamp,
        uint256 nextSettlementTimestamp
    ) {
        LagoonStorage storage config = _storage();
        allowed = config.vault != address(0) && config.vault == vault;
        if (!allowed) return (false, false, address(0), address(0), 0, 0, 0, 0);

        limitEnabled = config.limitEnabled;
        asset = config.asset;
        pendingSilo = config.pendingSilo;
        maxSettlementAmount = config.maxSettlementAmount;
        if (!limitEnabled) return (
            allowed,
            limitEnabled,
            asset,
            pendingSilo,
            maxSettlementAmount,
            0,
            0,
            0
        );

        settlementCooldown = _effectiveSettlementCooldown(config);
        lastSettlementTimestamp = config.lastSettlementTimestamp;
        if (lastSettlementTimestamp != 0) {
            nextSettlementTimestamp = lastSettlementTimestamp + settlementCooldown;
        }
    }

    // ----- Atomic settlement validation -----

    /// Capture an opaque Lagoon context immediately before Safe execution.
    ///
    /// Uncapped legacy configurations return empty bytes, signalling that the
    /// generic GuardV0Base lifecycle does not need a post-call validator. For a
    /// capped vault, the payload binds the exact vault address, configuration
    /// and pre-call balances into one value which only validatePostCall()
    /// decodes. The payload is produced internally and is never caller input.
    ///
    /// @param vault Allowlisted Lagoon vault about to receive a settlement call.
    /// @return context Empty for unlimited mode, otherwise encoded Lagoon state.
    function capturePostCallContext(
        address vault
    ) external view returns (bytes memory context) {
        LagoonStorage storage config = _storage();
        if (config.vault == address(0) || config.vault != vault) {
            revert LagoonVaultNotAllowed(vault);
        }
        if (!config.limitEnabled) return context;

        // Capture rather than enforce the current time window. Lagoon must run
        // before the validator can distinguish an empty settlement, which is
        // always allowed, from a non-zero settlement subject to cooldown. A
        // later rejection remains safe because the post-call revert atomically
        // rolls back the complete Safe and Lagoon execution.
        uint256 settlementCooldown = _effectiveSettlementCooldown(config);
        uint256 lastSettlementTimestamp = config.lastSettlementTimestamp;
        uint256 nextSettlementTimestamp;
        if (lastSettlementTimestamp != 0) {
            nextSettlementTimestamp = lastSettlementTimestamp + settlementCooldown;
        }

        SettlementSnapshot memory snapshot;
        snapshot.asset = config.asset;
        snapshot.pendingSilo = config.pendingSilo;
        snapshot.maxSettlementAmount = config.maxSettlementAmount;
        snapshot.settlementCooldown = settlementCooldown;
        snapshot.nextSettlementTimestamp = nextSettlementTimestamp;
        snapshot.siloBalanceBefore = IERC20(config.asset).balanceOf(config.pendingSilo);
        snapshot.vaultBalanceBefore = IERC20(config.asset).balanceOf(vault);

        // Include the vault in the library-owned payload. The post-call caller
        // therefore cannot accidentally supply a different target alongside a
        // valid snapshot when more validator kinds are added in the future.
        return abi.encode(vault, snapshot);
    }

    /// Validate an opaque Lagoon context immediately after Safe execution.
    ///
    /// Only capturePostCallContext() creates this payload. Decoding stays inside
    /// LagoonLib so the generic GuardV0Base post-call mechanism never imports
    /// SettlementSnapshot or assumes how Lagoon measures a settlement. The
    /// function rejects unexpected balance directions, calculates deposit and
    /// redemption deltas independently, and compares their gross sum with the
    /// configured inclusive cap. Equality is accepted; only actual amounts
    /// strictly greater than maxSettlementAmount revert.
    ///
    /// A revert propagates through TradingStrategyModuleV0 and Safe execution,
    /// atomically undoing the settlement. This property is the enforcement
    /// mechanism: stock Lagoon v0.5 is not modified and no queue item is split.
    ///
    /// @param context Value returned by capturePostCallContext() before execution.
    /// @return grossSettlementAmount Deposit plus redemption assets in raw units.
    function validatePostCall(
        bytes calldata context
    ) external returns (uint256 grossSettlementAmount) {
        (address vault, SettlementSnapshot memory snapshot) = abi.decode(
            context,
            (address, SettlementSnapshot)
        );

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

        // Empty Lagoon settlements are operational no-ops and must not start,
        // extend or be blocked by the cooldown. Only non-zero gross movement
        // reaches the timestamp check and storage write below.
        if (grossSettlementAmount != 0) {
            // A cooldown necessarily follows the chain's consensus timestamp.
            // Small validator drift cannot materially bypass the 24-hour
            // default; governance must choose custom durations with the same
            // timestamp tolerance in mind.
            if (
                snapshot.nextSettlementTimestamp != 0 &&
                // forge-lint: disable-next-line(block-timestamp)
                block.timestamp < snapshot.nextSettlementTimestamp
            ) {
                revert LagoonSettlementCooldownActive(
                    block.timestamp,
                    snapshot.nextSettlementTimestamp
                );
            }

            // Only a fully validated non-zero asset-manager settlement reaches
            // this write. Any later revert rolls it back with Lagoon. Rejected,
            // empty and direct-governance settlements leave the timestamp alone.
            LagoonStorage storage config = _storage();
            config.lastSettlementTimestamp = block.timestamp;
            uint256 nextSettlementTimestamp = block.timestamp + snapshot.settlementCooldown;
            emit LagoonSettlementCooldownStarted(
                vault,
                block.timestamp,
                nextSettlementTimestamp
            );
        }
    }

    /// Resolve the configured cooldown with a fail-safe migration default.
    ///
    /// The zero fallback protects any amount-only v1 storage written before
    /// the cooldown field existed. New configuration rejects zero explicitly,
    /// so this branch is only a backwards-compatibility safety net.
    ///
    /// @param config Lagoon singleton storage.
    /// @return Cooldown duration in seconds.
    function _effectiveSettlementCooldown(
        LagoonStorage storage config
    ) private view returns (uint256) {
        uint256 configuredCooldown = config.settlementCooldown;
        if (configuredCooldown == 0) {
            return DEFAULT_LAGOON_SETTLEMENT_COOLDOWN;
        }
        return configuredCooldown;
    }

    // ----- Configuration validation helpers -----

    /// Check that configured addresses describe one stock Lagoon vault setup.
    ///
    /// Stock Lagoon v0.5 removed the public pendingSilo() getter, so the guard
    /// takes the Silo as an explicit governance input. The deployment API reads
    /// the canonical ERC-7201 storage slot. Onchain, a non-zero Silo-to-vault
    /// allowance checks the expected deposit-pull relationship, while asset()
    /// binds the supplied ERC-20 to the vault.
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
        _validateVaultAssignment(vault);
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

    /// Enforce the singleton Lagoon deployment topology during configuration.
    ///
    /// Reconfiguring the same vault remains supported so governance can change
    /// or disable its settlement safety policy. Assigning another vault is
    /// rejected: the surrounding GuardV0Base call-site allowlist is append-only,
    /// so silently switching the singleton would otherwise retain obsolete
    /// permissions.
    ///
    /// @param vault Lagoon vault requested by governance.
    function _validateVaultAssignment(address vault) private view {
        if (vault == address(0)) revert LagoonInvalidAddress(vault);

        address configuredVault = _storage().vault;
        if (configuredVault != address(0) && configuredVault != vault) {
            revert LagoonVaultAlreadyConfigured(configuredVault, vault);
        }
    }
}
