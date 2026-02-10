/**
 * Check for legit trade execution actions.
 *
 */

pragma solidity ^0.8.0;

import "@openzeppelin/access/Ownable.sol";
import "./lib/Path.sol";
import "./IGuard.sol";
import "./GuardV0Base.sol";

/**
 * Prototype guard implementation.
 *
 * - Hardcoded actions for Uniswap v2, v3, Aave, etc.
 *
 */
contract GuardV0 is GuardV0Base, Ownable {

    constructor() Ownable() {
    }

    /**
     * Specify a modifier for guard owner
     */
    modifier onlyGuardOwner() override {
        require(owner() == _msgSender(), "Ownable: caller is not the owner");
        _;
    }

    /**
     * Get the address of the proto DAO
     */
    function getGovernanceAddress() override public view returns (address) {
        return owner();
    }

}