/**
 * Test receiveWithAuthorization() EIP-3009 transfers
 *
 * https://github.com/ethereum/EIPs/issues/3010
 */

pragma solidity 0.6.12;

import "./IEIP3009.sol";

/**
 * Receive tokens on this contract and increase the internal ledger of the deposits.
 *
 */
contract MockEIP3009Receiver {

    IEIP3009 public _token;
    uint256 public amountReceived;

    constructor(IEIP3009 token) public {
        _token = token;
    }

    function deposit(
        address from,
        address to,
        uint256 value,
        uint256 validAfter,
        uint256 validBefore,
        bytes32 nonce,
        uint8 v,
        bytes32 r,
        bytes32 s
    )
        public
        returns (uint256)
    {
        // TODO: Any validation goes here
        require(to == address(this), "Recipient is not this contract");

        // Call EIP-3009 token and ask it to transfer the amount of tokens
        // tok this contract from the sender
        _token.receiveWithAuthorization(
            from,
            to,
            value,
            validAfter,
            validBefore,
            nonce,
            v,
            r,
            s
        );

        // Increase the internal ledger of received tokens
        amountReceived += value;

        return amountReceived;
    }
}