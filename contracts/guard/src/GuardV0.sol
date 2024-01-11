/**
 * Check for legit trade execution actions.
 *
 */

pragma solidity ^0.8.0;

import "@openzeppelin/access/Ownable.sol";

interface IGuard {
    function validateCall(address sender, address target, bytes memory callDataWithSelector) external;
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

    event CallSiteApproved(address target, bytes4 selector, string notes);
    event CallSiteRemoved(address target, bytes4 selector, string notes);

    event SenderApproved(address sender, string notes);
    event SenderRemoved(address sender, string notes);

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

    function approveCallSite(address target, bytes4 selector, string calldata notes) public onlyOwner {
        allowedCallSites[target][selector] = true;
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

    function allowAsset(address sender, string calldata notes) public onlyOwner {
        allowedAssets[sender] = true;
        emit AssetApproved(sender, notes);
    }

    function removeAsset(address sender, string calldata notes) public onlyOwner {
        delete allowedAssets[sender];
        emit AssetRemoved(sender, notes);
    }

    // Basic check if any target contract is whitelisted
    function isGoodCallTarget(address target, bytes4 selector) public view returns (bool) {
        return allowedCallSites[target][selector];
    }

    function isAllowedSender(address sender) public view returns (bool) {
        return allowedSenders[sender] == true;
    }

    // For now we assume sender = generic adapter = receiver
    function isAllowedReceiver(address receiver) public view returns (bool) {
        return isAllowedSender(receiver);
    }

    function isAllowedAsset(address token) public view returns (bool) {
        return allowedAssets[token] == true;
    }

    // Validate Uniswap v2 trade
    function validate_swapTokensForExactTokens(bytes memory callData) public view {
        (, , address[] memory path, address to, ) = abi.decode(callData, (uint, uint, address[], address, uint));
        address tokenIn = path[0];
        address tokenOut = path[path.length - 1];
        require(isAllowedReceiver(to), "Receiver address does not match");
        require(isAllowedAsset(tokenIn), "Token in not allowed");
        require(isAllowedAsset(tokenOut), "Token out not allowed");
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

        require(!isAllowedSender(sender), "Sender not allowed");

        // Assume sender is trade-executor hot wallet

        bytes4 selector = bytes4(callDataWithSelector[:4]);
        require(!isGoodCallTarget(target, selector), "Call site not allowed");

        if(selector == getSelector("swapTokensForExactTokens(uint,uint,address[],address,uint)")) {
            validate_swapTokensForExactTokens(callDataWithSelector[4:]);
        } else {
            revert("Unknown function selector");
        }
    }
}