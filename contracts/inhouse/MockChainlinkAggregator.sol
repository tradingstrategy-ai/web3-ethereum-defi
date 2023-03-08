// SPDX-License-Identifier: MIT

pragma solidity 0.6.12;

interface IChainlinkAggregator {

    // See https://docs.chain.link/data-feeds/api-reference#latestrounddata
    function latestRoundData()
        external
        view
        returns (
            uint80,
            int256,
            uint256,
            uint256,
            uint80
        );
}

contract MockChainlinkAggregator is IChainlinkAggregator {
    int256 answer;
    uint256 updatedAt;

    function setValue(uint80 _answer) external {
        answer = _answer;
        updatedAt = block.timestamp;
    }

    function latestRoundData() external override view returns (uint80, int256, uint256, uint256, uint80) {
        return (0, answer, 0, updatedAt, 0);
    }
}
