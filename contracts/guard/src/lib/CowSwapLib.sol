// CowSwap order creation and signing as an external Forge library.
//
// Extracts GPv2Order hashing and CowSwap order signing out of the main
// guard contract to reduce its deployed bytecode size (EIP-170 limit).
// Uses diamond storage for the CowSwap whitelist.

pragma solidity ^0.8.13;

import {IERC20} from "./IERC20.sol";
import {ICowSettlement} from "./ICowSettlement.sol";
import {GPv2Order} from "./GPv2Order.sol";

// Gnosis Safe delegatecall information to presign CowSwap order
struct PresignCallData {
    bytes orderUid;
    address targetAddress;
    bytes data;
}

library CowSwapLib {

    // How long are our CowSwap orders valid for
    uint256 internal constant _SIGN_COOLDOWN = 20 minutes;

    // Diamond storage slot for CowSwap state
    bytes32 constant STORAGE_SLOT = keccak256("eth_defi.cowswap.v1");

    struct CowSwapStorage {
        mapping(address => bool) allowedCowSwaps;
    }

    // Let offchain logic get our order details
    event OrderSigned(
        uint256 indexed timestamp, bytes orderUid, GPv2Order.Data order, uint32 validTo, uint256 buyAmount, uint256 sellAmount
    );

    event CowSwapApproved(address settlementContract, string notes);

    function _storage() internal pure returns (CowSwapStorage storage s) {
        bytes32 slot = STORAGE_SLOT;
        assembly {
            s.slot := slot
        }
    }

    function isAllowedCowSwap(address settlement) external view returns (bool) {
        return _storage().allowedCowSwaps[settlement];
    }

    function whitelistCowSwap(address settlementContract, string calldata notes) external {
        _storage().allowedCowSwaps[settlementContract] = true;
        emit CowSwapApproved(settlementContract, notes);
    }

    /// Create a CowSwap sell order, sign it, and return the presign call data.
    ///
    /// Caller is responsible for validating sender, tokens, receiver, and
    /// CowSwap whitelist before calling this function.
    function createAndSignOrder(
        address settlementContract,
        address receiver,
        bytes32 appData,
        address tokenIn,
        address tokenOut,
        uint256 amountIn,
        uint256 minAmountOut
    ) external returns (PresignCallData memory) {
        GPv2Order.Data memory order = GPv2Order.Data({
            sellToken: IERC20(tokenIn),
            buyToken: IERC20(tokenOut),
            receiver: receiver,
            sellAmount: amountIn,
            buyAmount: minAmountOut,
            validTo: uint32(block.timestamp + _SIGN_COOLDOWN),
            appData: appData,
            feeAmount: 0,
            kind: GPv2Order.KIND_SELL,
            partiallyFillable: false,
            sellTokenBalance: GPv2Order.BALANCE_ERC20,
            buyTokenBalance: GPv2Order.BALANCE_ERC20
        });

        ICowSettlement settlement = ICowSettlement(payable(settlementContract));
        bytes32 orderDigest = GPv2Order.hash(order, settlement.domainSeparator());

        bytes memory orderUid = new bytes(GPv2Order.UID_LENGTH);
        GPv2Order.packOrderUidParams(orderUid, orderDigest, receiver, order.validTo);

        emit OrderSigned(block.timestamp, orderUid, order, order.validTo, order.buyAmount, order.sellAmount);

        return PresignCallData({
            orderUid: orderUid,
            targetAddress: settlementContract,
            data: abi.encodeWithSelector(
                ICowSettlement.setPreSignature.selector,
                orderUid,
                true
            )
        });
    }
}
