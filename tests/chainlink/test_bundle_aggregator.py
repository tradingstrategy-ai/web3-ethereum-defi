"""Chainlink bundle aggregator decoding and Hypersync query tests."""

import asyncio
import datetime
from decimal import Decimal
from types import SimpleNamespace

from eth_abi import encode

from eth_defi.chainlink import bundle_aggregator
from eth_defi.chainlink.bundle_aggregator import BUNDLE_REPORT_UPDATED_TOPIC0, decode_bundle_decimal, decode_bundle_report_event, encode_bundle_data_id_topic, fetch_chainlink_bundle_reports_hypersync_async

FILQ_D_DATA_ID = bytes.fromhex("02000001230700030000000000000000")
FILQ_CACHE_ADDRESS = "0x16B53825C8ceAEA593507274D4C1AAEC9E261433"
FILQ_REPORT_BLOCK = 25_425_952
FILQ_REPORT_TIMESTAMP = 1_782_767_700


def create_filq_d_bundle() -> bytes:
    """Create the reviewed FILQ-D event payload structure.

    :return: 320-byte bundle with a one-dollar NAV in its second word.
    """

    numeric_words = (192, 100, 91_303, 3_330_000_000, 1, 1)
    tail = (15).to_bytes(32, "big") + b"Class SP Dist 1".ljust(32, b"\x00") + (12).to_bytes(32, "big") + b"KYG5R61G1161".ljust(32, b"\x00")
    return b"".join(value.to_bytes(32, "big") for value in numeric_words) + tail


def test_decode_bundle_report_event() -> None:
    """Decode FILQ-D's real Chainlink event shape and NAV scale."""

    bundle = create_filq_d_bundle()
    report = decode_bundle_report_event(
        aggregator_address=FILQ_CACHE_ADDRESS,
        topics=[BUNDLE_REPORT_UPDATED_TOPIC0, encode_bundle_data_id_topic(FILQ_D_DATA_ID), hex(FILQ_REPORT_TIMESTAMP)],
        data=encode(["bytes"], [bundle]),
        block_number=FILQ_REPORT_BLOCK,
        block_timestamp=datetime.datetime(2026, 6, 29, 21, 16, 23, tzinfo=datetime.UTC).replace(tzinfo=None),
        transaction_hash="0x162d17b2e8e340f0c7396059a524d411ed91a28948bbd5b3123ababe74f734de",
        log_index=202,
    )

    assert report.data_id == FILQ_D_DATA_ID
    assert report.bundle == bundle
    assert report.update_time == datetime.datetime(2026, 6, 29, 21, 15, tzinfo=datetime.UTC).replace(tzinfo=None)
    assert report.decode_decimal(1, 2) == 1
    assert decode_bundle_decimal(bundle, 3, 9) == Decimal("3.33")


def test_fetch_bundle_reports_builds_targeted_hypersync_query(monkeypatch) -> None:
    """Filter bundle history by cache address, event signature and data id."""

    bundle = create_filq_d_bundle()
    block = SimpleNamespace(number=FILQ_REPORT_BLOCK, timestamp=FILQ_REPORT_TIMESTAMP + 83)
    log = SimpleNamespace(
        block_number=FILQ_REPORT_BLOCK,
        log_index=202,
        address=FILQ_CACHE_ADDRESS,
        transaction_hash="0x162d17b2e8e340f0c7396059a524d411ed91a28948bbd5b3123ababe74f734de",
        topics=[BUNDLE_REPORT_UPDATED_TOPIC0, encode_bundle_data_id_topic(FILQ_D_DATA_ID), hex(FILQ_REPORT_TIMESTAMP)],
        data="0x" + encode(["bytes"], [bundle]).hex(),
    )
    response = SimpleNamespace(data=SimpleNamespace(blocks=[block], logs=[log]))

    class FakeReceiver:
        """Yield one Hypersync response and finish."""

        def __init__(self) -> None:
            self.responses = iter((response, None))

        async def recv(self):
            """Return the next fake response."""

            return next(self.responses)

    captured = {}

    async def fake_open_hypersync_stream(client, query):
        """Capture the generated query and return a fake stream."""

        await asyncio.sleep(0)
        captured["client"] = client
        captured["query"] = query
        return FakeReceiver()

    monkeypatch.setattr(bundle_aggregator, "open_hypersync_stream", fake_open_hypersync_stream)
    client = object()
    reports = asyncio.run(
        fetch_chainlink_bundle_reports_hypersync_async(
            client,
            aggregator_address=FILQ_CACHE_ADDRESS,
            start_block=FILQ_REPORT_BLOCK,
            end_block=FILQ_REPORT_BLOCK,
            data_ids={FILQ_D_DATA_ID},
        )
    )

    assert len(reports) == 1
    assert reports[0].decode_decimal(1, 2) == 1
    query = captured["query"]
    assert query.from_block == FILQ_REPORT_BLOCK
    assert query.to_block == FILQ_REPORT_BLOCK + 1
    assert query.logs[0].address == [FILQ_CACHE_ADDRESS.lower()]
    assert query.logs[0].topics == [[BUNDLE_REPORT_UPDATED_TOPIC0], [encode_bundle_data_id_topic(FILQ_D_DATA_ID)]]
