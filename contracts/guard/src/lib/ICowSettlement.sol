// SPDX-License-Identifier: GPL-3.0-only
//
//
// https://github.com/cowprotocol/flash-loan-router/blob/2262e6bcf29a610eac3fc9c40377baa68971aba6/src/interface/ICowSettlement.sol
// https://github.com/cowprotocol/contracts/blob/main/src/contracts/mixins/GPv2Signing.sol
// https://github.com/cowprotocol/contracts/blob/main/src/contracts/GPv2Settlement.sol
//
// See also
//   https://github.com/search?q=repo%3Amstable%2Fmetavaults%20ICowSettlement&type=code
//   https://github.com/yearn/tokenized-strategy-periphery/blob/89208dd48433decdf164505a75bcd238aedf9e93/src/Auctions/Auction.sol#L12

pragma solidity >=0.5.0;

interface ICowSettlement {
    function domainSeparator() external view returns (bytes32);
    function setPreSignature(bytes calldata orderUid, bool signed) external;
}
