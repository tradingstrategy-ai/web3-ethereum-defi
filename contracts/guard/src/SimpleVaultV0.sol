/**
 * A very simple vault implementation.
 *
 */

pragma solidity ^0.8.0;

import "@openzeppelin/access/Ownable.sol";
import "@openzeppelin/token/ERC20/IERC20.sol";

import "./GuardV0.sol";


/**
 * Simple vault allowing delegating of a trading activites to a hot wallet.
 *
 * - Self-contained
 * - Guard is used to check asset manager can only perform approved operations.
 * - No shares, single owner
 * - No accounting
 * - No slippage protection (unlike Enzyme)
 */
contract SimpleVaultV0 is Ownable {

    address public assetManager;

    address public withdrawAddress;

    GuardV0 public guard;

    constructor(address _assetManager) Ownable() {
        guard = new GuardV0();

        // Set the initial asset manager
        assetManager = _assetManager;
        guard.allowSender(_assetManager, "Initial asset manager set");

    }

    /**
     * Initialise vault and guard for a withdrawal destination.
     */
    function initialiseOwnership(address _owner) onlyOwner external {
        // Initialise the guard where the deployer
        // is the owner and can always withdraw
        guard.allowWithdrawDestination(_owner, "Initial owner can withdraw");
        guard.allowReceiver(address(this), "Vault can receive tokens from a trade");
        guard.transferOwnership(_owner);  // The owner of the guard is the vault creator, not the vault itself
        transferOwnership(_owner);
    }

    function resetGuard(GuardV0 _guard) onlyOwner external {
        guard = _guard;
    }

    /**
     * Allow single withdrawal destination.
     *
     * Preferably multisig/DAO treasury address.
     */
    function getWithdrawAddress() public view returns (address) {
        return owner();
    }

    /**
     * Asset manager can no longer trade on this vault.
     *
     * Emergency pause set by the governance. Disable with updateAssetManager().
     */
    function isDisabled() public view returns (bool) {
        return assetManager != address(0);
    }

    /**
     * Change the asset manager.
     *
     * Set to zero address to disable asset manager.
     *
     */
    function updateAssetManager(address _assetManager, string calldata notes) public onlyOwner {
        if(assetManager != address(0)) {
            guard.removeSender(assetManager, notes);
        }
        assetManager = _assetManager;
        if(assetManager != address(0)) {
            guard.allowSender(_assetManager, notes);
        }
    }

    function performCall(address target, bytes calldata callData) external {

        // Check that the asset manager can perform this function
        guard.validateCall(msg.sender, target, callData);

        // https://ethereum.stackexchange.com/a/69134/620
        (bool success, bytes memory returnData) = target.call(callData);

        if(!success) {
            assembly{
                let revertStringLength := mload(returnData)
                let revertStringPtr := add(returnData, 0x20)
                revert(revertStringPtr, revertStringLength)
            }
        }
    }

}
