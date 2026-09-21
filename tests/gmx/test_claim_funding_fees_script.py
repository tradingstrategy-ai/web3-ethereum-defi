"""Smoke tests for the GMX funding-fee claim script.

The script serves both an EOA and a Lagoon vault Safe from a single command
line, and can take the signing key, RPC endpoint and vault address from a
freqtrade configuration file together with its secrets file. These tests load
the script as a module and check that the configuration is read, merged and
resolved correctly, that both execution modes are identified, and that a full
run reaches the printed mode report and funding table without network access.
"""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

#: Signing key used by the non-Lagoon freqtrade configuration fixture.
EOA_PRIVATE_KEY = "0x" + "22" * 32

#: Asset manager signing key used by the Lagoon freqtrade configuration fixture.
ASSET_MANAGER_PRIVATE_KEY = "0x" + "aa" * 32

#: Lagoon vault address, deliberately lower case so the checksumming is covered.
VAULT_ADDRESS = "0x000000000000000000000000000000000000dead"

#: Checksummed form of :data:`VAULT_ADDRESS`.
VAULT_ADDRESS_CHECKSUM = "0x000000000000000000000000000000000000dEaD"

#: Gnosis Safe address standing in for the vault Safe in the Lagoon tests.
SAFE_ADDRESS = "0x0000000000000000000000000000000000000005"

#: Address standing in for the asset manager that signs and pays the gas.
ASSET_MANAGER_ADDRESS = "0x0000000000000000000000000000000000000009"

#: Chain ID of Arbitrum One, the chain the fixtures use.
ARBITRUM_CHAIN_ID = 42161


def load_claim_module() -> ModuleType:
    """Load the GMX funding-fee claim script as a Python module.

    :return:
        Imported script module without invoking its command line entry point.
    """

    repository_root = Path(__file__).resolve().parents[2]
    script_path = repository_root / "scripts" / "gmx" / "gmx_claim_funding_fees.py"
    spec = importlib.util.spec_from_file_location("gmx_claim_funding_fees", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_non_lagoon_config(path: Path, **exchange_overrides: object) -> Path:
    """Write a freqtrade configuration in the non-Lagoon (EOA) style.

    :param path:
        Destination file.
    :param exchange_overrides:
        Values merged over the default ``exchange`` section.
    :return:
        The written path.
    """

    exchange: dict = {
        "name": "gmx",
        "rpc_url": "https://eoa.example/rpc",
        "private_key": EOA_PRIVATE_KEY,
        "pair_whitelist": ["ETH/USD"],
    }
    exchange.update(exchange_overrides)
    path.write_text(json.dumps({"exchange": exchange}))
    return path


def write_lagoon_config(path: Path, **ccxt_overrides: object) -> Path:
    """Write a freqtrade configuration in the Lagoon vault style.

    :param path:
        Destination file.
    :param ccxt_overrides:
        Values merged over the default ``exchange.ccxt_config`` section.
    :return:
        The written path.
    """

    ccxt_config: dict = {
        "rpcUrl": "https://lagoon.example/rpc",
        "privateKey": ASSET_MANAGER_PRIVATE_KEY,
        "options": {"vaultAddress": VAULT_ADDRESS},
    }
    ccxt_config.update(ccxt_overrides)
    path.write_text(json.dumps({"exchange": {"name": "gmx", "ccxt_config": ccxt_config}}))
    return path


def make_args(**overrides: object) -> SimpleNamespace:
    """Build the parsed-arguments namespace the script expects.

    :param overrides:
        Attribute values replacing the defaults.
    :return:
        Namespace with every flag the script defines.
    """

    values: dict = {"rpc_url": None, "private_key": None, "vault": None, "dry_run": True}
    values.update(overrides)
    return SimpleNamespace(**values)


class FakeReader:
    """Reader double returning canned funding receipts.

    :param rows:
        Value returned by :meth:`get_per_market_claimable_funding_fees`.
    """

    def __init__(self, rows: dict) -> None:
        self.rows = rows

    def get_per_market_claimable_funding_fees(self) -> dict:
        """Return the canned per-market receipts.

        :return:
            Market symbol mapped to its claimable funding detail.
        """

        return self.rows


class FakeTxHash:
    """Transaction hash double exposing :meth:`hex` like :class:`HexBytes`."""

    @staticmethod
    def hex() -> str:
        """Return a fixed hash string.

        :return:
            Hex transaction hash.
        """

        return "0x" + "cd" * 32


def patch_runtime(
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType,
    *,
    account: str = SAFE_ADDRESS,
    rows: dict | None = None,
) -> dict:
    """Replace the script's network, wallet and reader dependencies.

    :param monkeypatch:
        Pytest monkeypatch fixture.
    :param module:
        Loaded claim script module.
    :param account:
        Funding account the wallet double reports.
    :param rows:
        Canned funding receipts; defaults to a single nonzero long receipt.
    :return:
        Dictionary recording the values the script resolved and whether it claimed.
    """

    calls: dict = {}
    receipts = {"ETH": {"long_raw": 1_500_000, "long": 3.75, "short_raw": 0, "short": 0.0}} if rows is None else rows

    def fake_create_web3(rpc_url: str) -> SimpleNamespace:
        """Record the RPC endpoint and return a Web3 double."""

        calls["rpc_url"] = rpc_url
        eth = SimpleNamespace(chain_id=ARBITRUM_CHAIN_ID, block_number=123_456)
        eth.wait_for_transaction_receipt = lambda _tx_hash: {"blockNumber": 999, "status": 1}
        return SimpleNamespace(eth=eth)

    def fake_build_wallet(_web3, private_key: str, vault_address: str | None) -> tuple:
        """Record the resolved signing key and vault address."""

        calls["private_key"] = private_key
        calls["vault_address"] = vault_address
        return SimpleNamespace(address=account), account, ASSET_MANAGER_ADDRESS

    def fake_claim(_config, _wallet) -> FakeTxHash:
        """Record that a claim was submitted."""

        calls["claimed"] = True
        return FakeTxHash()

    monkeypatch.setattr(module, "setup_console_logging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "create_multi_provider_web3", fake_create_web3)
    monkeypatch.setattr(module, "get_chain_name", lambda _chain_id: "Arbitrum")
    monkeypatch.setattr(module, "build_wallet", fake_build_wallet)
    monkeypatch.setattr(module, "GMXConfig", lambda *_args, **_kwargs: SimpleNamespace())
    monkeypatch.setattr(module, "GetClaimableFundingFees", lambda _config, _account: FakeReader(receipts))
    monkeypatch.setattr(module, "claim_all_funding_fees", fake_claim)
    return calls


def test_load_config_files_merges_config_then_secrets(tmp_path: Path) -> None:
    """The secrets file overrides the config file without losing its sibling keys."""

    module = load_claim_module()
    config_file = write_lagoon_config(tmp_path / "config.json")
    secrets_file = tmp_path / "secrets.json"
    secrets_file.write_text(json.dumps({"exchange": {"ccxt_config": {"privateKey": "0x" + "bb" * 32}}}))

    merged = module.load_config_files([str(config_file), str(secrets_file)])

    assert module.lookup_str(merged, "exchange.ccxt_config.privateKey") == "0x" + "bb" * 32
    assert module.lookup_str(merged, "exchange.ccxt_config.rpcUrl") == "https://lagoon.example/rpc"
    assert module.lookup_str(merged, "exchange.ccxt_config.options.vaultAddress") == VAULT_ADDRESS


def test_load_config_files_rejects_unusable_files(tmp_path: Path) -> None:
    """Missing, non-object and malformed files are reported as errors."""

    module = load_claim_module()

    with pytest.raises(FileNotFoundError):
        module.load_config_files([str(tmp_path / "absent.json")])

    not_an_object = tmp_path / "list.json"
    not_an_object.write_text("[1, 2]")
    with pytest.raises(ValueError):
        module.load_config_files([str(not_an_object)])

    malformed = tmp_path / "broken.json"
    malformed.write_text("{oops")
    with pytest.raises(json.JSONDecodeError):
        module.load_config_files([str(malformed)])


def test_resolve_claim_settings_reads_non_lagoon_config(tmp_path: Path) -> None:
    """A non-Lagoon config resolves to EOA mode with the key and endpoint from the file."""

    module = load_claim_module()
    config_file = write_non_lagoon_config(tmp_path / "config.json")
    file_config = module.load_config_files([str(config_file)])

    settings = module.resolve_claim_settings(make_args(), file_config)

    assert settings.private_key == EOA_PRIVATE_KEY
    assert settings.rpc_url == "https://eoa.example/rpc"
    assert settings.vault_address is None


def test_resolve_claim_settings_reads_lagoon_config(tmp_path: Path) -> None:
    """A Lagoon config resolves to vault mode with the asset manager key."""

    module = load_claim_module()
    config_file = write_lagoon_config(tmp_path / "config.json")
    file_config = module.load_config_files([str(config_file)])

    settings = module.resolve_claim_settings(make_args(), file_config)

    assert settings.private_key == ASSET_MANAGER_PRIVATE_KEY
    assert settings.rpc_url == "https://lagoon.example/rpc"
    assert settings.vault_address == VAULT_ADDRESS_CHECKSUM


def test_resolve_claim_settings_command_line_wins(tmp_path: Path) -> None:
    """Explicit flags override everything read from the configuration files."""

    module = load_claim_module()
    config_file = write_lagoon_config(tmp_path / "config.json")
    file_config = module.load_config_files([str(config_file)])
    args = make_args(rpc_url="https://cli.example/rpc", private_key="0x" + "cc" * 32, vault="0x000000000000000000000000000000000000beef")

    settings = module.resolve_claim_settings(args, file_config)

    assert settings.private_key == "0x" + "cc" * 32
    assert settings.rpc_url == "https://cli.example/rpc"
    assert settings.vault_address == "0x000000000000000000000000000000000000bEEF"


def test_resolve_claim_settings_requires_key_and_endpoint() -> None:
    """A missing key or endpoint stops the script with an actionable message."""

    module = load_claim_module()
    empty = module.load_config_files([])

    with pytest.raises(SystemExit) as key_exit:
        module.resolve_claim_settings(make_args(rpc_url="https://rpc.example"), empty)
    assert "No signing key" in str(key_exit.value)

    with pytest.raises(SystemExit) as rpc_exit:
        module.resolve_claim_settings(make_args(private_key=EOA_PRIVATE_KEY), empty)
    assert "No RPC endpoint" in str(rpc_exit.value)


def test_resolve_claim_settings_rejects_bad_vault_address(tmp_path: Path) -> None:
    """A malformed vault address is fatal instead of silently selecting EOA mode."""

    module = load_claim_module()
    config_file = write_lagoon_config(tmp_path / "config.json", options={"vaultAddress": "not-an-address"})
    file_config = module.load_config_files([str(config_file)])

    with pytest.raises(SystemExit) as exit_info:
        module.resolve_claim_settings(make_args(), file_config)

    assert "Invalid Lagoon vault address" in str(exit_info.value)


def test_main_smoke_non_lagoon_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    """A dry run driven by a non-Lagoon config reads the file and reports EOA mode."""

    module = load_claim_module()
    config_file = write_non_lagoon_config(tmp_path / "config.json")
    calls = patch_runtime(monkeypatch, module)
    monkeypatch.setattr(sys, "argv", ["gmx_claim_funding_fees.py", "--config", str(config_file), "--dry-run"])

    exit_code = module.main()
    captured = capsys.readouterr().out

    assert exit_code == 0
    assert "Execution mode: EOA" in captured
    assert "Dry run: no claim submitted." in captured
    assert calls["private_key"] == EOA_PRIVATE_KEY
    assert calls["rpc_url"] == "https://eoa.example/rpc"
    assert calls["vault_address"] is None
    assert "claimed" not in calls


def test_main_smoke_lagoon_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    """A dry run driven by a Lagoon config plus secrets file reports the vault."""

    module = load_claim_module()
    config_file = write_lagoon_config(tmp_path / "config.json")
    secrets_file = tmp_path / "secrets.json"
    secrets_file.write_text(json.dumps({"exchange": {"ccxt_config": {"privateKey": ASSET_MANAGER_PRIVATE_KEY}}}))
    calls = patch_runtime(monkeypatch, module)
    monkeypatch.setattr(sys, "argv", ["gmx_claim_funding_fees.py", "--config", str(config_file), "--secrets", str(secrets_file), "--dry-run"])

    exit_code = module.main()
    captured = capsys.readouterr().out

    assert exit_code == 0
    assert f"Execution mode: Lagoon vault {VAULT_ADDRESS_CHECKSUM}" in captured
    assert calls["private_key"] == ASSET_MANAGER_PRIVATE_KEY
    assert calls["vault_address"] == VAULT_ADDRESS_CHECKSUM
    assert "claimed" not in calls


def test_main_smoke_reports_receipts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    """Nonzero receipts are printed as market, side, raw amount and USD rows."""

    module = load_claim_module()
    config_file = write_non_lagoon_config(tmp_path / "config.json")
    receipts = {
        "ETH": {"long_raw": 1_500_000, "long": 3.75, "short_raw": 0, "short": 0.0},
        "BTC": {"long_raw": 0, "long": 0.0, "short_raw": 2_000_000, "short": 5.5},
    }
    patch_runtime(monkeypatch, module, rows=receipts)
    monkeypatch.setattr(sys, "argv", ["gmx_claim_funding_fees.py", "--config", str(config_file), "--dry-run"])

    exit_code = module.main()
    captured = capsys.readouterr().out

    assert exit_code == 0
    assert "Claimable funding fees for" in captured
    assert "LONG" in captured and "SHORT" in captured
    assert "1,500,000" in captured and "2,000,000" in captured
    assert "$3.75" in captured and "$5.50" in captured


def test_main_smoke_submits_claim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    """Without ``--dry-run`` the script submits the claim and reports the receipt."""

    module = load_claim_module()
    config_file = write_lagoon_config(tmp_path / "config.json")
    calls = patch_runtime(monkeypatch, module)
    monkeypatch.setattr(sys, "argv", ["gmx_claim_funding_fees.py", "--config", str(config_file)])

    exit_code = module.main()
    captured = capsys.readouterr().out

    assert exit_code == 0
    assert calls["claimed"] is True
    assert "mined in block 999 with status 1" in captured


def test_main_smoke_without_receipts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    """An account with nothing claimable exits cleanly without a transaction."""

    module = load_claim_module()
    config_file = write_non_lagoon_config(tmp_path / "config.json")
    calls = patch_runtime(monkeypatch, module, rows={})
    monkeypatch.setattr(sys, "argv", ["gmx_claim_funding_fees.py", "--config", str(config_file)])

    exit_code = module.main()
    captured = capsys.readouterr().out

    assert exit_code == 0
    assert "No claimable funding fees" in captured
    assert "claimed" not in calls
