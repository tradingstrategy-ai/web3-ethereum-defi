/**
 * Check for legit trade execution actions.
 *
 */

pragma solidity ^0.8.0;

import "@openzeppelin/access/Ownable.sol";
import "./lib/Path.sol";
import "./IGuard.sol";

/**
 * Prototype guard implementation.
 *
 * - Hardcoded actions for Uniswap v2, v3, 1delta
 *
 */
contract GuardV0 is IGuard, Ownable {
    using Path for bytes;
    using BytesLib for bytes;

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

    // Allowed ERC20.approve()
    mapping(address target => mapping(bytes4 selector => bool allowed)) public allowedCallSites;

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

    // Allowed routers
    mapping(address destination => bool allowed) public allowedDelegationApprovalDestinations;

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

    constructor() Ownable() {
    }

    function getSelector(string memory _func) internal pure returns (bytes4) {
        // https://solidity-by-example.org/function-selector/
        return bytes4(keccak256(bytes(_func)));
    }

    /**
     * Get the address of the proto DAO
     */
    function getGovernanceAddress() public view returns (address) {
        return owner();
    }

    /**
     * Track version during internal development.
     *
     * We bump up when new whitelistings added.
     */
    function getInternalVersion() public pure returns (uint8) {
        return 1;
    }

    function allowCallSite(address target, bytes4 selector, string calldata notes) public onlyOwner {
        allowedCallSites[target][selector] = true;
        callSiteCount++;
        emit CallSiteApproved(target, selector, notes);
    }

    function removeCallSite(address target, bytes4 selector, string calldata notes) public onlyOwner {
        delete allowedCallSites[target][selector];
        emit CallSiteRemoved(target, selector, notes);
    }

    function allowSender(address sender, string calldata notes) public onlyOwner {
        allowedSenders[sender] = true;
        emit SenderApproved(sender, notes);
    }

    function removeSender(address sender, string calldata notes) public onlyOwner {
        delete allowedSenders[sender];
        emit SenderRemoved(sender, notes);
    }

    function allowReceiver(address receiver, string calldata notes) public onlyOwner {
        allowedReceivers[receiver] = true;
        emit ReceiverApproved(receiver, notes);
    }

    function removeReceiver(address receiver, string calldata notes) public onlyOwner {
        delete allowedReceivers[receiver];
        emit ReceiverRemoved(receiver, notes);
    }

    function allowWithdrawDestination(address destination, string calldata notes) public onlyOwner {
        allowedWithdrawDestinations[destination] = true;
        emit WithdrawDestinationApproved(destination, notes);
    }

    function removeWithdrawDestination(address destination, string calldata notes) public onlyOwner {
        delete allowedWithdrawDestinations[destination];
        emit WithdrawDestinationRemoved(destination, notes);
    }

    function allowApprovalDestination(address destination, string calldata notes) public onlyOwner {
        allowedApprovalDestinations[destination] = true;
        emit ApprovalDestinationApproved(destination, notes);
    }

    function removeApprovalDestination(address destination, string calldata notes) public onlyOwner {
        delete allowedApprovalDestinations[destination];
        emit ApprovalDestinationRemoved(destination, notes);
    }

    function allowDelegationApprovalDestination(address destination, string calldata notes) public onlyOwner {
        allowedDelegationApprovalDestinations[destination] = true;
        emit ApprovalDestinationApproved(destination, notes);
    }

    function removeDelegationApprovalDestination(address destination, string calldata notes) public onlyOwner {
        delete allowedApprovalDestinations[destination];
        emit ApprovalDestinationRemoved(destination, notes);
    }

    function allowAsset(address asset, string calldata notes) public onlyOwner {
        allowedAssets[asset] = true;
        emit AssetApproved(asset, notes);
    }

    function removeAsset(address asset, string calldata notes) public onlyOwner {
        delete allowedAssets[asset];
        emit AssetRemoved(asset, notes);
    }

    // Basic check if any target contract is whitelisted
    function isAllowedCallSite(address target, bytes4 selector) public view returns (bool) {
        return allowedCallSites[target][selector];
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

    function isAllowedAsset(address token) public view returns (bool) {
        return allowedAssets[token] == true;
    }

    function validate_transfer(bytes memory callData) public view {
        (address to, ) = abi.decode(callData, (address, uint));
        require(isAllowedWithdrawDestination(to), "Receiver address does not match");
    }

    function validate_approve(bytes memory callData) public view {
        (address to, ) = abi.decode(callData, (address, uint));
        require(isAllowedApprovalDestination(to), "Approve address does not match");
    }

    function validate_approveDelegation(bytes memory callData) public view {
        (address to, ) = abi.decode(callData, (address, uint));
        require(isAllowedDelegationApprovalDestination(to), "Approve delegation address does not match");
    }

    function whitelistToken(address token, string calldata notes) external {
        allowCallSite(token, getSelector("transfer(address,uint256)"), notes);
        allowCallSite(token, getSelector("approve(address,uint256)"), notes);
        allowAsset(token, notes);
    }

    function whitelistTokenForDelegation(address token, string calldata notes) external {
        allowCallSite(token, getSelector("approveDelegation(address,uint256)"), notes);
        allowAsset(token, notes);
    }

    function validateCall(
        address sender,
        address target,
        bytes calldata callDataWithSelector
    ) external view {

        if(sender == getGovernanceAddress()) {
            // Governance can manually recover any issue
            return;
        }

        require(isAllowedSender(sender), "Sender not allowed");

        // Assume sender is trade-executor hot wallet

        bytes4 selector = bytes4(callDataWithSelector[:4]);
        bytes calldata callData = callDataWithSelector[4:];
        require(isAllowedCallSite(target, selector), "Call site not allowed");

        if(selector == getSelector("swapExactTokensForTokens(uint256,uint256,address[],address,uint256)")) {
            validate_swapExactTokensForTokens(callData);
        } else if(selector == getSelector("exactInput((bytes,address,uint256,uint256,uint256))")) {
            validate_exactInput(callData);
        } else if(selector == getSelector("multicall(bytes[])")) {
            validate_1deltaMulticall(callData);
        } else if(selector == getSelector("transfer(address,uint256)")) {
            validate_transfer(callData);
        } else if(selector == getSelector("approve(address,uint256)")) {
            validate_approve(callData);
        } else if(selector == getSelector("approveDelegation(address,uint256)")) {
            validate_approveDelegation(callData);
        } else {
            revert("Unknown function selector");
        }
    }

    // Validate Uniswap v2 trade
    function validate_swapExactTokensForTokens(bytes memory callData) public view {
        (, , address[] memory path, address to, ) = abi.decode(callData, (uint, uint, address[], address, uint));

        require(isAllowedReceiver(to), "Receiver address does not match");

        address token;
        for (uint i = 0; i < path.length; i++) {
            token = path[i];
            require(isAllowedAsset(token), "Token not allowed");
        }        
    }

    function whitelistUniswapV2Router(address router, string calldata notes) external {
        allowCallSite(router, getSelector("swapExactTokensForTokens(uint256,uint256,address[],address,uint256)"), notes);
        allowApprovalDestination(router, notes);
    }

    // validate Uniswap v3 trade
    function validate_exactInput(bytes memory callData) public view {
        (ExactInputParams memory params) = abi.decode(callData, (ExactInputParams));
        
        require(isAllowedReceiver(params.recipient), "Receiver address does not match");
        validateUniswapV3Path(params.path);
    }

    function validate_exactOutput(bytes memory callData) public view {
        (ExactOutputParams memory params) = abi.decode(callData, (ExactOutputParams));
        
        require(isAllowedReceiver(params.recipient), "Receiver address does not match");
        validateUniswapV3Path(params.path);
    }

    function validateUniswapV3Path(bytes memory path) public view {
        address tokenIn;
        address tokenOut;

        while (true) {
            (tokenOut, tokenIn, ) = path.decodeFirstPool();

            require(isAllowedAsset(tokenIn), "Token not allowed");
            require(isAllowedAsset(tokenOut), "Token not allowed");

            if (path.hasMultiplePools()) {
                path = path.skipToken();
            } else {
                break;
            }
        }
    }

    function whitelistUniswapV3Router(address router, string calldata notes) external {
        allowCallSite(router, getSelector("exactInput((bytes,address,uint256,uint256,uint256))"), notes);
        allowCallSite(router, getSelector("exactOutput((bytes,address,uint256,uint256,uint256))"), notes);
        allowApprovalDestination(router, notes);
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
                validate_deposit(subCallData);
            } else if (selector == getSelector("withdraw(address,address)")) {
                validate_withdraw(subCallData);
            } else if (selector == getSelector("flashSwapExactIn(uint256,uint256,bytes)")) {
                validate_flashSwapExactInt(subCallData);
            } else if (selector == getSelector("flashSwapExactOut(uint256,uint256,bytes)")) {
                validate_flashSwapExactOut(subCallData);
            } else if (selector == getSelector("flashSwapAllOut(uint256,bytes)")) {
                validate_flashSwapAllOut(subCallData);
            } else {
                revert("Unknown function selector");
            }
        }
    }

    function validate_transferERC20In(bytes memory callData) public view {
        (address token, ) = abi.decode(callData, (address, uint256));
        require(isAllowedAsset(token), "Token not allowed");
    }

    function validate_transferERC20AllIn(bytes memory callData) public view {}
    
    function validate_deposit(bytes memory callData) public view {
        (address token, address receiver) = abi.decode(callData, (address, address));
        
        require(isAllowedAsset(token), "Token not allowed");
        require(isAllowedReceiver(receiver), "Receiver address does not match");
    }

    function validate_withdraw(bytes memory callData) public view {}
    
    function validate_flashSwapExactInt(bytes memory callData) public view {
        (, , bytes memory path) = abi.decode(callData, (uint256, uint256, bytes));

        validate1deltaPath(path);
    }

    function validate_flashSwapExactOut(bytes memory callData) public view {
        (, , bytes memory path) = abi.decode(callData, (uint256, uint256, bytes));

        validate1deltaPath(path);
    }

    function validate_flashSwapAllOut(bytes memory callData) public view {}

    /// @dev The length of the bytes encoded address
    uint256 private constant ADDR_SIZE = 20;
    /// @dev The length of the bytes encoded fee
    uint256 private constant FEE_SIZE = 3;

    uint256 private constant ID_SIZE = 1;
    uint256 private constant FLAG_SIZE = 1;
    uint256 private constant OFFSET_TILL_ID = ADDR_SIZE + FEE_SIZE;

    /// @dev The offset of a single token address and pool fee
    uint256 private constant NEXT_OFFSET = ADDR_SIZE + FEE_SIZE + ID_SIZE + FLAG_SIZE;
    /// @dev The offset of an encoded pool key
    uint256 private constant POP_OFFSET = NEXT_OFFSET + ADDR_SIZE;
    /// @dev The minimum length of an encoding that contains 2 or more pools
    uint256 private constant MULTIPLE_POOLS_MIN_LENGTH = POP_OFFSET + NEXT_OFFSET;

    function validate1deltaPath(bytes memory path) public view {
        address tokenIn;
        address tokenOut;

        while (true) {
            tokenIn = path.toAddress(0);
            tokenOut = path.toAddress(NEXT_OFFSET);

            require(isAllowedAsset(tokenIn), "Token not allowed");
            require(isAllowedAsset(tokenOut), "Token not allowed");

            // get next slice if the path still has multiple pools
            if (path.length >= MULTIPLE_POOLS_MIN_LENGTH) {
                path = path.slice(NEXT_OFFSET, path.length - NEXT_OFFSET);
            } else {
                break;
            }
        }
    }

    function whitelistOnedelta(address brokerProxy, address lendingPool, string calldata notes) external {
        allowCallSite(brokerProxy, getSelector("multicall(bytes[])"), notes);
        allowApprovalDestination(brokerProxy, notes);
        allowDelegationApprovalDestination(brokerProxy, notes);
        allowApprovalDestination(lendingPool, notes);
    }
}