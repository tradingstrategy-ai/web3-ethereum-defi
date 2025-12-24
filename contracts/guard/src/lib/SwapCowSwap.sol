// CowSwap onchain swaps
//
// https://github.com/cowprotocol/flash-loan-router/blob/2262e6bcf29a610eac3fc9c40377baa68971aba6/src/interface/ICowSettlement.sol
// https://github.com/cowprotocol/contracts/blob/main/src/contracts/mixins/GPv2Signing.sol
//
// Examples:
//   https://github.com/search?q=repo%3Amstable%2Fmetavaults%20ICowSettlement&type=code
//   https://github.com/yearn/tokenized-strategy-periphery/blob/89208dd48433decdf164505a75bcd238aedf9e93/src/Auctions/Auction.sol#L12
//   https://github.com/InfiniFi-Labs/infinifi-protocol/blob/888c147c4d0f1848577463bc74680c86b7a5c0ff/src/integrations/farms/CoWSwapFarmBase.sol#L98

pragma solidity ^0.8.13;

import {IERC20} from "./IERC20.sol";
import {ICowSettlement} from "./ICowSettlement.sol";
import {GPv2Order} from "./GPv2Order.sol";


// Perform a whitelisted swap via CowSwap
//
// Construct order structure and does it "pre-sign" on CowSwap settlement contract
//
// Currently does the minimal sell order support only.
//
contract SwapCowSwap {

    // How long are our CowSwap orders valid for
    //
    // Could be a parametr but now we do not care
    uint256 public constant _SIGN_COOLDOWN = 20 minutes;

    // Let offchain logic get our order details
    event OrderSigned(
        uint256 indexed timestamp, bytes orderUid, GPv2Order.Data order, uint32 validTo, uint256 buyAmount, uint256 sellAmount
    );

    // Gnosis Safe delegatecall information to presign CowSwap order
    struct PresignDeletaCallData {
        bytes orderUid;
        address targetAddress;
        bytes data;
    }

    // Perform sell (exact in) order creation with a max slippage limit
    // Copied from https://github.com/InfiniFi-Labs/infinifi-protocol/blob/888c147c4d0f1848577463bc74680c86b7a5c0ff/src/integrations/farms/CoWSwapFarmBase.sol#L71
    function _createCowSwapOrder(bytes32 appdata, address receiver, address _tokenIn, address _tokenOut, uint256 _amountIn, uint256 _minAmountOut)
        internal
        view
        returns (GPv2Order.Data memory)
    {
        return GPv2Order.Data({
            sellToken: IERC20(_tokenIn),
            buyToken: IERC20(_tokenOut),
            receiver: receiver,
            sellAmount: _amountIn,
            buyAmount: _minAmountOut,
            validTo: uint32(block.timestamp + _SIGN_COOLDOWN),
            appData: appdata,
            feeAmount: 0,
            kind: GPv2Order.KIND_SELL,
            partiallyFillable: false,
            sellTokenBalance: GPv2Order.BALANCE_ERC20,
            buyTokenBalance: GPv2Order.BALANCE_ERC20
        });
    }

    // Copied from https://github.com/InfiniFi-Labs/infinifi-protocol/blob/888c147c4d0f1848577463bc74680c86b7a5c0ff/src/integrations/farms/CoWSwapFarmBase.sol#L93C1-L102C6
    // Takes the order structure and prepares order UID.
    // This order UID must be passed to Gnosis Safe delegatecall to be called at ICowSettlement.setPreSignature(orderUid, true)
    function _signCowSwapOrder(address settlementContract, address owner, GPv2Order.Data memory order) internal returns (PresignDeletaCallData memory) {
        ICowSettlement settlement = ICowSettlement(payable(settlementContract));
        bytes32 orderDigest = GPv2Order.hash(order, settlement.domainSeparator());

        // Fill in order UID bytes
        bytes memory orderUid = new bytes(GPv2Order.UID_LENGTH);
        GPv2Order.packOrderUidParams(orderUid, orderDigest, owner, order.validTo);

        // Notify our offchain logic about the created order UID
        emit OrderSigned(block.timestamp, orderUid, order, order.validTo, order.buyAmount, order.sellAmount);

        return PresignDeletaCallData({
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