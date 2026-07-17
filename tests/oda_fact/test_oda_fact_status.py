"""Test static ODA-FACT deposit and redemption status."""

import datetime

from eth_defi.erc_4626.classification import ODA_FACT_JLTXX_ADDRESS, ODA_FACT_JLTXX_CHAIN_ID
from eth_defi.tokenised_fund.kinexys.historical import OdaFactVaultHistoricalReader
from eth_defi.tokenised_fund.kinexys.vault import KINEXYS_WHITELISTED_FLOW_REASON, OdaFactVault
from eth_defi.vault.base import VaultSpec


def test_oda_fact_deposit_and_redemption_reason() -> None:
    """Kinexys public deposits and redemptions are always closed.

    Kinexys ODA-FACT fund flows are permissioned and whitelist-only. The
    adapter exposes the same closed reason through direct status methods and
    scan-record extra fields so downstream exports can display the policy
    without an additional RPC read.

    :return:
        None.
    """

    vault = OdaFactVault(
        web3=None,
        spec=VaultSpec(ODA_FACT_JLTXX_CHAIN_ID, ODA_FACT_JLTXX_ADDRESS),
    )

    extra_data = vault.fetch_scan_record_extra_data()

    assert vault.fetch_deposit_closed_reason() == KINEXYS_WHITELISTED_FLOW_REASON
    assert vault.fetch_redemption_closed_reason() == KINEXYS_WHITELISTED_FLOW_REASON
    assert extra_data["_deposit_closed_reason"] == KINEXYS_WHITELISTED_FLOW_REASON
    assert extra_data["_redemption_closed_reason"] == KINEXYS_WHITELISTED_FLOW_REASON


def test_oda_fact_historical_read_marks_flows_closed() -> None:
    """Kinexys historical price rows mark public flows as closed.

    The historical scanner does not have ERC-4626 ``maxDeposit`` or
    ``maxRedeem`` calls for ODA-FACT contracts. It therefore records the static
    Kinexys public availability policy directly in the exported open-state
    columns.

    :return:
        None.
    """

    vault = OdaFactVault(
        web3=None,
        spec=VaultSpec(ODA_FACT_JLTXX_CHAIN_ID, ODA_FACT_JLTXX_ADDRESS),
    )
    reader = OdaFactVaultHistoricalReader(vault, stateful=False)

    read = reader.process_result(
        block_number=25_452_271,
        timestamp=datetime.datetime(2026, 6, 25, 12, 0, 0, tzinfo=datetime.UTC).replace(tzinfo=None),
        call_results=[],
    )

    exported = read.export()

    assert read.deposits_open is False
    assert read.redemption_open is False
    assert exported["deposits_open"] == "false"
    assert exported["redemption_open"] == "false"
