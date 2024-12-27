/**
 * Safe and Zodiac based guards.
 *
 * For Lagoon and Safe wallet integration.
 *
 * Notes on Safe Modules
 * - https://gist.github.com/auryn-macmillan/841906d0bc6c2624e83598cdfac17de8
 * - https://github.com/gnosisguild/zodiac/blob/master/contracts/core/Module.sol
 * - https://gist.github.com/auryn-macmillan/105ae8f09c34406997d217ee4dc0f63a
 * - https://www.zodiac.wiki/documentation/custom-module
 */

pragma solidity ^0.8.26;

import "@gnosis.pm/zodiac/contracts/core/Module.sol";
import "@guard/GuardV0Base.sol";

/**
 * Trading Strategy integration as Zodiac Module.
 *
 * - Add automated trading strategy support w/whitelisted trading universe and
 *   and trade executors
 * - Support Lagoon, Gnosis Safe and other Gnosis Safe-based ecosystems which support Zodiac modules
 * - Owner should point to Gnosis Safe / DAO
 *
 */
contract TradingStrategyModule is Module, GuardV0Base {

    constructor(address _owner) {
        bytes memory initializeParams = abi.encode(_owner);
        setUp(initializeParams);
    }

    modifier onlyGuardOwner() override {
        _checkOwner();
        _;
    }

    /**
     * Get the address of the proto DAO
     */
    function getGovernanceAddress() override public view returns (address) {
        return owner();
    }

    /// @dev Initialize function, will be triggered when a new proxy is deployed
    /// @param initializeParams Parameters of initialization encoded
    /// https://gist.github.com/auryn-macmillan/841906d0bc6c2624e83598cdfac17de8
    function setUp(bytes memory initializeParams) public override initializer {
        __Ownable_init(msg.sender);
        (address _owner) = abi.decode(initializeParams, (address));

        setAvatar(_owner);
        transferOwnership(_owner);
    }

    /**
     * The main entry point for the trade executor.
     *
     * - Checks for the whitelisted sender (=trade executor hot wallet)
     * - Check for the allowed callsites/token whitelists/etc.
     * - Execute transaction on behalf of Safe
     *
     */
    function performCall(address target, bytes calldata callData) external {

        // Check that the asset manager can perform this function.
        // Will revert() on error
        validateCall(msg.sender, target, callData);

        // Inherit from Module contract,
        // execute a tx on behalf of Gnosis
        exec(
            target,
            0,
            callData,
            Enum.Operation.Call
        );
    }
}



