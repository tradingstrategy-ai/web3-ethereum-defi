"""Optional real-provider verification against a local catalogue snapshot."""

import hashlib
import logging
import os

import pytest

from eth_defi.erc_4626.vault_protocol.tori.disclosures import TORI_EXPOSURES
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.fee import VaultFeeMode
from eth_defi.vault.vaultdb import DEFAULT_VAULT_DATABASE, VaultDatabase
from eth_defi.vault_protocols.repairs.tori import refresh_tori_metadata
from eth_defi.vault_protocols.repairs.tori_risk_and_fees import refresh_tori_risk_and_fees

logger = logging.getLogger(__name__)


@pytest.mark.skipif(os.environ.get("RUN_TORI_EXPOSURE_LIVE_TEST") != "1", reason="Set RUN_TORI_EXPOSURE_LIVE_TEST=1 with supplied Ethereum/Monad RPCs and a local catalogue")
def test_tori_exposure_current_provider_reads() -> None:
    """Read actual current fees for every reviewed deployment without persistence.

    Load the existing local catalogue into memory, apply the prerequisite
    native classification there, and exercise all 17 current fee resolvers.
    The saved input catalogue must remain byte-identical. No historical
    Monad state, event discovery or production mutation is involved.

    :return: ``None`` after successful real-provider validation.
    """
    original_hash = hashlib.sha256(DEFAULT_VAULT_DATABASE.read_bytes()).digest()
    providers = {chain: create_multi_provider_web3(read_json_rpc_url(chain)) for chain in (1, 143)}
    database = VaultDatabase.read()
    refresh_tori_metadata(database, providers[1])
    report = refresh_tori_risk_and_fees(database, providers)
    assert report["inspected_rows"] == len(TORI_EXPOSURES)
    assert report["missing"] == []
    assert set(report["source_blocks"]) == {"1", "143"}
    for spec in (
        VaultSpec(1, "0xcd69123b3fbbfc666e1f6a501da27b564c00de54"),
        VaultSpec(1, "0x3bd9adae6643ddcddd02746b8b60075e56df9478"),
        VaultSpec(143, "0x9e98bda72ced55e562bed1dcaf2b123e95eb181e"),
    ):
        fees = database.rows[spec]["_fees"]
        assert fees.fee_mode is VaultFeeMode.internalised_skimming
        assert fees.management is not None
        assert fees.performance is not None
        assert fees.deposit == fees.withdraw == 0.0
    assert hashlib.sha256(DEFAULT_VAULT_DATABASE.read_bytes()).digest() == original_hash
    logger.info("Real-provider Tori exposure preview succeeded: %s", report)
