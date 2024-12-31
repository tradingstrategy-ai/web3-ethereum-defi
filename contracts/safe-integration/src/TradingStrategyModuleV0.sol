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
 * This is initial, MVP, version.
 *
 * Notes
 * - See VelvetSafeModule as an example https://github.com/Velvet-Capital/velvet-core/blob/9d487937d0569c12e85b436a1c6f3e68a1dc8c44/contracts/vault/VelvetSafeModule.sol#L16
 *
 */
contract TradingStrategyModuleV0 is Module, GuardV0Base {

    constructor(address _owner, address _target) {
        bytes memory initializeParams = abi.encode(_owner, _target);
        setUp(initializeParams);
    }

    // Override to use Zodiac Module's ownership mechanism
    modifier onlyGuardOwner() override {
        _checkOwner();
        _;
    }

    /**
     * Get the address of the proto DAO.@author
     *
     * Override to use Zodiac Module's ownership mechanism.
     */
    function getGovernanceAddress() override public view returns (address) {
        return owner();
    }

    /// @dev Initialize function, will be triggered when a new proxy is deployed
    /// @param initializeParams Parameters of initialization encoded
    /// https://gist.github.com/auryn-macmillan/841906d0bc6c2624e83598cdfac17de8
    function setUp(bytes memory initializeParams) public override initializer {
        __Ownable_init(msg.sender);
        (address _owner, address _target) = abi.decode(initializeParams, (address, address));
        setAvatar(_target);
        setTarget(_target);
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

        bool success;
        bytes memory response;

        // Check that the asset manager can perform this function.
        // Will revert() on error
        _validateCallInternal(msg.sender, target, callData);

        // Inherit from Module contract,
        // execute a tx on behalf of Gnosis
        (success, response) = execAndReturnData(
            target,
            0,
            callData,
            Enum.Operation.Call
        );

        // Bubble up the revert reason
        if (!success) {
            assembly {
                revert(add(response, 0x20), mload(response))
            }
       }
    }
}



