"""In-process export steps must not replace the scanner's log handlers."""

import logging

import pytest

from eth_defi.vault.post_processing import export_data_files, export_protocol_metadata


def test_in_process_exports_keep_scanner_log_handlers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Running both exports inside the scanner keeps its root log handlers.

    The scanner attaches its ``scan-all-chains.log`` handler to the root
    logger. The exports previously reconfigured logging from ``main()``, which
    cleared that handler, so all later cycles logged into the export files.
    Missing bucket configuration makes each export stop early after its
    ``main()`` starts, without network access.
    """
    monkeypatch.delenv("R2_ALTERNATIVE_VAULT_METADATA_BUCKET_NAME", raising=False)
    monkeypatch.delenv("R2_VAULT_METADATA_BUCKET_NAME", raising=False)

    root = logging.getLogger()
    scanner_handler = logging.NullHandler()
    root.addHandler(scanner_handler)
    try:
        handlers_before = list(root.handlers)

        assert export_data_files() is False
        assert export_protocol_metadata() is False

        assert root.handlers == handlers_before
    finally:
        root.removeHandler(scanner_handler)
