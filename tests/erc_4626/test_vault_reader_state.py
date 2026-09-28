"""Unit tests for ERC-4626 historical reader scheduling."""

import datetime
from dataclasses import dataclass
from decimal import Decimal

from eth_defi.erc_4626.vault import VaultReaderState
from eth_defi.vault.base import VaultSpec


@dataclass(slots=True)
class DummyDenominationToken:
    """Provide the token symbol needed for TVL scheduling."""

    symbol: str


@dataclass(slots=True)
class DummyVault:
    """Provide the vault attributes used by :class:`VaultReaderState`."""

    spec: VaultSpec
    denomination_token: DummyDenominationToken
    first_seen_at_block: int | None = None

    @property
    def vault_address(self) -> str:
        """Return the vault address stored in the specification."""
        return self.spec.vault_address


@dataclass(slots=True)
class DummyCallResult:
    """Provide the multicall result fields consumed by ``on_called``."""

    timestamp: datetime.datetime
    block_identifier: int


def test_vault_reader_state_recovers_from_faded_status_after_tvl_growth() -> None:
    """Restore normal polling after a previously tiny vault gains TVL traction.

    A late deposit must clear the persisted ``faded_at`` marker. Otherwise the
    reader keeps polling a now-active vault only once per week.
    """
    vault = DummyVault(
        spec=VaultSpec(1, "0x0000000000000000000000000000000000000001"),
        denomination_token=DummyDenominationToken("USDC"),
    )
    state = VaultReaderState(vault)
    first_read_at = datetime.datetime(2026, 1, 1)  # noqa: DTZ001 - scanner timestamps are naive UTC

    state.on_called(
        DummyCallResult(timestamp=first_read_at, block_identifier=1),
        total_assets=Decimal(200),
        share_price=Decimal(1),
    )
    state.on_called(
        DummyCallResult(timestamp=first_read_at + datetime.timedelta(days=61), block_identifier=2),
        total_assets=Decimal(200),
        share_price=Decimal(1),
    )

    assert state.faded_at is not None
    assert state.get_frequency() == ("faded", datetime.timedelta(days=7))

    state.on_called(
        DummyCallResult(timestamp=first_read_at + datetime.timedelta(days=62), block_identifier=3),
        total_assets=Decimal(1_500),
        share_price=Decimal(1),
    )

    assert state.faded_at is None
    assert state.reading_restarted_count == 1
    assert state.get_frequency() == ("small_tvl", datetime.timedelta(days=1))


def test_vault_reader_state_freshness_qualification_uses_converted_tvl() -> None:
    """Enter at $1,500 and leave below $1,000 using the existing ETH rate."""
    vault = DummyVault(
        spec=VaultSpec(1, "0x0000000000000000000000000000000000000002"),
        denomination_token=DummyDenominationToken("ETH"),
    )
    state = VaultReaderState(vault)
    start = datetime.datetime(2026, 1, 1)
    for index, (assets, expected) in enumerate(
        (("0.49", False), ("0.5", True), ("0.4", True), ("0.3", False)),
        start=1,
    ):
        state.on_called(
            DummyCallResult(timestamp=start + datetime.timedelta(days=index), block_identifier=index),
            total_assets=Decimal(assets),
            share_price=Decimal(1),
        )
        assert state.freshness_qualified is expected
    restored = VaultReaderState(vault)
    restored.load(state.save())
    assert "freshness_qualified" not in state.save()
    assert restored.freshness_qualified is False


def test_vault_reader_state_unknown_conversion_cannot_certify_tvl() -> None:
    """The fallback 0.99 marker is not a verified USD quote."""
    vault = DummyVault(
        spec=VaultSpec(1, "0x0000000000000000000000000000000000000003"),
        denomination_token=DummyDenominationToken("UNRECOGNISED"),
    )
    state = VaultReaderState(vault)
    state.on_called(
        DummyCallResult(timestamp=datetime.datetime(2026, 1, 1), block_identifier=1),
        total_assets=Decimal(1_000_000),
        share_price=Decimal(1),
    )
    assert state.unsupported_token is True
    assert state.freshness_qualified is False
