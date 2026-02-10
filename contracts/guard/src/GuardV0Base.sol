/**
 * Check for legit trade execution actions.
 *
 */

pragma solidity ^0.8.0;

import "./lib/Path.sol";
import "./IGuard.sol";

import "./lib/IERC4626.sol";
import "./lib/Multicall.sol";
import "./lib/SwapCowSwap.sol";

/**
 * Prototype guard implementation.
 *
 * - Hardcoded actions for Uniswap v2, v3, 1delta, Aave, etc.
 *
 * - Abstract base contract to deal with different ownership modifiers and initialisers (Safe, OpenZeppelin).@author
 *
 * - We include native multicall support so you can whitelist multiple assets in the same tx
 *
 */
abstract contract GuardV0Base is IGuard,  Multicall, SwapCowSwap  {

    using Path for bytes;
    using BytesLib for bytes;

    /**
     * Constants for 1delta path decoding using similar approach as Uniswap v3 `Path.sol`
     * 
     * Check our implementation at: `validate1deltaPath()`
     */
    /// @dev The length of the bytes encoded address
    uint256 private constant ADDR_SIZE = 20;
    /// @dev The length of the bytes encoded pool fee
    uint256 private constant ONEDELTA_FEE_SIZE = 3;
    /// @dev The length of the bytes encoded DEX ID
    uint256 private constant ONEDELTA_PID_SIZE = 1;
    /// @dev The length of the bytes encoded action
    uint256 private constant ONEDELTA_ACTION_SIZE = 1;
    /// @dev The offset of a single token address, fee, pid and action
    uint256 private constant ONEDELTA_NEXT_OFFSET = ADDR_SIZE + ONEDELTA_FEE_SIZE + ONEDELTA_PID_SIZE + ONEDELTA_ACTION_SIZE;
    /// @dev The offset of an encoded pool key
    uint256 private constant ONEDELTA_POP_OFFSET = ONEDELTA_NEXT_OFFSET + ADDR_SIZE;
    /// @dev The minimum length of an encoding that contains 2 or more pools
    uint256 private constant ONEDELTA_MULTIPLE_POOLS_MIN_LENGTH = ONEDELTA_POP_OFFSET + ONEDELTA_NEXT_OFFSET;

    struct ExactInputParams {
        bytes path;
        address recipient;
        uint256 deadline;
        uint256 amountIn;
        uint256 amountOutMinimum;
    }

    struct ExactOutputParams {
        bytes path;
        address recipient;
        uint256 deadline;
        uint256 amountOut;
        uint256 amountInMaximum;
    }

    // Allowed external smart contract calls (address, function selector) tuples
    mapping(address target => mapping(bytes4 selector => bool allowed)) public allowedCallSites;

    // Because of EVM limitations, maintain a separate list of allowed target smart contracts,
    // so we can produce better error messages.
    // Note: This list is only referential, as because EVM and Solidity are such crap,
    // it is not possible to smartly remove items from this list.
    // It is not used in the security checks.
    mapping(address target => bool allowed) public allowedTargets;

    // How many call sites we have enabled all-time counter.
    //
    // Used for diagnostics/debugging.
    //
    uint public callSiteCount;

    // Allowed ERC-20 tokens we may receive or send in a trade
    mapping(address token => bool allowed) public allowedAssets;

    // Allowed trade executor hot wallets
    mapping(address sender => bool allowed) public allowedSenders;

    // Allowed token receivers post trade
    mapping(address receiver => bool allowed) public allowedReceivers;

    // Allowed owners
    mapping(address destination => bool allowed) public allowedWithdrawDestinations;

    // Allowed routers
    mapping(address destination => bool allowed) public allowedApprovalDestinations;

    // Allowed delegation approval destinations
    mapping(address destination => bool allowed) public allowedDelegationApprovalDestinations;

    // Allowed Lagoon vault settlement destinations
    //
    // We need to perform this action as a Safe multisig by calling Vault.settleDeposit() and Vault.settleRedeem()
    //
    mapping(address destination => bool allowed) public allowedLagoonVaults;

    // Allowed cow swap instances.
    //
    // The deployed address of GPv2 settlement contract.
    //
    // https://etherscan.io/address/0x9008d19f58aabd9ed0d60971565aa8510560ab41
    //
    mapping(address destination => bool allowed) public allowedCowSwaps;

    // Allow trading any token
    //
    // Dangerous, as malicious/compromised trade-executor can drain all assets through creating fake tokens
    //
    bool public anyAsset;

    event CallSiteApproved(address target, bytes4 selector, string notes);
    event CallSiteRemoved(address target, bytes4 selector, string notes);

    event SenderApproved(address sender, string notes);
    event SenderRemoved(address sender, string notes);

    event ReceiverApproved(address sender, string notes);
    event ReceiverRemoved(address sender, string notes);

    event WithdrawDestinationApproved(address sender, string notes);
    event WithdrawDestinationRemoved(address sender, string notes);

    event ApprovalDestinationApproved(address sender, string notes);
    event ApprovalDestinationRemoved(address sender, string notes);

    event DelegationApprovalDestinationApproved(address sender, string notes);
    event DelegationApprovalDestinationRemoved(address sender, string notes);

    event AssetApproved(address sender, string notes);
    event AssetRemoved(address sender, string notes);

    event AnyAssetSet(bool value, string notes);
    event AnyVaultSet(bool value, string notes);


    event LagoonVaultApproved(address vault, string notes);

    event CowSwapApproved(address settlementContract, string notes);
    event ERC4626Approved(address vault, string notes);

    // Implementation needs to provide its own ownership policy hooks
    modifier onlyGuardOwner() virtual;

    // Implementation needs to provide its own ownership policy hooks
    function getGovernanceAddress() virtual public view returns (address);

    /**
     * Calculate Solidity 4-byte function selector from a string.
     */
    function getSelector(string memory _func) internal pure returns (bytes4) {
        // https://solidity-by-example.org/function-selector/
        return bytes4(keccak256(bytes(_func)));
    }

    /**
     * Track version during internal development.
     *
     * We bump up when new whitelistings added.
     */
    function getInternalVersion() public pure returns (uint8) {
        return 1;
    }

    function allowCallSite(address target, bytes4 selector, string calldata notes) public onlyGuardOwner {
        allowedCallSites[target][selector] = true;
        allowedTargets[target] = true;
        callSiteCount++;
        emit CallSiteApproved(target, selector, notes);
    }

    function removeCallSite(address target, bytes4 selector, string calldata notes) public onlyGuardOwner {
        delete allowedCallSites[target][selector];
        emit CallSiteRemoved(target, selector, notes);
    }

    function allowSender(address sender, string calldata notes) public onlyGuardOwner {
        allowedSenders[sender] = true;
        emit SenderApproved(sender, notes);
    }

    function removeSender(address sender, string calldata notes) public onlyGuardOwner {
        delete allowedSenders[sender];
        emit SenderRemoved(sender, notes);
    }

    function allowReceiver(address receiver, string calldata notes) public onlyGuardOwner {
        allowedReceivers[receiver] = true;
        emit ReceiverApproved(receiver, notes);
    }

    function removeReceiver(address receiver, string calldata notes) public onlyGuardOwner {
        delete allowedReceivers[receiver];
        emit ReceiverRemoved(receiver, notes);
    }

    function allowWithdrawDestination(address destination, string calldata notes) public onlyGuardOwner {
        allowedWithdrawDestinations[destination] = true;
        emit WithdrawDestinationApproved(destination, notes);
    }

    function removeWithdrawDestination(address destination, string calldata notes) public onlyGuardOwner {
        delete allowedWithdrawDestinations[destination];
        emit WithdrawDestinationRemoved(destination, notes);
    }

    function allowApprovalDestination(address destination, string calldata notes) public onlyGuardOwner {
        allowedApprovalDestinations[destination] = true;
        emit ApprovalDestinationApproved(destination, notes);
    }

    function removeApprovalDestination(address destination, string calldata notes) public onlyGuardOwner {
        delete allowedApprovalDestinations[destination];
        emit ApprovalDestinationRemoved(destination, notes);
    }

    function allowDelegationApprovalDestination(address destination, string calldata notes) public onlyGuardOwner {
        allowedDelegationApprovalDestinations[destination] = true;
        emit ApprovalDestinationApproved(destination, notes);
    }

    function removeDelegationApprovalDestination(address destination, string calldata notes) public onlyGuardOwner {
        delete allowedApprovalDestinations[destination];
        emit ApprovalDestinationRemoved(destination, notes);
    }

    function allowAsset(address asset, string calldata notes) public onlyGuardOwner {
        allowedAssets[asset] = true;
        emit AssetApproved(asset, notes);
    }

    function removeAsset(address asset, string calldata notes) public onlyGuardOwner {
        delete allowedAssets[asset];
        emit AssetRemoved(asset, notes);
    }

    function whitelistLagoon(address vault, string calldata notes) public onlyGuardOwner {
        allowedLagoonVaults[vault] = true;
        allowCallSite(vault, getSelector("settleDeposit()"), notes);
        allowCallSite(vault, getSelector("settleRedeem()"), notes);
        // Lagoon v0.5.0+
        allowCallSite(vault, getSelector("settleDeposit(uint256)"), notes);
        allowCallSite(vault, getSelector("settleRedeem(uint256)"), notes);
        emit LagoonVaultApproved(vault, notes);
    }

    function isAnyTokenApproveSelector(bytes4 selector) internal pure returns (bool) {
        return selector == getSelector("approve(address,uint256)");
    }

    // Basic check if any target contract is whitelisted
    function isAllowedCallSite(address target, bytes4 selector) public view returns (bool) {
        return allowedCallSites[target][selector];
    }

    function isAllowedTarget(address target) public view returns (bool) {
        return allowedTargets[target] == true;
    }

    function isAllowedSender(address sender) public view returns (bool) {
        return allowedSenders[sender] == true;
    }

    // Assume any tokens are send back to the vault
    function isAllowedReceiver(address receiver) public view returns (bool) {
        return allowedReceivers[receiver] == true;
    }

    function isAllowedWithdrawDestination(address receiver) public view returns (bool) {
        return allowedWithdrawDestinations[receiver] == true;
    }

    function isAllowedApprovalDestination(address receiver) public view returns (bool) {
        return allowedApprovalDestinations[receiver] == true;
    }

    function isAllowedDelegationApprovalDestination(address receiver) public view returns (bool) {
        return allowedDelegationApprovalDestinations[receiver] == true;
    }

    /**
     * Are we allowed to trade/own an ERC-20.
     */
    function isAllowedAsset(address token) public view returns (bool) {
        return anyAsset || allowedAssets[token] == true;
    }

    function isAllowedLagoonVault(address vault) public view returns (bool) {
        return allowedLagoonVaults[vault] == true;
    }

    function isAllowedCowSwap(address settlement) public view returns (bool) {
        return allowedCowSwaps[settlement] == true;
    }

    function validate_transfer(bytes memory callData) public view {
        (address to, ) = abi.decode(callData, (address, uint));
        require(isAllowedWithdrawDestination(to), "validate_transfer: Receiver address not whitelisted by Guard");
    }

    function validate_approve(bytes memory callData) public view {
        (address to, ) = abi.decode(callData, (address, uint));
        require(isAllowedApprovalDestination(to), "validate_approve: Approve address does not match");
    }

    function validate_approveDelegation(bytes memory callData) public view {
        (address to, ) = abi.decode(callData, (address, uint));
        require(isAllowedDelegationApprovalDestination(to), "validate_approveDelegation: Approve delegation address does not match");
    }

    // Make this callable both internally and externally
    function _whitelistToken(address token, string calldata notes) internal {
        allowCallSite(token, getSelector("transfer(address,uint256)"), notes);
        allowCallSite(token, getSelector("approve(address,uint256)"), notes);
        allowAsset(token, notes);
    }

    // Allow ERC-20.approve() to a specific asset and this asset used as the part of path of swaps
    function whitelistToken(address token, string calldata notes) external {
        _whitelistToken(token, notes);
    }

    function whitelistTokenForDelegation(address token, string calldata notes) external {
        allowCallSite(token, getSelector("approveDelegation(address,uint256)"), notes);
        allowAsset(token, notes);
    }

    // Whitelist SwapRouter or SwapRouter02
    // The selector doesn't really matter as long as router address is correct
    function whitelistUniswapV3Router(address router, string calldata notes) external {

        // Original SwapRouter
        allowCallSite(router, getSelector("exactInput((bytes,address,uint256,uint256,uint256))"), notes);
        allowCallSite(router, getSelector("exactOutput((bytes,address,uint256,uint256,uint256))"), notes);

        // SwapRouter02
        // https://github.com/Uniswap/swap-router-contracts/blob/70bc2e40dfca294c1cea9bf67a4036732ee54303/contracts/interfaces/IV3SwapRouter.sol#L39
        // function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut);
        // https://basescan.org/address/0x5788F91Aa320e0610122fb88B39Ab8f35e50040b#writeContract
        // exactInput (0xb858183f)
        allowCallSite(router, 0xb858183f, notes);
        allowApprovalDestination(router, notes);
    }

    function whitelistUniswapV2Router(address router, string calldata notes) external {
        allowCallSite(router, getSelector("swapExactTokensForTokens(uint256,uint256,address[],address,uint256)"), notes);
        allowCallSite(router, getSelector("swapExactTokensForTokensSupportingFeeOnTransferTokens(uint256,uint256,address[],address,uint256)"), notes);
        allowApprovalDestination(router, notes);
    }

    // Enable unlimited trading space
    function setAnyAssetAllowed(bool value, string calldata notes) external onlyGuardOwner {
        anyAsset = value;
        emit AnyAssetSet(value, notes);
    }

    // Satisfy IGuard
    function validateCall(
        address sender,
        address target,
        bytes calldata callDataWithSelector
    ) external view {
        _validateCallInternal(sender, target, callDataWithSelector);
    }

    function _validateCallInternal(
        address sender,
        address target,
        bytes calldata callDataWithSelector
    ) internal view {

        // Governance can always perform any action through guard
        if(sender == getGovernanceAddress()) {
            return;
        }

        // Assume sender is trade-executor hot wallet
        require(isAllowedSender(sender), "validateCall: Sender not allowed");

        bytes4 selector = bytes4(callDataWithSelector[:4]);
        bytes calldata callData = callDataWithSelector[4:];

        // If we have dynamic whitelist/any token, we cannot check approve() call sites of
        // individual tokens
        bool anyTokenCheck = anyAsset && isAnyTokenApproveSelector(selector);

        // With anyToken, we cannot check approve() call site because we do not whitelist
        // individual token addresses
        if(!anyTokenCheck) {
            if(!isAllowedCallSite(target, selector)) {
                // Do dual check for better error message
                require(isAllowedTarget(target), "validateCall: target not allowed");
                require(isAllowedCallSite(target, selector), "validateCall: selector not allowed on the target");
            }
        }

        // Validate the function payaload.
        // Depends on the called protocol.
        if(selector == getSelector("swapExactTokensForTokens(uint256,uint256,address[],address,uint256)")) {
            validate_swapExactTokensForTokens(callData);
        } else if(selector == getSelector("swapExactTokensForTokensSupportingFeeOnTransferTokens(uint256,uint256,address[],address,uint256)")) {
            validate_swapExactTokensForTokens(callData);
        } else if(selector == getSelector("exactInput((bytes,address,uint256,uint256,uint256))")) {
            validate_exactInput(callData);
        } else if(selector == 0xb858183f) {
            // See whitelistUniswapV3Router
            // TODO: Build logic later if needed
            require(anyAsset, "validateCall: SwapRouter02 is currently supported only with anyAsset whitelist");
        } else if(selector == getSelector("multicall(bytes[])")) {
            validate_1deltaMulticall(callData);
        } else if(selector == getSelector("transfer(address,uint256)")) {
            validate_transfer(callData);
        } else if(selector == getSelector("approve(address,uint256)")) {
            validate_approve(callData);
        } else if(selector == getSelector("approveDelegation(address,uint256)")) {
            validate_approveDelegation(callData);
        } else if(selector == getSelector("supply(address,uint256,address,uint16)")) {
            validate_aaveSupply(callData);
        } else if(selector == getSelector("withdraw(address,uint256,address)")) {
            validate_aaveWithdraw(callData);
        } else if (selector == getSelector("settleDeposit()")) {
            validate_lagoonSettle(target);
        } else if (selector == getSelector("settleRedeem()")) {
            validate_lagoonSettle(target);
        } else if (selector == getSelector("settleDeposit(uint256)")) {
            validate_lagoonSettle(target);
        } else if (selector == getSelector("settleRedeem(uint256)")) {
            validate_lagoonSettle(target);
        } else if (selector == getSelector("deposit(uint256,address)") || selector == getSelector("deposit(uint256,address,address)")) {
            // Guard logic in approve() whitelist - no further checks here needed
            // validate_ERC4626Deposit(target, callData);
            // On ERC-7540 (Lagoon) - deposit takes extra adderss parameter?
        } else if (selector == getSelector("deposit(uint256,uint256,address)")) {
            // Umami non-standard ERC-4626 deposit with minShares slippage parameter
            // See UmamiDepositManager
            validate_UmamiDeposit(callData);
        } else if (selector == getSelector("redeem(uint256,uint256,address,address)")) {
            // Umami non-standard ERC-4626 redeem with minShares slippage parameter
            // See UmamiDepositManager
            validate_UmamiRedeem(callData);
        } else if (selector == getSelector("withdraw(uint256,address,address)")) {
            validate_ERC4626Withdraw(callData);
        } else if (selector == getSelector("redeem(uint256,address,address)")) {
            validate_ERC4626Redeem(callData);
        } else if (selector == getSelector("requestRedeem(uint256,address,address)")) {
            // See ERC7540DepositManager
            // The signature parameters are the same as in ERC-4626
            validate_ERC4626Redeem(callData);
        } else if (selector == getSelector("requestWithdraw(uint256,address,address)")) {
            // See ERC7540DepositManager
            // The signature parameters are the same as in ERC-4626
            validate_ERC4626Withdraw(callData);
        } else if (selector == getSelector("requestDeposit(uint256,address,address)")) {
            // Guard logic in approve() whitelist - no further checks here needed
            // See ERC7540DepositManager
        } else if (selector == getSelector("makeWithdrawRequest(uint256,address)")) {
            // Gains/Ostium modified ERC-4626
            // Check still subject to ERC-4626 redeem()
            // See GainsDepositManager
        } else if (selector == getSelector("delegateSigner((bytes32,address))")) {
            validate_orderlyDelegateSigner(callData);
        } else if (selector == getSelector("deposit((bytes32,bytes32,bytes32,uint128))")) {
            validate_orderlyDeposit(callData);
        } else if (selector == getSelector("withdraw((bytes32,bytes32,bytes32,uint128,uint128,address,address,uint64))")) {
            validate_orderlyWithdraw(callData);
        } else {
            revert("Unknown function selector");
        }
    }

    // Validate Uniswap v2 trade
    function validate_swapExactTokensForTokens(bytes memory callData) public view {
        (, , address[] memory path, address to, ) = abi.decode(callData, (uint, uint, address[], address, uint));

        require(isAllowedReceiver(to), "validate_swapExactTokensForTokens: Receiver address not whitelisted by Guard");

        address token;
        for (uint256 i = 0; i < path.length; i++) {
            token = path[i];
            require(isAllowedAsset(token), "Token not allowed");
        }        
    }

    // validate Uniswap v3 trade
    function validate_exactInput(bytes memory callData) public view {
        (ExactInputParams memory params) = abi.decode(callData, (ExactInputParams));
        
        require(isAllowedReceiver(params.recipient), "validate_exactInput: Receiver address not whitelisted by Guard");
        validateUniswapV3Path(params.path);
    }

    function validate_exactOutput(bytes memory callData) public view {
        (ExactOutputParams memory params) = abi.decode(callData, (ExactOutputParams));
        
        require(isAllowedReceiver(params.recipient), "validate_exactOutput: Receiver address not whitelisted by Guard");
        validateUniswapV3Path(params.path);
    }

    function validateUniswapV3Path(bytes memory path) public view {
        address tokenIn;
        address tokenOut;

        while (true) {
            (tokenOut, tokenIn, ) = path.decodeFirstPool();

            require(isAllowedAsset(tokenIn), "validateUniswapV3Path: Token not allowed");
            require(isAllowedAsset(tokenOut), "validateUniswapV3Path: Token not allowed");

            if (path.hasMultiplePools()) {
                path = path.skipToken();
            } else {
                break;
            }
        }
    }

    // validate 1delta trade
    function validate_1deltaMulticall(bytes memory callData) public view {
        (bytes[] memory callArr) = abi.decode(callData, (bytes[]));

        // loop through all sub-calls and validate
        for (uint i; i < callArr.length; i++) {
            bytes memory callDataWithSelector = callArr[i];

            // bytes memory has to be sliced using BytesLib
            bytes4 selector = bytes4(callDataWithSelector.slice(0, 4));
            bytes memory subCallData = callDataWithSelector.slice(4, callDataWithSelector.length - 4);

            // validate each sub-call
            if (selector == getSelector("transferERC20In(address,uint256)")) {
                validate_transferERC20In(subCallData);
            } else if (selector == getSelector("transferERC20AllIn(address)")) {
                validate_transferERC20AllIn(subCallData);
            } else if (selector == getSelector("deposit(address,address)")) {
                validate_1deltaDeposit(subCallData);
            } else if (selector == getSelector("withdraw(address,address)")) {
                validate_1deltaWithdraw(subCallData);
            } else if (selector == getSelector("flashSwapExactIn(uint256,uint256,bytes)")) {
                validate_flashSwapExactInt(subCallData);
            } else if (selector == getSelector("flashSwapExactOut(uint256,uint256,bytes)")) {
                validate_flashSwapExactOut(subCallData);
            } else if (selector == getSelector("flashSwapAllOut(uint256,bytes)")) {
                validate_flashSwapAllOut(subCallData);
            } else {
                revert("validate_1deltaMulticall: Unknown function selector");
            }
        }
    }

    // 1delta implementation: https://github.com/1delta-DAO/contracts-delegation/blob/4f27e1593c564c419ff042cdd932ed52d04216bf/contracts/1delta/modules/aave/FlashAggregator.sol#L78-L81
    function validate_transferERC20In(bytes memory callData) public view {
        (address token, ) = abi.decode(callData, (address, uint256));
        require(isAllowedAsset(token), "validate_transferERC20In: Token not allowed");
    }

    // 1delta implementation: https://github.com/1delta-DAO/contracts-delegation/blob/4f27e1593c564c419ff042cdd932ed52d04216bf/contracts/1delta/modules/aave/FlashAggregator.sol#L83-L93
    function validate_transferERC20AllIn(bytes memory callData) public view {
        (address token) = abi.decode(callData, (address));

        require(isAllowedAsset(token), "validate_transferERC20AllIn: Token not allowed");
    }
    
    // 1delta implementation: https://github.com/1delta-DAO/contracts-delegation/blob/4f27e1593c564c419ff042cdd932ed52d04216bf/contracts/1delta/modules/aave/FlashAggregator.sol#L34-L39
    function validate_1deltaDeposit(bytes memory callData) public view {
        (address token, address receiver) = abi.decode(callData, (address, address));
        require(isAllowedAsset(token), "validate_transferERC20AllIn: Token not allowed");
        require(isAllowedReceiver(receiver), "validate_deposit: Receiver address not whitelisted by Guard");
    }

    // 1delta: https://github.com/1delta-DAO/contracts-delegation/blob/4f27e1593c564c419ff042cdd932ed52d04216bf/contracts/1delta/modules/aave/FlashAggregator.sol#L71-L74
    function validate_1deltaWithdraw(bytes memory callData) public view {
        (address token, address receiver) = abi.decode(callData, (address, address));
        require(isAllowedAsset(token), "validate_withdraw: Token not allowed");
        require(isAllowedReceiver(receiver), "validate_deposit: Receiver address not whitelisted by Guard");
    }

    // ERC-4626 trading: Check we are allowed to deposit to a vault
    function validate_ERC4626Deposit(address target, bytes memory callData) public view {
        // This is no-op.
        // As ERC-4626 deposits are basically controlled by approve() permission
    }

    // ERC-4626 trading: Check we are allowed to withdraw from a vault to ourselves only
    function validate_ERC4626Withdraw(bytes memory callData) public view {
        // We can only receive from ERC-4626 to ourselves
        (, address receiver, ) = abi.decode(callData, (uint256, address, address));
        require(isAllowedReceiver(receiver), "validate_ERC4626Withdrawal: Receiver address not whitelisted by Guard");
    }

    // ERC-4626 trading: Check we are allowed to withdraw from a vault to ourselves only
    function validate_ERC4626Redeem(bytes memory callData) public view {
        // We can only receive from ERC-4626 to ourselves
        (, address receiver, ) = abi.decode(callData, (uint256, address, address));
        require(isAllowedReceiver(receiver), "validate_ERC4626Redeem: Receiver address not whitelisted by Guard");
    }

    // Umami non-standard ERC-4626 deposit: deposit(uint256 assets, uint256 minOutAfterFees, address receiver)
    // https://arbiscan.io/address/0x959f3807f0aa7921e18c78b00b2819ba91e52fef#code
    function validate_UmamiDeposit(bytes memory callData) public view {
        (, , address receiver) = abi.decode(callData, (uint256, uint256, address));
        require(isAllowedReceiver(receiver), "validate_UmamiDeposit: Receiver address not whitelisted by Guard");
    }

    // Umami non-standard ERC-4626 redeem: redeem(uint256 shares, uint256 minOutAfterFees, address receiver, address owner)
    // https://arbiscan.io/address/0x959f3807f0aa7921e18c78b00b2819ba91e52fef#code
    function validate_UmamiRedeem(bytes memory callData) public view {
        (, , address receiver, ) = abi.decode(callData, (uint256, uint256, address, address));
        require(isAllowedReceiver(receiver), "validate_UmamiRedeem: Receiver address not whitelisted by Guard");
    }

    // Validate cow swap settlement
    function validate_cowSwapSettlement(bytes memory callData) public view {
        // We can only receive from ERC-4626 to ourselves
        (, address receiver, ) = abi.decode(callData, (uint256, address, address));
        require(isAllowedReceiver(receiver), "validate_ERC4626Withdrawal: Receiver address not whitelisted by Guard");
    }

    // 1delta implementation: https://github.com/1delta-DAO/contracts-delegation/blob/4f27e1593c564c419ff042cdd932ed52d04216bf/contracts/1delta/modules/aave/MarginTrading.sol#L43-L89
    function validate_flashSwapExactInt(bytes memory callData) public view {
        (, , bytes memory path) = abi.decode(callData, (uint256, uint256, bytes));
        validate1deltaPath(path);
    }

    // Reference in 1delta: https://github.com/1delta-DAO/contracts-delegation/blob/4f27e1593c564c419ff042cdd932ed52d04216bf/contracts/1delta/modules/aave/MarginTrading.sol#L91-L103
    function validate_flashSwapExactOut(bytes memory callData) public view {
        (, , bytes memory path) = abi.decode(callData, (uint256, uint256, bytes));
        validate1deltaPath(path);
    }

    // 1delta implementation: https://github.com/1delta-DAO/contracts-delegation/blob/4f27e1593c564c419ff042cdd932ed52d04216bf/contracts/1delta/modules/aave/MarginTrading.sol#L153-L203
    function validate_flashSwapAllOut(bytes memory callData) public view {
        (, bytes memory path) = abi.decode(callData, (uint256, bytes));
        validate1deltaPath(path);
    }

    /**
     * Our implementation of 1delta path decoding and validation using similar 
     * approach as Uniswap v3 `Path.sol`
     *
     * Read more:
     * - How 1delta encodes the path: https://github.com/1delta-DAO/contracts-delegation/blob/4f27e1593c564c419ff042cdd932ed52d04216bf/test-ts/1delta/shared/aggregatorPath.ts#L5-L32
     * - How 1delta decodes the path: https://github.com/1delta-DAO/contracts-delegation/blob/4f27e1593c564c419ff042cdd932ed52d04216bf/contracts/1delta/modules/aave/MarginTrading.sol#L54-L60
     */
    function validate1deltaPath(bytes memory path) public view {
        address tokenIn;
        address tokenOut;

        while (true) {
            tokenIn = path.toAddress(0);
            tokenOut = path.toAddress(ONEDELTA_NEXT_OFFSET);

            require(isAllowedAsset(tokenIn), "validate1deltaPath: Token not allowed");
            require(isAllowedAsset(tokenOut), "validate1deltaPath: Token not allowed");

            // iterate to next slice if the path still contains multiple pools
            if (path.length >= ONEDELTA_MULTIPLE_POOLS_MIN_LENGTH) {
                path = path.slice(ONEDELTA_NEXT_OFFSET, path.length - ONEDELTA_NEXT_OFFSET);
            } else {
                break;
            }
        }
    }



    function whitelistOnedelta(address brokerProxy, address lendingPool, string calldata notes) external {
        allowCallSite(brokerProxy, getSelector("multicall(bytes[])"), notes);
        allowApprovalDestination(brokerProxy, notes);
        allowApprovalDestination(lendingPool, notes);

        // vToken has to be approved delegation for broker proxy
        // Reference in 1delta tests: https://github.com/1delta-DAO/contracts-delegation/blob/4f27e1593c564c419ff042cdd932ed52d04216bf/test-ts/1delta/aave/marginSwap.spec.ts#L206
        allowDelegationApprovalDestination(brokerProxy, notes);
    }

    /**
     * Whitelist an ERC-4626/ERC-7540 vault.
     *
     * - Callsites for deposits and redemptions
     * - Vault share and denomination tokens
     * - Any ERC-4626 extensions are not supported by this function, like special share tokens
     * - ERC-4626 withdrawal address must be always the Safe
     * - Because of non-standardisation the whitelisted function list is long
     */
    function whitelistERC4626(address vault, string calldata notes) external {
        IERC4626 vault_ = IERC4626(vault);
        address denominationToken = vault_.asset();
        address shareToken = vault;

        // ERC-4626
        allowCallSite(vault, getSelector("deposit(uint256,address)"), notes);
        allowCallSite(vault, getSelector("withdraw(uint256,address,address)"), notes);
        allowCallSite(vault, getSelector("redeem(uint256,address,address)"), notes);

        // Umami non-standard ERC-4626
        // See UmamiDepositManager()
        // https://arbiscan.io/address/0x959f3807f0aa7921e18c78b00b2819ba91e52fef#code
        allowCallSite(vault, getSelector("deposit(uint256,uint256,address)"), notes);
        allowCallSite(vault, getSelector("redeem(uint256,uint256,address,address)"), notes);

        // ERC-7540
        // See ERC7540DepositManager
        allowCallSite(vault, getSelector("deposit(uint256,address,address)"), notes);
        allowCallSite(vault, getSelector("requestRedeem(uint256,address,address)"), notes);
        allowCallSite(vault, getSelector("requestWithdraw(uint256,address,address)"), notes);
        allowCallSite(vault, getSelector("requestDeposit(uint256,address,address)"), notes);

        // Ostium/Gains
        // See GainsDepositManager
        allowCallSite(vault, getSelector("makeWithdrawRequest(uint256,address)"), notes);

        allowApprovalDestination(vault, notes);
        _whitelistToken(shareToken, notes);
        _whitelistToken(denominationToken, notes);

        emit ERC4626Approved(vault, notes);
    }

    // Aave V3 implementation: https://github.com/aave/aave-v3-core/blob/e0bfed13240adeb7f05cb6cbe5e7ce78657f0621/contracts/protocol/pool/Pool.sol#L145
    function validate_aaveSupply(bytes memory callData) public view {
        (address token, , , ) = abi.decode(callData, (address, uint, address, uint));

        require(isAllowedAsset(token), "Token not allowed");
        // require(isAllowedReceiver(wallet), "Receiver address not whitelisted by Guard");
    }

    // Aave V3 implementation: https://github.com/aave/aave-v3-core/blob/e0bfed13240adeb7f05cb6cbe5e7ce78657f0621/contracts/protocol/pool/Pool.sol#L198
    function validate_aaveWithdraw(bytes memory callData) public view {
        (address token, , address to) = abi.decode(callData, (address, uint, address));
        require(isAllowedAsset(token), "Token not allowed");
        require(isAllowedReceiver(to), "Receiver address not whitelisted by Guard");
    }

    function whitelistAaveV3(address lendingPool, string calldata notes) external {
        allowCallSite(lendingPool, getSelector("supply(address,uint256,address,uint16)"), notes);
        allowCallSite(lendingPool, getSelector("withdraw(address,uint256,address)"), notes);
        allowApprovalDestination(lendingPool, notes);
    }

    function validate_lagoonSettle(address vault) public view {
        require(isAllowedLagoonVault(vault), "Vault not allowed");
    }

    function whitelistOrderly(address orderlyVault, string calldata notes) external {
        allowCallSite(orderlyVault, getSelector("delegateSigner((bytes32,address))"), notes);
        allowCallSite(orderlyVault, getSelector("deposit((bytes32,bytes32,bytes32,uint128))"), notes);
        allowCallSite(orderlyVault, getSelector("withdraw((bytes32,bytes32,bytes32,uint128,uint128,address,address,uint64))"), notes);
        allowApprovalDestination(orderlyVault, notes);
    }

    // https://github.com/cowprotocol/contracts/tree/main/deployments
    function whitelistCowSwap(address settlementContract, address relayerContract, string calldata notes) external {
        // Interaction by special _swapAndValidateCowSwap() internal function
        allowApprovalDestination(settlementContract, notes);
        allowApprovalDestination(relayerContract, notes);
        allowedCowSwaps[settlementContract] = true;
        emit CowSwapApproved(settlementContract, notes);
    }

    /**
     * Swap and validate a CowSwap order.
     *
     * Checks that an asset manager tries to perform a legit CowSwap swap.
     *
     * 1. Validate the swap is within our allowed whitelists
     * 2. Create a Order structure
     * 3. Calculate order data hash and prefix with additional information to create order UID
     * 4. Set up data needed to call ICowSettlement.setPreSignature(orderUid, True) from Gnosis Safe as a
     * 5. Return data to call setPreSignature(orderUid, True) on CowSwap by Safe
     * 6. Offchain logic can now take over to fill the order
     *     6.a) Read the emitted order data from OrderSigned event
     *     6.b) Submit to CowSwap offchain settlement system
     *     6.c) Wait for order to be filled
     *
     * Assume receiver is the same as owner that is the same as the Gnosis Safe address.
     */
    function _swapAndValidateCowSwap(
        address settlementContract,
        address receiver,
        bytes32 appData,
        address tokenIn,
        address tokenOut,
        uint256 amountIn,
        uint256 minAmountOut
    ) internal returns (PresignDeletaCallData memory) {
        // Assume sender is trade-executor hot wallet
        require(isAllowedCowSwap(settlementContract), "swapAndValidateCowSwap: Cow Swap not enabled");
        require(isAllowedSender(msg.sender), "swapAndValidateCowSwap: Sender not asset manager");
        require(isAllowedAsset(tokenIn), "swapAndValidateCowSwap: tokenIn not allowed");
        require(isAllowedAsset(tokenOut), "swapAndValidateCowSwap: tokenOut not allowed");
        require(isAllowedReceiver(receiver), "swapAndValidateCowSwap: receiver not allowed");
        GPv2Order.Data memory order = _createCowSwapOrder(
            appData,
            receiver,
            tokenIn,
            tokenOut,
            amountIn,
            minAmountOut
        );
        return _signCowSwapOrder(
            settlementContract,
            receiver,
            order
        );
    }

    function validate_orderlyDelegateSigner(bytes memory callData) public view {
        // TODO: Implement validation
    }

    function validate_orderlyDeposit(bytes memory callData) public view {
        // TODO: Implement validation
    }

    function validate_orderlyWithdraw(bytes memory callData) public view {
        // TODO: Implement
    }

}