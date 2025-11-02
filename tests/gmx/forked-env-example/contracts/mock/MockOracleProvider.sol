// SPDX-License-Identifier: BUSL-1.1
pragma solidity ^0.8.0;

import "../interfaces/IGmxV2.sol";

/**
 * @title MockOracleProvider
 * Mock oracle provider for testing that returns preset prices without validation
 * @dev This bypasses the Chainlink Data Stream signature verification for fork testing
 * @dev Implements IOracleProvider interface as expected by GMX
 */

contract MockOracleProvider /* is IOracleProvider */ {
    mapping(address => Price.Props) public tokenPrices;

    /// Set price for a token
    function setPrice(address token, uint256 minPrice, uint256 maxPrice) external {
        tokenPrices[token].min = minPrice;
        tokenPrices[token].max = maxPrice;
    }

    /// Get prices for a token (called by Oracle during validation)
    /// @dev Returns the preset prices without any validation
    /// @dev Implements IOracleProvider.getOraclePrice - returns OracleUtils.ValidatedPrice struct
    function getOraclePrice(
        address token,
        bytes memory /* data */
    ) external returns (OracleUtils.ValidatedPrice memory validatedPrice) {
        Price.Props memory price = tokenPrices[token];

        validatedPrice.token = token;
        validatedPrice.min = price.min;
        validatedPrice.max = price.max;
        validatedPrice.timestamp = block.timestamp;
        validatedPrice.provider = address(this);

        return validatedPrice;
    }

    /// Should adjust timestamp - required by IOracleProvider
    /// @dev Returns false for this mock (no timestamp adjustment needed)
    function shouldAdjustTimestamp() external pure returns (bool) {
        return false;
    }

    /// Is Chainlink on-chain provider - required by IOracleProvider
    /// @dev Returns false for this mock (not a Chainlink provider)
    function isChainlinkOnChainProvider() external pure returns (bool) {
        return false;
    }
}
