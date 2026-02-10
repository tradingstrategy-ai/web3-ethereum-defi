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

    // Identify the deployed ABI
    function getTradingStrategyModuleVersion() public pure returns (string memory) {
        return "v0.1.4";
    }

    /**
     * Get the address of the proto DAO.
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
    function performCall(address target, bytes calldata callData, uint256 value) public {

        bool success;
        bytes memory response;

        // Check that the asset manager can perform this function.
        // Will revert() on error
        _validateCallInternal(msg.sender, target, callData);

        // Inherit from Module contract,
        // execute a tx on behalf of Gnosis
        (success, response) = execAndReturnData(
            target,
            value,
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

    /**
     * Keep backward compatibility with the old performCall
     */
    function performCall(address target, bytes calldata callData) external {
        performCall(target, callData, 0);
    }

    // Expose CowSwap swap function pre-signer to asset managers
    function swapAndValidateCowSwap(
        address settlementContract,
        address receiver,
        bytes32 appData,
        address tokenIn,
        address tokenOut,
        uint256 amountIn,
        uint256 minAmountOut
    ) public {
        bool success;
        bytes memory response;

        // Checks for asset manager (msg.sender), receiver, etc.
        PresignDeletaCallData memory presignDeletaCallData = _swapAndValidateCowSwap(
            settlementContract,
            receiver,
            appData,
            tokenIn,
            tokenOut,
            amountIn,
            minAmountOut
        );
        // Perform ICowSettlement.setPresigned() call on the behalf of the Safe
        (success, response) = execAndReturnData(
            presignDeletaCallData.targetAddress,
            0,
            presignDeletaCallData.data,
            Enum.Operation.Call
        );

        // Bubble up the revert reason
        if (!success) {
            assembly {
                revert(add(response, 0x20), mload(response))
            }
       }
    }

    /**
     * Execute a Velora (ParaSwap) swap through the Safe.
     *
     * Unlike CowSwap which uses offchain order books and presigning,
     * Velora executes atomically by calling Augustus Swapper directly.
     *
     * @param augustusSwapper The Velora Augustus Swapper contract address
     * @param tokenIn The token being sold
     * @param tokenOut The token being bought
     * @param amountIn The amount of tokenIn to sell (for event logging)
     * @param minAmountOut The minimum amount of tokenOut to receive (slippage protection)
     * @param augustusCalldata The raw calldata from Velora API to execute on Augustus
     */
    function swapAndValidateVelora(
        address augustusSwapper,
        address tokenIn,
        address tokenOut,
        uint256 amountIn,
        uint256 minAmountOut,
        bytes memory augustusCalldata
    ) public {
        uint256 preBalance = _validateVeloraSwapAndGetPreBalance(
            avatar,
            augustusSwapper,
            tokenIn,
            tokenOut
        );

        (bool success, bytes memory response) = execAndReturnData(
            augustusSwapper,
            0,
            augustusCalldata,
            Enum.Operation.Call
        );

        if (!success) {
            assembly {
                revert(add(response, 0x20), mload(response))
            }
        }

        _verifyVeloraSwapAndEmit(
            avatar,
            augustusSwapper,
            tokenIn,
            tokenOut,
            amountIn,
            minAmountOut,
            preBalance
        );
    }

}



