"""Regression tests for the address-scoped Sygnum migration helper."""

from eth_defi.tokenised_fund.sygnum import backfill
from eth_defi.tokenised_fund.sygnum.constants import FILQ_A_ETHEREUM_ADDRESS, FILQ_A_ETHEREUM_FIRST_SEEN_AT_BLOCK


def test_sygnum_backfill_is_filq_a_only() -> None:
    """Keep the migration lead scoped to the independently verified class."""

    lead = backfill.create_lead()
    detection = backfill.create_detection()
    assert lead.address == FILQ_A_ETHEREUM_ADDRESS
    assert lead.first_seen_at_block == FILQ_A_ETHEREUM_FIRST_SEEN_AT_BLOCK
    assert detection.address == FILQ_A_ETHEREUM_ADDRESS
    assert detection.first_seen_at_block == FILQ_A_ETHEREUM_FIRST_SEEN_AT_BLOCK
