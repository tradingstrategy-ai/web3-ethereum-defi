"""Regression tests for the address-scoped Sygnum migration helper."""

import importlib.util
from pathlib import Path

from eth_defi.tokenised_fund.sygnum.constants import FILQ_A_ETHEREUM_ADDRESS, FILQ_A_ETHEREUM_FIRST_SEEN_AT_BLOCK


def test_sygnum_backfill_is_filq_a_only() -> None:
    """Keep the migration lead scoped to the independently verified class."""

    script_path = Path(__file__).parents[2] / "scripts" / "sygnum" / "backfill-history.py"
    spec = importlib.util.spec_from_file_location("sygnum_backfill_history", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    lead = module.create_lead()
    detection = module.create_detection()
    assert lead.address == FILQ_A_ETHEREUM_ADDRESS
    assert lead.first_seen_at_block == FILQ_A_ETHEREUM_FIRST_SEEN_AT_BLOCK
    assert detection.address == FILQ_A_ETHEREUM_ADDRESS
    assert detection.first_seen_at_block == FILQ_A_ETHEREUM_FIRST_SEEN_AT_BLOCK
