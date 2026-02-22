// Mock CoreWriter for testing Hypercore vault guard integration.
//
// Deployed at 0x3333333333333333333333333333333333333333 via anvil_setCode
// to simulate the CoreWriter system contract in Anvil fork tests.
//
// Records all sendRawAction calls for test assertions.

pragma solidity ^0.8.0;

contract MockCoreWriter {

    struct RecordedAction {
        address sender;
        uint8 version;
        uint24 actionId;
        bytes params;
    }

    RecordedAction[] public actions;

    event RawAction(address indexed sender, bytes data);

    function sendRawAction(bytes calldata data) external {
        require(data.length >= 4, "MockCoreWriter: data too short");

        uint8 version = uint8(data[0]);
        uint24 actionId = uint24(uint8(data[1])) << 16
                        | uint24(uint8(data[2])) << 8
                        | uint24(uint8(data[3]));

        bytes memory params = new bytes(data.length - 4);
        for (uint256 i = 4; i < data.length; i++) {
            params[i - 4] = data[i];
        }

        actions.push(RecordedAction({
            sender: msg.sender,
            version: version,
            actionId: actionId,
            params: params
        }));

        emit RawAction(msg.sender, data);
    }

    function getActionCount() external view returns (uint256) {
        return actions.length;
    }

    function getAction(uint256 index) external view returns (
        address sender,
        uint8 version,
        uint24 actionId,
        bytes memory params
    ) {
        RecordedAction storage action = actions[index];
        return (action.sender, action.version, action.actionId, action.params);
    }
}
