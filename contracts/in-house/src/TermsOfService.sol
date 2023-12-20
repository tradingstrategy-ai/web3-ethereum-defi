// SPDX-License-Identifier: GPL-3.0

pragma solidity 0.6.12;

import "../../1delta/contracts/external-protocols/venus/test/assets/BEP20BitcoinCash.sol";
pragma experimental ABIEncoderV2;

/**
 * Manage signatures of users of different versions of terms of service.
 */
contract TermsOfServiceAcceptanceTracker is Ownable {

    // Terms of service acceptances
    //
    // Account can and may need to accept multiple terms of services.
    // Each terms of service is identified by its hash of text.
    // The acceptance is a message that signs this terms of service version.
    //
    public mapping(address account => mapping(bytes32 textHash => boolean)) acceptances;

    public uint256 latestTermsOfServiceHash;

    // Add a new terms of service version
    event UpdateTermsOfService(bytes32 textHash);

    public construct() : Ownable {
    }

    function hasAccepted(address account, bytes32 textHash) public returns (boolean accapted) {
    }

    function updateTermsOfService(bytes32 textHash) public onlyOwner {
        this.latestTermsOfServiceHash = textHash;
        emit UpdateTermsOfService(textHash);
    }

    /**
     * Can the current user proceed to deposit, or they they need to sign
     * the latest terms of service.
     */
    function canProceedToDeposit() public returns (boolean accapted) {
        return this.hasAccepted(msg.sender, this.latestTermsOfServiceHash);
    }
}
