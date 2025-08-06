// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.18;

/// @title VaultTypes library
/// @author Orderly_Rubick
library VaultTypes {
    struct VaultDepositFE {
        bytes32 accountId;
        bytes32 brokerHash;
        bytes32 tokenHash;
        uint128 tokenAmount;
    }

    struct VaultDeposit {
        bytes32 accountId;
        address userAddress;
        bytes32 brokerHash;
        bytes32 tokenHash;
        uint128 tokenAmount;
        uint64 depositNonce; // deposit nonce
    }

    struct VaultWithdraw {
        bytes32 accountId;
        bytes32 brokerHash;
        bytes32 tokenHash;
        uint128 tokenAmount;
        uint128 fee;
        address sender;
        address receiver;
        uint64 withdrawNonce; // withdraw nonce
    }

    struct VaultDelegate {
        bytes32 brokerHash;
        address delegateSigner;
    }

    enum VaultEnum {
        ProtocolVault,
        UserVault
    }

    struct VaultWithdraw2Contract {
        VaultEnum vaultType;
        bytes32 accountId;
        bytes32 brokerHash;
        bytes32 tokenHash;
        uint128 tokenAmount;
        uint128 fee;
        address sender;
        address receiver;
        uint64 withdrawNonce;
        uint256 clientId;
    }
}
