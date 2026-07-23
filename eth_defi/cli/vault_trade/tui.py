"""Textual interface for choosing vault test deposits and redemptions."""

from dataclasses import dataclass

from eth_defi.vault.base import VaultSpec
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Input, Label, Static
from tradingstrategy.chain import ChainId

from eth_defi.cli.vault_trade.state import (
    get_latest_vault_position,
    get_vault_test_status,
    get_vault_trade_position,
)
from tradeexecutor.state.state import State


@dataclass(frozen=True)
class VaultChoice:
    """A searchable vault entry supplied by the downloaded vault universe.

    This intentionally contains only display/search fields so Textual does not
    depend on protocol-specific vault adapter objects.
    """

    vault_spec: VaultSpec
    name: str
    chain: str
    protocol: str

    @property
    def search_text(self) -> str:
        """Text searched by the new-deposit typeahead."""

        return " ".join(
            (self.vault_spec.as_string_id(), self.name, self.chain, self.protocol)
        ).lower()


@dataclass(frozen=True)
class VaultTestAction:
    """One operator-selected action returned to the sequential runner."""

    vault_spec: VaultSpec
    action: str


class VaultSearchScreen(ModalScreen[VaultChoice | None]):
    """Typeahead selector used before creating a new vault deposit test.

    Search covers vault id, name, chain and protocol.  Selecting a row closes
    the modal with its ``VaultChoice``; Escape or Cancel returns ``None``.
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, choices: list[VaultChoice]):
        """Initialise the modal with the complete searchable vault list."""

        super().__init__()
        self.choices = choices
        self.filtered_choices = choices

    def compose(self) -> ComposeResult:
        """Build the modal search input, filtered table and cancel button."""

        with Vertical(id="vault-search-dialog"):
            yield Label("New vault deposit test")
            yield Input(
                placeholder="Type vault name, chain, protocol or chain-address id",
                id="vault-search-input",
            )
            yield DataTable(id="vault-search-table", cursor_type="row")
            yield Button("Cancel", id="cancel-search")

    def on_mount(self) -> None:
        """Populate the initial unfiltered table and focus typeahead input."""

        self._refresh_table()
        self.query_one("#vault-search-input", Input).focus()

    @on(Input.Changed, "#vault-search-input")
    def filter_choices(self, event: Input.Changed) -> None:
        """Apply case-insensitive substring search after each input change."""

        needle = event.value.strip().lower()
        self.filtered_choices = [
            choice for choice in self.choices if needle in choice.search_text
        ]
        self._refresh_table()

    def _refresh_table(self) -> None:
        """Rebuild table rows so cursor indexes match ``filtered_choices``."""

        table = self.query_one("#vault-search-table", DataTable)
        table.clear(columns=True)
        table.add_columns("Vault", "Chain", "Protocol", "Vault id")
        for index, choice in enumerate(self.filtered_choices):
            table.add_row(
                choice.name,
                choice.chain,
                choice.protocol,
                choice.vault_spec.as_string_id(),
                key=str(index),
            )

    @on(DataTable.RowSelected, "#vault-search-table")
    def choose_vault(self, event: DataTable.RowSelected) -> None:
        """Return the vault represented by the activated result row."""

        if event.cursor_row < len(self.filtered_choices):
            self.dismiss(self.filtered_choices[event.cursor_row])

    @on(Button.Pressed, "#cancel-search")
    def cancel(self) -> None:
        """Handle the visible Cancel button."""

        self.dismiss(None)

    def action_cancel(self) -> None:
        """Handle the Escape binding."""

        self.dismiss(None)


class VaultTestTradeApp(App[VaultTestAction | None]):
    """Show previous vault tests and select the next manual action.

    The main table deliberately has one row per vault, based on its latest
    position.  ``n`` opens the complete universe for a new deposit; Enter is
    accepted only for an open, non-pending real deposit.
    """

    CSS = """
    #vault-test-table { height: 1fr; }
    #status-bar { dock: top; height: 1; background: $accent; color: $text; padding: 0 1; }
    #hint-bar { dock: bottom; height: 1; background: $primary; color: $text; padding: 0 1; }
    #vault-search-dialog { width: 90%; height: 80%; padding: 1 2; background: $surface; }
    #vault-search-table { height: 1fr; }
    """

    BINDINGS = [
        Binding("escape", "quit_app", "Quit"),
        Binding("q", "quit_app", "Quit"),
        Binding("n", "new_deposit", "New deposit"),
        Binding("enter", "redeem_selected", "Redeem selected"),
    ]

    def __init__(self, *, choices: list[VaultChoice], state: State):
        """Merge current universe choices with historical state-only vaults."""

        super().__init__()
        self.state = state
        self.choices = self._include_historical_choices(choices)
        self.selected_action: VaultTestAction | None = None
        self.tested_choices = self._get_tested_choices()

    def _include_historical_choices(
        self, choices: list[VaultChoice]
    ) -> list[VaultChoice]:
        """Keep tested vaults visible if they disappear from a later download."""

        result = list(choices)
        known_ids = {choice.vault_spec.as_string_id() for choice in result}
        for position in self.state.portfolio.get_all_positions():
            attempt = position.other_data.get("vault_test_attempt", {})
            vault_id = attempt.get("vault_id")
            if not vault_id or vault_id in known_ids:
                continue
            spec = VaultSpec.parse_string(vault_id, separator="-")
            pair = position.pair
            result.append(
                VaultChoice(
                    vault_spec=spec,
                    name=pair.other_data.get("vault_name")
                    or pair.exchange_name
                    or pair.base.token_symbol,
                    chain=ChainId(spec.chain_id).get_name(),
                    protocol=pair.other_data.get("vault_protocol") or "unknown",
                )
            )
            known_ids.add(vault_id)
        return result

    def _get_tested_choices(self) -> list[VaultChoice]:
        """Return one display choice for every vault with persisted history."""

        return [
            choice
            for choice in self.choices
            if get_latest_vault_position(self.state, choice.vault_spec) is not None
        ]

    def compose(self) -> ComposeResult:
        """Build the status bar, historical results table and keyboard footer."""

        yield Static(
            f"Vault tests: {len(self.tested_choices)}    n: new deposit    Enter: redeem selected",
            id="status-bar",
        )
        yield DataTable(id="vault-test-table", cursor_type="row")
        yield Static(
            "Select an open deposit to request redemption, or press n to search all vaults",
            id="hint-bar",
        )
        yield Footer()

    def on_mount(self) -> None:
        """Populate the main table from each vault's authoritative position."""

        table = self.query_one("#vault-test-table", DataTable)
        table.add_columns("Vault", "Chain", "Protocol", "Status", "Mode", "Position")
        for index, choice in enumerate(self.tested_choices):
            position = get_latest_vault_position(self.state, choice.vault_spec)
            assert position is not None
            table.add_row(
                choice.name,
                choice.chain,
                choice.protocol,
                get_vault_test_status(position),
                "simulated" if position.simulated else "real",
                str(position.position_id),
                key=str(index),
            )
        table.focus()

    def action_quit_app(self) -> None:
        """Exit without selecting another test action."""

        self.exit(None)

    def action_new_deposit(self) -> None:
        """Open typeahead over the complete current and historical universe."""

        self.push_screen(VaultSearchScreen(self.choices), self._on_vault_selected)

    def _on_vault_selected(self, choice: VaultChoice | None) -> None:
        """Translate a typeahead choice into a deposit action."""

        if choice is not None:
            self.selected_action = VaultTestAction(choice.vault_spec, "deposit")
            self.exit(self.selected_action)

    def action_redeem_selected(self) -> None:
        """Handle the Enter binding on the current historical result row."""

        table = self.query_one("#vault-test-table", DataTable)
        if table.cursor_row is None or table.cursor_row >= len(self.tested_choices):
            return
        self._redeem_row(table.cursor_row)

    @on(DataTable.RowSelected, "#vault-test-table")
    def redeem_selected(self, event: DataTable.RowSelected) -> None:
        """Handle mouse or keyboard row activation."""

        self._redeem_row(event.cursor_row)

    def _redeem_row(self, row_index: int) -> None:
        """Return a redemption action only for an actionable open deposit."""

        choice = self.tested_choices[row_index]
        position = get_vault_trade_position(
            self.state, choice.vault_spec, open_only=True
        )
        status = get_vault_test_status(position)
        pending_statuses = {
            "deposit pending",
            "redemption pending",
            "bridge out pending",
            "bridge back pending",
        }
        if (
            position is not None
            and position.is_open()
            and status not in pending_statuses
        ):
            self.selected_action = VaultTestAction(choice.vault_spec, "redeem")
            self.exit(self.selected_action)


def display_vault_test_trade_ui(
    *, choices: list[VaultChoice], state: State
) -> VaultTestAction | None:
    """Run the manual vault-test screen and return the requested action."""

    app = VaultTestTradeApp(choices=choices, state=state)
    return app.run()
