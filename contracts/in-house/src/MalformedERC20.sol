// SPDX-License-Identifier: MIT

pragma solidity 0.6.12;

/**
 * A broken ERC-20 implementation.
 */
contract MalformedERC20 {

    // Contains nul byte
    string public name = "Foobar\x00Boobar";

    // Symbol empty string
    string public symbol = "";

    // Total supply missing
    // Decimals missing

}