pragma solidity >=0.6.0 <0.8.0;

import "./ERC20_flat.sol";

/**
 * None of the libraries provide a contract that allows mock us decimals of ERC-20 token, needed for USDC mocks
 */
contract ERC20MockDecimals is ERC20 {

    uint8 private _decimals;

    constructor(
        string memory name,
        string memory symbol,
        uint256 supply,
        uint8 __decimals
    ) public ERC20(name, symbol) {
        _mint(msg.sender, supply);
        _decimals = __decimals;
    }

    function decimals() public view virtual override returns (uint8) {
        return _decimals;
    }
}