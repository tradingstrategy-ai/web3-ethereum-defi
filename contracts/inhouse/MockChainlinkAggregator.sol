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

    /// Lifter from Enzyme mocks:
    /// @return roundId_ The `roundId` value returned by the Chainlink aggregator
    /// @return answer_ The `answer` value returned by the Chainlink aggregator, inverted to USD/ETH
    /// @return startedAt_ The `startedAt` value returned by the Chainlink aggregator
    /// @return updatedAt_ The `updatedAt` value returned by the Chainlink aggregator
    /// @return answeredInRound_ The `answeredInRound` value returned by the Chainlink aggregator
    /// @dev All values are returned directly from the target Chainlink ETH/USD aggregator,
    function latestRoundData() external override view returns (uint80, int256, uint256, uint256, uint80) {
        return (0, answer, 0, updatedAt, 0);
    }
}
