// SPDX-License-Identifier: GPL-2.0-or-later

// From Uniswao v3
pragma solidity ^0.8.0;

/// @title Multicall
/// @notice Enables calling multiple methods in a single call to the contract
abstract contract Multicall {
    // msg.value should not be trusted from any call coming from this function.
    // WARNING: msg.value is constant across all delegatecall iterations. Batching two or more
    // performCall(target, data, value > 0) calls in one multicall() will revert on the second
    // iteration because the module's ETH balance is exhausted after the first forward. This is
    // a DoS (fails closed, no fund loss) rather than a theft risk. GMX is not affected because
    // its execution fee is sent via a single performCall — the inner GMX sub-calls run inside
    // GMX's ExchangeRouter, not through this multicall.
    function multicall(bytes[] calldata data) public payable returns (bytes[] memory results) {
        results = new bytes[](data.length);
        for (uint256 i = 0; i < data.length; i++) {
            (bool success, bytes memory result) = address(this).delegatecall(data[i]);

            if (!success) {
                // Next 5 lines from https://ethereum.stackexchange.com/a/83577
                if (result.length < 68) revert();
                assembly {
                    result := add(result, 0x04)
                }
                revert(abi.decode(result, (string)));
            }

            results[i] = result;
        }
    }
}
