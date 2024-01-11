/**
 * Check for legit trade execution actions.
 *
 */

pragma solidity ^0.8.0;

interface IGuard {
    function validateCall(address target, bytes callData) public;
}

interface IUniswapV2Router02 {
    function swapTokensForExactTokens(
        uint amountOut,
        uint amountInMax,
        address[] calldata path,
        address to,
        uint deadline
    ) external returns (uint[] memory amounts);
}

/**
 * Prototype guard implementation.
 *
 * - Hardcoded actions for Uniswap v2, v3, 1delta
 *
 */
contract GuardV0 is IGuard, Ownable {

    // Allowed ERC20.approve()
    mapping(address target => mapping(bytes4 selector => bool allowed)) public allowedCallSites;

    // Allowed ERC-20 tokens we may receive or send in a trade
    mapping(address token => bool allowed) public allowedAssets;

    // Allowed trade executors
    mapping(address sender => bool allowed) public allowedSenders;

    // Allowed ERC20.approve()
    mapping(address target => bool allowed) public uniswapV2Routers;

    mapping(address target => bool allowed) public uniswapV2Routers;

    event CallSiteApproved(address target, bytes4 selector, string notes);
    event CallSiteRemoved(address target, bytes4 selector, string notes);

    event SenderApproved(address sender, string notes);
    event SenderRemoved(address sender, string notes);

    constructor() Ownable {
        governance = owner;
    }

    /**
     * Get the address of the proto DAO
     */
    function getGovernanceAddress() public view returns (address) {
        return owner();
    }

    function approveCallSite(address target, bytes4 selector, string notes) onlyOwner {
        allowedCallSites[target][selector] = true;
        emit CallSiteApproved(target, selector, notes);
    }

    function removeCallSite(address target, bytes4 selector) onlyOwner {
        delete allowedCallSites[target][selector];
        emit CallSiteApproved(target, selector);
    }

    function allowSender(address sender, string notes) onlyOwner {
        allowedSender[sender] = true;
        emit SenderApproved(sender, notes);
    }

    function removeSender(address sender, string notes) onlyOwner {
        delete allowedSender[sender];
        emit SenderRemoved(sender, notes);
    }

    function removeCallSite(address target, bytes4 selector, string notes) onlyOwner {
        delete allowedCallSites[target][selector];
        emit CallSiteApproved(target, selector, notes);
    }
    // Basic check if any target contract is whitelisted
    function canCall(address target, bytes4 selector) public view returns (bool) {
        return allowedCallSites[target][selector];
    }

    function isAllowedSender(address sender) public view returns (bool) {
        return allowedSenders[sender] == true;
    }

    function isAllowedAsset(address token) public view returns (bool) {
        return allowedAssets[sender] == true;
    }

    // Validate Uniswap v2 trade
    function validate_swapTokensForExactTokens(bytes callData) public {
        (uint amountOut, uint amountInMax, address[] path, address to, uint deadline) = abi.decode(callData, uint, uint, address[], address, uint);
        address tokenIn = path[0];
        address tokenOut = path[-1];
        require(isAllowedToken(tokenIn), "Token in not allowed");
        require(isAllowedToken(tokenOut), "Token out not allowed");
    }

    function validateCall(address sender, address target, bytes callData) {

        if(sender == getGovernanceAddress()) {
            // Governance can manually recover any issue
            return;
        }

        requre(!isAllowedCaller(sender), "Sender not allowed");

        // Assume sender is trade-executor hot wallet

        bytes4 selector = bytes4(callData[:4]);
        require(!canCall(target, selector), "Call site not allowed");

        if(selector == abi.encodeCall(IUniswapV2Router02.swapTokensForExactTokens)) {
            validate_swapTokensForExactTokens(callData[4:]);
        } else {
            revert("Unknown function selector");
        }
    }
}