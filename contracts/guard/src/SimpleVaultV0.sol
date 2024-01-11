/**
 * A very simple vault implementation.
 *
 */

pragma solidity ^0.8.0;

import "@openzeppelin/access/Ownable.sol";

import "./GuardV0.sol";


/**
 * Simple vault allowing delegating of a trading activites to a hot wallet.@author
 *
 * - Self-contained
 * - Guard is used to check asset manager can only perform approved operations.
 * - No shares, single owner
 * - No accounting
 */
contract SimpleVaultV0 is Ownable {

    address public assetManager;

    address public withdrawAddress;

    GuardV0 public guard;

    constructor() Ownable() {
        guard = new GuardV0();
        // The owner of the guard is the vault creator, not the vault itself
        guard.transferOwnership(msg.sender);
        guard.allowWithdrawDestination(msg.sender, "Initial owner can withdraw");
        guard.allowReceiver(address(this), "Vault can receive tokens from a trade");
    }

    function getWithdrawAddress() public view returns (address) {
        return owner();
    }

    function updateAssetManager(address _assetManager, string calldata notes) external onlyOwner {
        if(assetManager != address(0)) {
            guard.removeSender(assetManager, notes);
        }
        assetManager = _assetManager;
        guard.allowSender(_assetManager, notes);
    }

    function performCall(address target, bytes calldata callData) external {

        // Check that the asset manager can perform this function
        guard.validateCall(msg.sender, target, callData);

        (bool success, bytes memory returnData) = target.call(callData);
        require(success, string(returnData));
    }

}
