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
 * - GuardV0 contract integration for Safe multisignature wallets as a module
 * - Add automated trading strategy support w/whitelisted trading universe and trade executors
 * - Support Lagoon, Gnosis Safe and other Gnosis Safe-based ecosystems which support Zodiac modules
 * - Owner should point to Gnosis Safe / DAO
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
    function getTradingStrategyModuleVersion()
        public
        pure
        returns (string memory)
    {
        return "v0.4";
    }

    /**
     * Get the address of the proto DAO.
     *
     * Override to use Zodiac Module's ownership mechanism.
     */
    function getGovernanceAddress() public view override returns (address) {
        return owner();
    }

    /// @dev Initialize function, will be triggered when a new proxy is deployed
    /// @param initializeParams Parameters of initialization encoded
    /// https://gist.github.com/auryn-macmillan/841906d0bc6c2624e83598cdfac17de8
    function setUp(bytes memory initializeParams) public override initializer {
        __Ownable_init(msg.sender);
        (address _owner, address _target) = abi.decode(
            initializeParams,
            (address, address)
        );
        setAvatar(_target);
        setTarget(_target);
        transferOwnership(_owner);
    }

    // Bubble up revert reason from a failed Safe execution.
    // Used by performCall, swapAndValidateCowSwap, and swapAndValidateVelora
    // to avoid duplicating the assembly block.
    function _bubbleUpRevert(bool success, bytes memory response) private pure {
        if (!success) {
            assembly {
                revert(add(response, 0x20), mload(response))
            }
        }
    }

    /**
     * The main entry point for the trade executor.
     *
     * - Checks for the whitelisted sender (=trade executor hot wallet)
     * - Check for the allowed callsites/token whitelists/etc.
     * - Execute transaction on behalf of Safe
     *
     */
    // NOTE: The `value` parameter (ETH sent with the call) is not validated
    // by the guard. This is accepted behaviour — Safes typically hold minimal
    // ETH (gas money), all targets are governance-approved contracts, and any
    // ETH sent goes to those trusted targets (not to the asset manager).
    //
    // If msg.value > 0, the caller's ETH is forwarded to the Safe (avatar)
    // before execution — this allows the asset manager to fund execution fees
    // (e.g. GMX keeper fees) without the Safe needing a pre-funded ETH balance.
    // If msg.value == 0, the Safe uses its own ETH balance (backward compatible).
    function performCall(
        address target,
        bytes calldata callData,
        uint256 value
    ) public payable {
        // Check that the asset manager can perform this function.
        // Will revert() on error
        _validateCallInternal(msg.sender, target, callData);

        // Forward any ETH sent by the caller to the Safe.
        // The Safe's receive() function accepts plain ETH transfers.
        if (msg.value > 0) {
            (bool sent, ) = avatar.call{value: msg.value}("");
            require(sent, "ETH forward failed");
        }

        // Inherit from Module contract,
        // execute a tx on behalf of Gnosis
        (bool success, bytes memory response) = execAndReturnData(
            target,
            value,
            callData,
            Enum.Operation.Call
        );

        _bubbleUpRevert(success, response);
    }

    /**
     * Keep backward compatibility with the old performCall
     */
    function performCall(address target, bytes calldata callData) external {
        performCall(target, callData, 0);
    }

    // Expose CowSwap swap function pre-signer to asset managers.
    //
    // Validation and order creation are consolidated in CowSwapLib to
    // minimise the module's bytecode (EIP-170).
    function swapAndValidateCowSwap(
        address settlementContract,
        address receiver,
        bytes32 appData,
        address tokenIn,
        address tokenOut,
        uint256 amountIn,
        uint256 minAmountOut
    ) public {
        require(CowSwapLib.isDeployed(), "CowSwapLib not linked");

        PresignCallData memory presignCallData = CowSwapLib.validateAndCreateOrder(
            settlementContract,
            receiver,
            appData,
            tokenIn,
            tokenOut,
            amountIn,
            minAmountOut
        );

        // Perform ICowSettlement.setPresigned() call on the behalf of the Safe
        (bool success, bytes memory response) = execAndReturnData(
            presignCallData.targetAddress,
            0,
            presignCallData.data,
            Enum.Operation.Call
        );

        _bubbleUpRevert(success, response);
    }

    /**
     * Execute a Velora (ParaSwap) swap through the Safe.
     *
     * Unlike CowSwap which uses offchain order books and presigning,
     * Velora executes atomically by calling Augustus Swapper directly.
     *
     * Validation is consolidated in VeloraLib to minimise the module's
     * bytecode (EIP-170).
     *
     * @param augustusSwapper The Velora Augustus Swapper contract address
     * @param receiver The address that receives swap output (must be whitelisted)
     * @param tokenIn The token being sold
     * @param tokenOut The token being bought
     * @param amountIn The amount of tokenIn to sell (for event logging)
     * @param minAmountOut The minimum amount of tokenOut to receive (slippage protection)
     * @param augustusCalldata The raw calldata from Velora API to execute on Augustus
     */
    function swapAndValidateVelora(
        address augustusSwapper,
        address receiver,
        address tokenIn,
        address tokenOut,
        uint256 amountIn,
        uint256 minAmountOut,
        bytes memory augustusCalldata
    ) public {
        require(VeloraLib.isDeployed(), "VeloraLib not linked");

        (uint256 preBalanceIn, uint256 preBalanceOut) = VeloraLib.validateAndGetPreBalances(
            avatar,
            augustusSwapper,
            receiver,
            tokenIn,
            tokenOut
        );

        (bool success, bytes memory response) = execAndReturnData(
            augustusSwapper,
            0,
            augustusCalldata,
            Enum.Operation.Call
        );

        _bubbleUpRevert(success, response);

        VeloraLib.verifyBalancesAndEmit(
            avatar,
            augustusSwapper,
            tokenIn,
            tokenOut,
            amountIn,
            minAmountOut,
            preBalanceIn,
            preBalanceOut
        );
    }
}
