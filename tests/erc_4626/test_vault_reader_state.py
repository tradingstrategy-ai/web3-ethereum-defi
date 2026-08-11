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
