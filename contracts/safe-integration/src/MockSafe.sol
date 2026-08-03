// SPDX-License-Identifier: LGPL-3.0-only
pragma solidity >=0.8.0;

/**
 * Mock enableModule()
 */
contract MockSafe {
    address public module;

    error NotAuthorized(address unacceptedAddress);

    receive() external payable {}

    function enableModule(address _module) external {
        module = _module;
    }

    function exec(
        address payable to,
        uint256 value,
        bytes calldata data
    ) external {
        bool success;
        bytes memory response;
        (success, response) = to.call{value: value}(data);
        if (!success) {
            assembly {
                revert(add(response, 0x20), mload(response))
            }
       }
    }

    function execTransactionFromModule(
        address payable to,
        uint256 value,
        bytes calldata data,
        uint8 operation
    ) external returns (bool success) {
        if (msg.sender != module) revert NotAuthorized(msg.sender);
        if (operation == 1) (success, ) = to.delegatecall(data);
        else (success, ) = to.call{value: value}(data);
    }

    /// Mirror the Safe module API used by Zodiac Module.execAndReturnData().
    function execTransactionFromModuleReturnData(
        address payable to,
        uint256 value,
        bytes calldata data,
        uint8 operation
    ) external returns (bool success, bytes memory returnData) {
        if (msg.sender != module) revert NotAuthorized(msg.sender);
        if (operation == 1) (success, returnData) = to.delegatecall(data);
        else (success, returnData) = to.call{value: value}(data);
    }
}
