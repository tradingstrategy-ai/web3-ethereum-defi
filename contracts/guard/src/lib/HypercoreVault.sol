// Hypercore native vault guard - library contract.
//
// Uses diamond storage pattern so the library's state is stored in the
// calling contract's storage at a deterministic keccak slot, avoiding
// collisions with existing storage variables.
//
// External library functions are called via DELEGATECALL, meaning:
//   - Code lives in the deployed library (does NOT count toward the
//     calling contract's 24 KB EIP-170 limit)
//   - Storage reads/writes happen in the calling contract's context
//
// See README-Hypercore-guard.md for full documentation.

pragma solidity ^0.8.0;

// Pre-computed function selectors
bytes4 constant SEL_SEND_RAW_ACTION = 0x17938e13;  // sendRawAction(bytes)
bytes4 constant SEL_CORE_DEPOSIT = 0x2b2dfd2c;      // deposit(uint256,uint32)

library HypercoreVaultLib {

    // ----- Diamond storage -----

    bytes32 constant STORAGE_SLOT = keccak256("eth_defi.hypercore.vault.v1");

    struct HypercoreStorage {
        address allowedCoreWriter;
        address allowedCoreDepositWallet;
        mapping(address => bool) allowedHypercoreVaults;
        mapping(uint24 => bool) allowedCoreWriterActions;
    }

    function _storage() private pure returns (HypercoreStorage storage s) {
        bytes32 slot = STORAGE_SLOT;
        assembly { s.slot := slot }
    }

    // ----- CoreWriter action IDs -----

    uint24 constant VAULT_TRANSFER_ACTION = 2;
    uint24 constant SPOT_SEND_ACTION = 6;
    uint24 constant USD_CLASS_TRANSFER_ACTION = 7;

    // ----- Events -----

    event CoreWriterApproved(address coreWriter, string notes);
    event CoreDepositWalletApproved(address wallet, string notes);
    event HypercoreVaultApproved(address vault, string notes);
    event HypercoreVaultRemoved(address vault, string notes);

    // ----- Whitelisting functions (called via delegatecall from guard) -----

    function whitelistCoreWriter(
        address coreWriter,
        address coreDepositWallet,
        string calldata notes
    ) external {
        HypercoreStorage storage s = _storage();
        s.allowedCoreWriter = coreWriter;
        s.allowedCoreDepositWallet = coreDepositWallet;
        s.allowedCoreWriterActions[VAULT_TRANSFER_ACTION] = true;
        s.allowedCoreWriterActions[SPOT_SEND_ACTION] = true;
        s.allowedCoreWriterActions[USD_CLASS_TRANSFER_ACTION] = true;
        emit CoreWriterApproved(coreWriter, notes);
        emit CoreDepositWalletApproved(coreDepositWallet, notes);
    }

    function whitelistHypercoreVault(
        address vault,
        string calldata notes
    ) external {
        _storage().allowedHypercoreVaults[vault] = true;
        emit HypercoreVaultApproved(vault, notes);
    }

    function removeHypercoreVault(
        address vault,
        string calldata notes
    ) external {
        _storage().allowedHypercoreVaults[vault] = false;
        emit HypercoreVaultRemoved(vault, notes);
    }

    // ----- Validation functions (called via delegatecall from guard) -----

    /// Validate a CoreWriter sendRawAction() call.
    /// Parses the raw action bytes, checks version, action ID, and vault address.
    /// Returns the action ID and (for spotSend) the destination address.
    function validateAction(
        address target,
        bytes calldata callData
    ) external view returns (uint24 actionId, address spotSendDestination) {
        HypercoreStorage storage s = _storage();
        require(target == s.allowedCoreWriter, "CW");

        // Decode the bytes parameter from sendRawAction(bytes)
        bytes memory rawAction = abi.decode(callData, (bytes));
        uint256 len = rawAction.length;
        require(len >= 4, "CW short");

        // Parse version (first byte, must be 1)
        uint8 version;
        assembly { version := byte(0, mload(add(rawAction, 32))) }
        require(version == 1, "CW ver");

        // Parse action ID (bytes 1-3, big-endian uint24)
        // Shift right by 224 (= 256 - 32) to bring the first 4 bytes to
        // the low 32 bits, then mask with 0xFFFFFF to drop the version byte.
        assembly {
            let w := mload(add(rawAction, 32))
            actionId := and(shr(224, w), 0xFFFFFF)
        }

        require(s.allowedCoreWriterActions[actionId], "CW act");

        // Parse and validate action-specific parameters
        if (len > 4) {
            uint256 paramLen = len - 4;
            bytes memory actionParams;
            assembly {
                actionParams := mload(0x40)
                mstore(actionParams, paramLen)
                let src := add(add(rawAction, 32), 4)
                let dst := add(actionParams, 32)
                for { let i := 0 } lt(i, paramLen) { i := add(i, 32) } {
                    mstore(add(dst, i), mload(add(src, i)))
                }
                mstore(0x40, add(dst, and(add(paramLen, 31), not(31))))
            }

            if (actionId == VAULT_TRANSFER_ACTION) {
                (address vault, , ) = abi.decode(actionParams, (address, bool, uint64));
                require(s.allowedHypercoreVaults[vault], "HC vault");
            } else if (actionId == SPOT_SEND_ACTION) {
                (spotSendDestination, , ) = abi.decode(actionParams, (address, uint64, uint64));
            }
        }
    }

    /// Validate a CoreDepositWallet deposit() call.
    function validateDeposit(address target) external view {
        require(target == _storage().allowedCoreDepositWallet, "CDW");
    }

    // ----- View functions -----

    function getAllowedCoreWriter() external view returns (address) {
        return _storage().allowedCoreWriter;
    }

    function getAllowedCoreDepositWallet() external view returns (address) {
        return _storage().allowedCoreDepositWallet;
    }

    function isAllowedHypercoreVault(address vault) external view returns (bool) {
        return _storage().allowedHypercoreVaults[vault];
    }

    function isAllowedCoreWriterAction(uint24 actionId) external view returns (bool) {
        return _storage().allowedCoreWriterActions[actionId];
    }
}
