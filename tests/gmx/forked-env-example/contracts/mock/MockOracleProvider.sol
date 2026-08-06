// SPDX-License-Identifier: BUSL-1.1
pragma solidity ^0.8.0;

import "../interfaces/IGmxV2.sol";

/**
 * @title MockOracleProvider
 * Mock oracle provider for testing that returns preset prices without validation
 * @dev This bypasses the Chainlink Data Stream signature verification for fork testing
 * @dev Implements IOracleProvider interface as expected by GMX
 */

contract MockOracleProvider {
    /* is IOracleProvider */
    mapping(address => Price.Props) public tokenPrices;
    mapping(address => uint256) public timestampAdjustments;

    /// Set price for a token
    function setPrice(address token, uint256 minPrice, uint256 maxPrice) external {
        tokenPrices[token].min = minPrice;
        tokenPrices[token].max = maxPrice;
    }

    /// Set the GMX timestamp adjustment for a token.
    function setTimestampAdjustment(address token, uint256 adjustment) external {
        timestampAdjustments[token] = adjustment;
    }

    /// Get prices for a token (called by Oracle during validation)
    /// @dev Returns the preset prices without any validation
    /// @dev Implements IOracleProvider.getOraclePrice - returns OracleUtils.ValidatedPrice struct
    function getOraclePrice(
        address token,
        bytes memory /* data */
    )
        external
        view
        returns (OracleUtils.ValidatedPrice memory validatedPrice)
    {
        Price.Props memory price = tokenPrices[token];

        validatedPrice.token = token;
        validatedPrice.min = price.min;
        validatedPrice.max = price.max;
        validatedPrice.rawMin = price.min;
        validatedPrice.rawMax = price.max;
        validatedPrice.timestamp = block.timestamp + timestampAdjustments[token];
        validatedPrice.provider = address(this);

        return validatedPrice;
    }

    /// Should adjust timestamp - required by IOracleProvider.
    /// @dev Lets both old and current GMX Oracle implementations subtract the
    ///      configured provider-specific adjustment.
    function shouldAdjustTimestamp() external pure returns (bool) {
        return true;
    }

    /// Is Chainlink on-chain provider - required by IOracleProvider.
    /// @dev Returns true so fork tests bypass GMX's live reference-feed
    ///      deviation check. Test prices are supplied by ``setPrice``.
    function isChainlinkOnChainProvider() external pure returns (bool) {
        return true;
    }

    /// Disable the live Chainlink reference-price check for fork tests.
    function shouldCheckRefPrice() external pure returns (bool) {
        return false;
    }
}
