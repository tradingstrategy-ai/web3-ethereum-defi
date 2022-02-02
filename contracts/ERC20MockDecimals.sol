// SPDX-License-Identifier: MIT

pragma solidity 0.6.12;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";

/**
 * None of the libraries provide a contract that allows mock us decimals of ERC-20 token, needed for USDC mocks
 */
contract ERC20MockDecimals is ERC20 {
    constructor(
        string memory name,
        string memory symbol,
        uint256 supply,
        uint8 decimals
    ) public ERC20(name, symbol) {
        _mint(msg.sender, supply);
        _setupDecimals(decimals);
    }
}