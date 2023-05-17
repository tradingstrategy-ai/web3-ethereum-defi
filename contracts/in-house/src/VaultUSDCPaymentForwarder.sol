/**
 * Purchase shares using USDC without approve()
 *
 * https://github.com/ethereum/EIPs/issues/3010
 */

pragma solidity 0.6.12;

import "./IEIP3009.sol";

interface IEnzymeComptroller {
    function buySharesOnBehalf(
        address _buyer,
        uint256 _investmentAmount,
        uint256 _minSharesQuantity
    ) external returns (uint256 sharesReceived_);
}

/**
 * Purchase shares for the user using USDC.receiveWithAuthorization()
 *
 * No extra approval step needed.
 *
 */
contract VaultUSDCPaymentForwarder {

    // USDC contract
    IEIP3009 public token;

    // The comptroller of the vault for which we are buying shares
    IEnzymeComptroller public comptroller;

    // Total USDC that has passed through this contract
    uint256 public amountProxied;

    constructor(IEIP3009 _token, IEnzymeComptroller _comptroller) public {
        token = _token;
        comptroller = _comptroller;
    }

    function buySharesOnBehalf(
        address from,
        address to,
        uint256 value,
        uint256 validAfter,
        uint256 validBefore,
        bytes32 nonce,
        uint8 v,
        bytes32 r,
        bytes32 s,
        uint256 minSharesQuantity
    )
        public
        returns (uint256)
    {
        require(to == address(this), "Recipient is not this contract");

        // Call EIP-3009 token and ask it to transfer the amount of tokens
        // tok this contract from the sender
        token.receiveWithAuthorization(
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

        token.approve(address(comptroller), value);
        uint256 sharesReceived = comptroller.buySharesOnBehalf(
            msg.sender,
            value,
            minSharesQuantity
        );

        // Increase the internal ledger of how much shares purchases
        // we have proxied
        amountProxied += value;

        return sharesReceived;
    }
}