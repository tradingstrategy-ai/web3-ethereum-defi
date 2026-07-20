"""Regression tests for the address-scoped Sygnum migration helper."""

from eth_defi.tokenised_fund.sygnum import backfill
from eth_defi.tokenised_fund.sygnum.constants import FILQ_A_ETHEREUM_ADDRESS, FILQ_A_ETHEREUM_FIRST_SEEN_AT_BLOCK, FILQ_D_ETHEREUM_ADDRESS, FILQ_D_ETHEREUM_FIRST_SEEN_AT_BLOCK


def test_sygnum_backfill_defaults_to_filq_a_and_accepts_filq_d() -> None:
    """Keep both independently verified FILQ share classes address-scoped."""

    lead = backfill.create_lead()
    detection = backfill.create_detection()
    assert lead.address == FILQ_A_ETHEREUM_ADDRESS
    assert lead.first_seen_at_block == FILQ_A_ETHEREUM_FIRST_SEEN_AT_BLOCK
    assert detection.address == FILQ_A_ETHEREUM_ADDRESS
    assert detection.first_seen_at_block == FILQ_A_ETHEREUM_FIRST_SEEN_AT_BLOCK

    distributing_lead = backfill.create_lead(FILQ_D_ETHEREUM_ADDRESS, FILQ_D_ETHEREUM_FIRST_SEEN_AT_BLOCK)
    distributing_detection = backfill.create_detection(FILQ_D_ETHEREUM_ADDRESS, FILQ_D_ETHEREUM_FIRST_SEEN_AT_BLOCK)
    assert distributing_lead.address == FILQ_D_ETHEREUM_ADDRESS
    assert distributing_detection.address == FILQ_D_ETHEREUM_ADDRESS
