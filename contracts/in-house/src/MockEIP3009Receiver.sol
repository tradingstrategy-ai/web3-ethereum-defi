/**
 * Receive transferWithAuthorization() EIP-3009 transfers
 *
 * https://github.com/ethereum/EIPs/issues/3010
 */

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";


contract MockEIP3009Receiver {

    bytes4 private constant _RECEIVE_WITH_AUTHORIZATION_SELECTOR = 0xef55bec6;

    IERC20 public _token;

    uint256 public amountReceived;

    constructor(IERC20 token) public {
        _token = token;
    }

    function deposit(bytes calldata receiveAuthorization)
        external
        returns (uint256)
    {
        (address from, address to, uint256 amount) = abi.decode(
            receiveAuthorization[0:96],
            (address, address, uint256)
        );
        require(to == address(this), "Recipient is not this contract");

        (bool success, ) = address(_token).call(
            abi.encodePacked(
                _RECEIVE_WITH_AUTHORIZATION_SELECTOR,
                receiveAuthorization
            )
        );
        require(success, "Failed to transfer to the forwarder");

        amountReceived += amount;

        return amountReceived;
    }
}