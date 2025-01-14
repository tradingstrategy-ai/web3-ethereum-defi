from eth_defi.token_analysis.blacklist import is_blacklisted_address, is_blacklisted_symbol


def test_is_blacklisted():
    assert is_blacklisted_address("0xd9ea811a51d6fe491d27c2a0442b3f577852874d")
    assert is_blacklisted_symbol("BOB")


def test_is_not_blacklisted():
    assert not is_blacklisted_address("0x4200000000000000000000000000000000000006")
    assert not is_blacklisted_symbol("WETH")