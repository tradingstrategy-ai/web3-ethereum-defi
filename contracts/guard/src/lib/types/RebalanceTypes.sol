// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.18;

/// @title RebalanceTypes library
/// @author Orderly_Rubick
library RebalanceTypes {
    enum RebalanceStatusEnum {
        None,
        Pending,
        Succ,
        Fail
    }

    // RebalanceStatus
    struct RebalanceStatus {
        uint64 rebalanceId; // Because the mapping key rebalanceId is mod, so we need to record the real rebalanceId
        RebalanceStatusEnum burnStatus;
        RebalanceStatusEnum mintStatus;
    }
    // RebalanceBurnUploadData

    struct RebalanceBurnUploadData {
        bytes32 r;
        bytes32 s;
        uint8 v;
        uint64 rebalanceId;
        uint128 amount;
        bytes32 tokenHash;
        uint256 burnChainId;
        uint256 mintChainId;
    }

    struct RebalanceBurnCCData {
        uint32 dstDomain;
        uint64 rebalanceId;
        uint128 amount;
        bytes32 tokenHash;
        uint256 burnChainId;
        uint256 mintChainId;
        address dstVaultAddress;
    }

    struct RebalanceBurnCCFinishData {
        bool success;
        uint64 rebalanceId;
        uint128 amount;
        bytes32 tokenHash;
        uint256 burnChainId;
        uint256 mintChainId;
    }

    // RebalanceMintUploadData
    struct RebalanceMintUploadData {
        bytes32 r;
        bytes32 s;
        uint8 v;
        uint64 rebalanceId;
        uint128 amount;
        bytes32 tokenHash;
        uint256 burnChainId;
        uint256 mintChainId;
        bytes messageBytes;
        bytes messageSignature;
    }

    struct RebalanceMintCCData {
        uint64 rebalanceId;
        uint128 amount;
        bytes32 tokenHash;
        uint256 burnChainId;
        uint256 mintChainId;
        bytes messageBytes;
        bytes messageSignature;
    }

    struct RebalanceMintCCFinishData {
        bool success;
        uint64 rebalanceId;
        uint128 amount;
        bytes32 tokenHash;
        uint256 burnChainId;
        uint256 mintChainId;
    }
}
