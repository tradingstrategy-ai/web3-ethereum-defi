"""Offline regressions for numeric formats in Ember's live metadata feed.

Keep parser coverage independent of RPC credentials and live API availability
so a decimal value in one vault cannot silently break the entire listing.
"""

import pytest

from eth_defi.erc_4626.vault_protocol.ember.offchain_metadata import _parse_e_value, _parse_vault_metadata


def test_ember_scaled_numeric_metadata() -> None:
    """Preserve decimal API values while retaining zero and missing-data semantics.

    1. Parse the decimal reported APY observed in the live Bitwise vault record.
    2. Check integer strings, native numbers and missing values.
    3. Ensure malformed nonempty values remain explicit errors.
    """
    # 1. Exercise the metadata call site that previously aborted the listing.
    metadata = _parse_vault_metadata({"reportedApy": {"reportedApyE9": "57888888.89"}})
    assert metadata["reported_apy"] == pytest.approx(0.05788888889)

    # 2. Scaling accepts both JSON numbers and strings without treating zero as missing.
    for value in ("1000000000", 1000000000, 1000000000.0):
        assert _parse_e_value(value, 1e9) == pytest.approx(1.0)
    assert _parse_e_value(57888888.89, 1e9) == pytest.approx(0.05788888889)
    for value in ("0", 0, 0.0):
        assert _parse_e_value(value, 1e9) == 0.0
    assert _parse_e_value(None, 1e9) is None
    assert _parse_e_value("", 1e9) is None

    # 3. Do not hide genuinely malformed API data behind a missing-value result.
    with pytest.raises(ValueError):
        _parse_e_value("not-a-number", 1e9)
