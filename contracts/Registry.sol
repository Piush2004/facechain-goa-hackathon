// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title Registry
/// @notice Tamper-evident registry that anchors a fingerprint (hash) of a
///         discovered social-media post to the blockchain. Anyone can later
///         re-compute the hash of the post data and call `verify` to confirm
///         the record has not been altered since it was written.
contract Registry {
    struct Record {
        bytes32 fingerprint; // keccak256 of the canonical post payload
        uint256 timestamp;   // block time the record was anchored
        string uri;          // where the matching post was found
        address submitter;   // who anchored it
    }

    Record[] public records;
    // 1-based index into `records`; 0 means "not present"
    mapping(bytes32 => uint256) private _slot;

    event Recorded(bytes32 indexed fingerprint, uint256 index, string uri, address submitter);

    /// @notice Anchor a new fingerprint. Reverts if it was already anchored.
    function record(bytes32 fingerprint, string calldata uri) external returns (uint256 index) {
        require(_slot[fingerprint] == 0, "already recorded");
        records.push(Record(fingerprint, block.timestamp, uri, msg.sender));
        index = records.length - 1;
        _slot[fingerprint] = index + 1;
        emit Recorded(fingerprint, index, uri, msg.sender);
    }

    /// @notice Re-verify a fingerprint against the on-chain record.
    /// @return found      true if this exact fingerprint is anchored
    /// @return timestamp  when it was anchored
    /// @return uri        the URI stored alongside it
    /// @return submitter  the address that anchored it
    function verify(bytes32 fingerprint)
        external
        view
        returns (bool found, uint256 timestamp, string memory uri, address submitter)
    {
        uint256 p = _slot[fingerprint];
        if (p == 0) return (false, 0, "", address(0));
        Record storage r = records[p - 1];
        return (true, r.timestamp, r.uri, r.submitter);
    }

    function count() external view returns (uint256) {
        return records.length;
    }
}
