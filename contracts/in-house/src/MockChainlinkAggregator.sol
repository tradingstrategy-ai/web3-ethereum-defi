// SPDX-License-Identifier: MIT

pragma solidity 0.6.12;

interface IChainlinkAggregator {

    // See https://docs.chain.link/data-feeds/api-reference#latestrounddata
  function latestRoundData()
    external
    view
    returns (
      uint80 roundId,
      int256 answer,
      uint256 startedAt,
      uint256 updatedAt,
      uint80 answeredInRound
    );
}

/**
 * A mock that allows us to set the price
 */
contract MockChainlinkAggregator is IChainlinkAggregator {
    int256 currentAnswer;
    uint256 currentUpdatedAt;

    uint256 public decimals;

    constructor() public {
        decimals = 8;
    }

    function setValue(uint80 _answer) external {
        currentAnswer = _answer;
        currentUpdatedAt = block.timestamp;
    }

    function setDecimals(uint80 _decimals) external {
        decimals = _decimals;
    }

    /// Lifter from Enzyme mocks:
    function latestRoundData() external override view returns (
      uint80 roundId,
      int256 answer,
      uint256 startedAt,
      uint256 updatedAt,
      uint80 answeredInRound
    ) {
        return (0, currentAnswer, 0, currentUpdatedAt, 0);
    }
}
