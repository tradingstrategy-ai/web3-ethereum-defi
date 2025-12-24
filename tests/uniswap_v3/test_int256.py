from eth_defi.event_reader.conversion import convert_int256_bytes_to_int


def test_signed_int256():
    # https://etherscan.io/tx/0xce7c3c307d820785caa12938012372fc9366a614a6aacf8ba9ddb2b6497b7ff5#eventlog
    i = convert_int256_bytes_to_int(bytes.fromhex("fffffffffffffffffffffffffffffffffffffffffffffffffffffffffffaf878"), signed=True)
    assert i == -329608
