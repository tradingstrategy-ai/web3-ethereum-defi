"""Test the generic tokenised-fund backfill dispatcher."""

import pytest

from eth_defi.tokenised_fund import backfill


def test_parse_protocols_defaults_to_all() -> None:
    """Select every registered protocol when ``PROTOCOLS`` is unset."""

    assert backfill.parse_protocols(None) == tuple(backfill.PROTOCOL_BACKFILLS)


def test_parse_protocols_deduplicates_and_validates() -> None:
    """Normalise explicit selectors and reject unknown protocols."""

    assert backfill.parse_protocols("ondo, securitize,ondo") == ("ondo", "securitize")
    with pytest.raises(ValueError, match="Unknown tokenised-fund protocols: unknown"):
        backfill.parse_protocols("unknown")


def test_run_protocol_backfills_in_selection_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invoke only selected protocols sequentially."""

    calls: list[str] = []
    monkeypatch.setattr(backfill, "PROTOCOL_BACKFILLS", {"ondo": lambda: calls.append("ondo"), "spiko": lambda: calls.append("spiko")})

    assert backfill.run_protocol_backfills(("spiko", "ondo")) == ("spiko", "ondo")
    assert calls == ["spiko", "ondo"]


def test_main_defaults_blank_dry_run_to_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never turn a blank aggregate dry-run setting into permission to write."""

    observed: list[tuple[tuple[str, ...], str]] = []
    monkeypatch.setenv("DRY_RUN", "")
    monkeypatch.setenv("PROTOCOLS", "ondo")
    monkeypatch.setattr(backfill, "setup_console_logging", lambda **_kwargs: None)
    monkeypatch.setattr(backfill, "run_protocol_backfills", lambda protocols: observed.append((tuple(protocols), backfill.os.environ["DRY_RUN"])) or tuple(protocols))

    backfill.main()

    assert observed == [(("ondo",), "true")]
