/**
 * Purchase shares using USDC without approve()
 *
 * https://github.com/ethereum/EIPs/issues/3010
 */

pragma solidity 0.6.12;

import "./IEIP3009.sol";

/**
 * Copy-pated because Enzyme compiler version differences.
 */
interface ITermsOfService {
    function canAddressProceed(address sender) external returns (bool accepted);
    function signTermsOfServiceBehalf(address signer, bytes32 hash, bytes calldata signature, bytes calldata metadata) external;
}



interface IEnzymeComptroller {
    function buySharesOnBehalf(
        address _buyer,
        uint256 _investmentAmount,
        uint256 _minSharesQuantity
    ) external returns (uint256 sharesReceived_);
}



/**
 * Purchase shares for the user using USDC and update terms of service.
 *
 * - Provide EIP-3009 wrapper around Enzyme's buyShares() function
 *
 * - Support receiveWithAuthorization() hooks
 *
 * - Support transferWithAuthorization() hooks
 *
 * - No approve() and extra pop-up needed when depositd to the vault
 *
 */
contract TermedVaultUSDCPaymentForwarder {

    // USDC contract
    IEIP3009 public token;

    // Terms of service acceptance management contract
    ITermsOfService public termsOfService;

    // The comptroller of the vault for which we are buying shares
    //
    // You can get this from vault by asking getAccessor()
    //
    IEnzymeComptroller public comptroller;

    // Total USDC that has passed through this contract
    uint256 public amountProxied;

    constructor(IEIP3009 _token, IEnzymeComptroller _comptroller, ITermsOfService _termsOfService) public {
        token = _token;
        comptroller = _comptroller;
        termsOfService = _termsOfService;
    }

    /**
     * An interface flag to separate us from VaultUSDCPaymentForwarder for legacy compat.
     */
    function isTermsOfServiceEnabled() public pure returns (bool) {
        return true;
    }

    /**
     *
     */
    function buySharesOnBehalfUsingTransferWithAuthorizationAndTermsOfService(
        address from,
        address to,
        uint256 value,
        uint256 validAfter,
        uint256 validBefore,
        bytes32 nonce,
        uint8 v,
        bytes32 r,
        bytes32 s,
        uint256 minSharesQuantity,
        bytes32 termsOfServiceHash,
        bytes calldata termsOfServiceSignature
    )
        external
        returns (uint256)
    {

        // Check terms of service is up-to-date for this user
        // (Or what frontend thought when it created the transaction)
        if(termsOfServiceHash != bytes32(0)) {
            // Forward signature payload to the terms of service manager
            // TODO: If we pass any signTermsOfServiceBehalf(metadata) here we get
            // Error: Compiler error (/Users/distiller/project/libsolidity/codegen/LValue.cpp:54):Stack too deep, try removing local variables.,
            // and thus metadata passing is removed
            termsOfService.signTermsOfServiceBehalf(from, termsOfServiceHash, termsOfServiceSignature, "");
        }

        require(termsOfService.canAddressProceed(from), "Terms of service check failed, cannot proceed to deposit");

        // Call EIP-3009 token and ask it to transfer the amount of tokens
        // tok this contract from the sender
        token.transferWithAuthorization(
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

        // Buy Enzyme vaults on behalf of this user
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