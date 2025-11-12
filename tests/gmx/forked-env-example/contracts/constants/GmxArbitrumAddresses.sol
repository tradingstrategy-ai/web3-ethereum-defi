// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

/**
 * GMX Synthetics V2 contract addresses on Arbitrum mainnet
 * @dev All addresses from official GMX deployments
 * @dev Source: https://github.com/gmx-io/gmx-synthetics/blob/main/docs/arbitrum-deployments.md
 * @dev Last Updated: Aug 13, 2025
 */
library GmxArbitrumAddresses {
    // ============ Core Protocol Contracts ============

    /// Central key-value store for all protocol data
    address internal constant DATA_STORE = 0xFD70de6b91282D8017aA4E741e9Ae325CAb992d8;

    /// Role-based access control
    address internal constant ROLE_STORE = 0x3c3d99FD298f679DBC2CEcd132b4eC4d0F5e6e72;

    /// Reader contract for querying protocol state
    address internal constant READER = 0x65A6CC451BAfF7e7B4FDAb4157763aB4b6b44D0E;

    /// Router contract for plugin transfers
    address internal constant ROUTER = 0x7452c558d45f8afC8c83dAe62C3f8A5BE19c71f6;

    /// Main entry point for creating deposits, withdrawals, and orders
    address internal constant EXCHANGE_ROUTER = 0x87d66368cD08a7Ca42252f5ab44B2fb6d1Fb8d15;

    /// Handles order execution logic
    address internal constant ORDER_HANDLER = 0x04315E233C1c6FfA61080B76E29d5e8a1f7B4A35;

    /// Oracle contract for price feeds
    address internal constant ORACLE = 0x7F01614cA5198Ec979B1aAd1DAF0DE7e0a215BDF;

    /// Oracle price store
    address internal constant ORACLE_STORE = 0xA8AF9B86fC47deAde1bc66B12673706615E2B011;

    /// Chainlink Data Streams oracle provider
    /// @dev Used for WETH and USDC price feeds on mainnet
    address internal constant CHAINLINK_DATA_STREAM_PROVIDER = 0xE1d5a068c5b75E0c7Ea1A9Fe8EA056f9356C6fFD;

    /// Order vault - holds collateral for pending orders
    address internal constant ORDER_VAULT = 0x31eF83a530Fde1B38EE9A18093A333D8Bbbc40D5;

    /// Deposit handler
    address internal constant DEPOSIT_HANDLER = 0x563E8cDB5Ba929039c2Bb693B78CE12dC0AAfaDa;

    /// Withdrawal handler
    address internal constant WITHDRAWAL_HANDLER = 0x1EC018d2b6ACCA20a0bEDb86450b7E27D1D8355B;

    /// Event emitter
    address internal constant EVENT_EMITTER = 0xC8ee91A54287DB53897056e12D9819156D3822Fb;

    /// Deposit vault
    address internal constant DEPOSIT_VAULT = 0xF89e77e8Dc11691C9e8757e84aaFbCD8A67d7A55;

    /// Withdrawal vault
    address internal constant WITHDRAWAL_VAULT = 0x0628D46b5D145f183AdB6Ef1f2c97eD1C4701C55;

    // ============ Markets ============

    /// ETH/USD perp market
    /// @dev Long collateral: WETH, Short collateral: USDC, Index: WETH
    address internal constant ETH_USD_MARKET = 0x70d95587d40A2caf56bd97485aB3Eec10Bee6336;

    /// BTC/USD perp market
    /// @dev Long collateral: WBTC, Short collateral: USDC, Index: WBTC
    address internal constant BTC_USD_MARKET = 0x47c031236e19d024b42f8AE6780E44A573170703;

    // ============ Tokens ============

    /// Wrapped ETH on Arbitrum
    address internal constant WETH = 0x82aF49447D8a07e3bd95BD0d56f35241523fBab1;

    /// Native USDC on Arbitrum (not bridged)
    address internal constant USDC = 0xaf88d065e77c8cC2239327C5EDb3A432268e5831;

    /// Wrapped BTC on Arbitrum
    address internal constant WBTC = 0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f;

    /// USDC.e (bridged USDC from Ethereum)
    address internal constant USDC_E = 0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8;
}
