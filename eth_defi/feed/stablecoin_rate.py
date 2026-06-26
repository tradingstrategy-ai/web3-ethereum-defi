"""Stablecoin rate refresh and depeg lookups.

This module maintains mutable rate metadata in
``eth_defi/data/stablecoins/*.yaml``. It fetches stablecoin prices from the
CoinGecko simple price API, stores the latest USD rate and fetch timestamp, and
marks a sticky ``depegged_at`` timestamp when a token trades below the
configured threshold in its inferred peg currency.

Vault metric calculation should use :class:`StablecoinRateFeeder` instead of
reading YAML files directly. The feeder owns the in-process lookup caches used
to resolve a vault denomination token to stablecoin rate metadata.

See `CoinGecko simple price documentation <https://docs.coingecko.com/reference/simple-price>`__.
"""

import datetime
import json
import logging
import math
import os
import re
import stat
import tempfile
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Sequence, TypeVar

from eth_typing import HexAddress
from strictyaml import YAMLError
from tqdm_loggable.auto import tqdm

from eth_defi.compat import native_datetime_utc_fromtimestamp, native_datetime_utc_now
from eth_defi.stablecoin_metadata import STABLECOINS_DATA_DIR, normalise_token_symbol, read_stablecoin_metadata

try:
    import requests  # type: ignore[import-not-found]
except ModuleNotFoundError:

    class _UrllibResponse:
        """Small ``requests.Response`` compatible wrapper for keyless GETs."""

        def __init__(self, status_code: int, body: bytes):
            self.status_code = status_code
            self._body = body
            self.text = body.decode("utf-8", errors="replace")

        def raise_for_status(self) -> None:
            """Raise an HTTP-style error for non-2xx responses."""
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}: {self.text[:200]}")

        def json(self) -> Any:
            """Decode the response body as JSON."""
            return json.loads(self.text)

    class _RequestsFallback:
        """Tiny subset of :mod:`requests` used by this module.

        The repository currently receives ``requests`` transitively in some
        extras, but the base Poetry environment may not install it. Keeping this
        shim avoids import-time failures while preserving a monkeypatchable
        ``requests.get`` surface for tests.
        """

        @staticmethod
        def get(url: str, params: dict[str, str] | None = None, headers: dict[str, str] | None = None, timeout: float = 20.0) -> _UrllibResponse:
            """Perform a blocking HTTP GET with urllib."""
            encoded_params = urllib.parse.urlencode(params or {})
            request_url = f"{url}?{encoded_params}" if encoded_params else url
            request = urllib.request.Request(request_url, headers=headers or {})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return _UrllibResponse(response.status, response.read())

    requests = _RequestsFallback()  # type: ignore[assignment]


logger = logging.getLogger(__name__)

T = TypeVar("T")

COINGECKO_SIMPLE_PRICE_URL = "https://api.coingecko.com/api/v3/simple/price"
COINGECKO_LINK_PREFIX = "https://www.coingecko.com/en/coins/"
DEPEG_THRESHOLD = 0.90
MIN_PLAUSIBLE_STABLECOIN_RATE = 0.01
STABLECOIN_RATE_SOURCE_COINGECKO = "coingecko"

_RATE_FIELDS = (
    "coingecko_id",
    "coingecko_link",
    "coingecko_id_source",
    "coingecko_id_verified_at",
    "usd_rate",
    "usd_rate_fetched_at",
    "usd_rate_updated_at",
    "peg_rate",
    "peg_rate_currency",
    "rate_fetch_failed_at",
    "rate_fetch_failed_reason",
    "depegged_at",
)

_CHAIN_ALIASES_TO_ID: dict[str, int] = {
    "ethereum": 1,
    "eth": 1,
    "mainnet": 1,
    "base": 8453,
    "arbitrum": 42161,
    "arbitrum_one": 42161,
    "arbitrum-one": 42161,
    "polygon": 137,
    "matic": 137,
    "optimism": 10,
    "op": 10,
    "binance": 56,
    "bnb": 56,
    "bsc": 56,
    "avalanche": 43114,
    "avax": 43114,
    "fantom": 250,
    "gnosis": 100,
    "xdai": 100,
    "mantle": 5000,
    "linea": 59144,
    "scroll": 534352,
    "celo": 42220,
    "kava": 2222,
    "berachain": 80094,
    "hyperevm": 999,
    "hyperliquid": 999,
}


@dataclass(slots=True)
class StablecoinRateTarget:
    """One stablecoin YAML entry that may be refreshed from CoinGecko."""

    yaml_path: Path
    entry_index: int | None
    slug: str
    symbol: str
    category: str
    name: str
    coingecko_id: str | None
    coingecko_link: str | None
    coingecko_id_source: str | None
    coingecko_id_verified_at: datetime.datetime | None
    peg_currency: str | None
    usd_rate: float | None
    usd_rate_fetched_at: datetime.datetime | None
    usd_rate_updated_at: datetime.datetime | None
    peg_rate: float | None
    peg_rate_currency: str | None
    rate_fetch_failed_at: datetime.datetime | None
    rate_fetch_failed_reason: str | None
    depegged_at: datetime.datetime | None
    contract_addresses: list[tuple[int, str]]


@dataclass(slots=True)
class StablecoinRateRefreshSummary:
    """Counters for one stablecoin rate refresh run."""

    files_scanned: int = 0
    entries_seen: int = 0
    due_count: int = 0
    skipped_attempted_today_count: int = 0
    skipped_failed_today_count: int = 0
    skipped_succeeded_today_count: int = 0
    rates_fetched: int = 0
    files_updated: int = 0
    depegged_count: int = 0
    unactionable_depegged_count: int = 0
    skipped_missing_coingecko: int = 0
    skipped_unknown_peg: int = 0
    failed_count: int = 0


@dataclass(slots=True)
class DenominationTokenRate:
    """Rate data section exported for a vault denomination token."""

    coingecko_id: str | None
    usd_rate: float | None
    usd_rate_fetched_at: datetime.datetime | None
    usd_rate_source: str | None


@dataclass(slots=True)
class StablecoinRateFeeder:
    """Cached stablecoin rate/depeg lookup helper for vault metrics."""

    data_dir: Path = STABLECOINS_DATA_DIR
    _depegged_contracts: set[tuple[int, str]] | None = field(default=None, init=False, repr=False)
    _depegged_symbols: set[str] | None = field(default=None, init=False, repr=False)
    _rate_contracts: dict[tuple[int, str], DenominationTokenRate] | None = field(default=None, init=False, repr=False)
    _rate_symbols: dict[str, DenominationTokenRate] | None = field(default=None, init=False, repr=False)

    def get_denomination_token_rate_section(
        self,
        chain_id: int | None,
        address: HexAddress | str | None,
        symbol: str | None,
    ) -> DenominationTokenRate:
        """Resolve a vault denomination token to exported rate metadata."""
        if self._rate_contracts is None or self._rate_symbols is None:
            self._rate_contracts, self._rate_symbols = build_stablecoin_rate_lookups(self.data_dir)

        key = _normalise_contract_key(chain_id, address)
        if key and key in self._rate_contracts:
            return self._rate_contracts[key]

        normalised_symbol = normalise_token_symbol(symbol)
        if normalised_symbol and normalised_symbol in self._rate_symbols:
            return self._rate_symbols[normalised_symbol]

        return DenominationTokenRate(coingecko_id=None, usd_rate=None, usd_rate_fetched_at=None, usd_rate_source=None)

    def is_depegged_stablecoin_token(
        self,
        chain_id: int | None,
        address: HexAddress | str | None,
        symbol: str | None,
    ) -> bool:
        """Return ``True`` when a denomination token is marked depegged."""
        if self._depegged_contracts is None or self._depegged_symbols is None:
            self._depegged_contracts, self._depegged_symbols = build_depegged_stablecoin_lookups(self.data_dir)

        key = _normalise_contract_key(chain_id, address)
        if key and key in self._depegged_contracts:
            return True

        normalised_symbol = normalise_token_symbol(symbol)
        return bool(normalised_symbol and normalised_symbol in self._depegged_symbols)


def extract_coingecko_id(url: str | None) -> str | None:
    """Extract a CoinGecko coin id from a human-readable CoinGecko URL."""
    if not url:
        return None

    parsed = urllib.parse.urlparse(url.strip())
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 3 and parts[0] == "en" and parts[1] == "coins":
        return parts[2]
    return None


def resolve_coingecko_metadata(entry: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    """Resolve the CoinGecko id, page link, and id source for one YAML entry."""
    explicit_id = _clean_string(entry.get("coingecko_id"))
    explicit_link = _clean_string(entry.get("coingecko_link"))
    source = _clean_string(entry.get("coingecko_id_source"))

    if explicit_id:
        return explicit_id, explicit_link or f"{COINGECKO_LINK_PREFIX}{explicit_id}", source or "manual"

    for link in (explicit_link, _clean_string((entry.get("links") or {}).get("coingecko"))):
        parsed_id = extract_coingecko_id(link)
        if parsed_id:
            return parsed_id, f"{COINGECKO_LINK_PREFIX}{parsed_id}", "url"

    return None, explicit_link, source


def iter_stablecoin_rate_targets(data_dir: Path = STABLECOINS_DATA_DIR) -> Iterator[StablecoinRateTarget]:
    """Iterate stablecoin YAML entries that can participate in rate refreshes."""
    for yaml_path in sorted(data_dir.glob("*.yaml")):
        try:
            data = read_stablecoin_metadata(yaml_path)
        except YAMLError as e:
            raise ValueError(f"Could not read stablecoin metadata {yaml_path}: {e}") from e
        symbol = data["symbol"]
        slug = data.get("slug") or yaml_path.stem

        if "entries" in data:
            for entry_index, entry in enumerate(data["entries"]):
                yield _build_target(yaml_path, entry_index, slug, symbol, data.get("category", ""), entry)
        else:
            yield _build_target(yaml_path, None, slug, symbol, data.get("category", ""), data)


def fetch_stablecoin_rates(targets: Sequence[StablecoinRateTarget], timeout: float = 20.0, progress_bar: bool = False) -> dict[str, dict[str, Any]]:
    """Fetch CoinGecko prices for due stablecoin targets.

    :param targets:
        Due targets with resolved CoinGecko ids.

    :param timeout:
        HTTP request timeout in seconds.

    :param progress_bar:
        Show a tqdm progress bar for CoinGecko request batches.

    :return:
        CoinGecko response keyed by coin id.
    """
    ids = sorted({target.coingecko_id for target in targets if target.coingecko_id})
    if not ids:
        return {}

    vs_currencies = sorted({"usd"} | {target.peg_currency for target in targets if target.peg_currency})
    headers = {"User-Agent": "web3-ethereum-defi stablecoin rate refresh"}
    api_key = os.environ.get("COINGECKO_DEMO_API_KEY")
    if api_key:
        headers["x-cg-demo-api-key"] = api_key

    result: dict[str, dict[str, Any]] = {}
    batch_size = 200
    batch_starts = list(range(0, len(ids), batch_size))
    for start in _progress(batch_starts, progress_bar, "Fetching CoinGecko batches", "batch"):
        batch = ids[start : start + batch_size]
        response = requests.get(
            COINGECKO_SIMPLE_PRICE_URL,
            params={
                "ids": ",".join(batch),
                "vs_currencies": ",".join(vs_currencies),
                "include_last_updated_at": "true",
            },
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("CoinGecko simple price response was not a JSON object")
        result.update(payload)
    return result


def refresh_stablecoin_rates(
    data_dir: Path = STABLECOINS_DATA_DIR,
    now_: datetime.datetime | None = None,
    force: bool = False,
    timeout: float = 20.0,
    progress_bar: bool = False,
) -> StablecoinRateRefreshSummary:
    """Refresh stablecoin rates and persist YAML metadata updates.

    This function is intentionally entry tolerant: missing CoinGecko ids and
    missing prices are written to per-entry failure fields instead of aborting
    the whole run. HTTP-level failures are recorded for all due targets in the
    batch, then returned in the summary. Depeg decisions are made only when the
    token's peg currency can be inferred and CoinGecko returns that currency.
    """
    now_ = now_ or native_datetime_utc_now()
    targets = list(_progress(iter_stablecoin_rate_targets(data_dir), progress_bar, "Reading stablecoin YAML", "entry"))
    summary = StablecoinRateRefreshSummary(
        files_scanned=len({target.yaml_path for target in targets}),
        entries_seen=len(targets),
    )

    due_targets: list[StablecoinRateTarget] = []
    skipped_targets: list[StablecoinRateTarget] = []
    for target in targets:
        if force or not _target_was_attempted_today(target, now_):
            due_targets.append(target)
        else:
            skipped_targets.append(target)
    summary.due_count = len(due_targets)
    summary.skipped_attempted_today_count = len(skipped_targets)
    summary.skipped_failed_today_count = sum(1 for target in skipped_targets if _target_failed_today(target, now_))
    summary.skipped_succeeded_today_count = sum(1 for target in skipped_targets if _target_succeeded_today(target, now_))
    due_with_ids = [target for target in due_targets if target.coingecko_id]
    updates: dict[tuple[Path, int | None], dict[str, Any]] = {}

    for target in _progress(due_targets, progress_bar, "Checking stablecoin entries", "entry"):
        if not target.coingecko_id:
            summary.skipped_missing_coingecko += 1
            summary.failed_count += 1
            updates[(target.yaml_path, target.entry_index)] = _failure_update(now_, "missing_coingecko_id", target)

    prices: dict[str, dict[str, Any]] = {}
    if due_with_ids:
        try:
            prices = fetch_stablecoin_rates(due_with_ids, timeout=timeout, progress_bar=progress_bar)
        except _coingecko_fetch_error_types() as e:
            logger.warning("CoinGecko stablecoin rate batch failed: %s", e)
            for target in due_with_ids:
                summary.failed_count += 1
                updates[(target.yaml_path, target.entry_index)] = _failure_update(now_, "coingecko_http_error", target)
            return _apply_refresh_updates(updates, summary, progress_bar=progress_bar)

    fetched_ids: set[str] = set()
    for target in _progress(due_with_ids, progress_bar, "Applying CoinGecko prices", "entry"):
        price_data = prices.get(target.coingecko_id or "")
        if not isinstance(price_data, dict):
            summary.failed_count += 1
            updates[(target.yaml_path, target.entry_index)] = _failure_update(now_, "coingecko_price_missing", target)
            continue

        usd_rate = _parse_price(price_data.get("usd"))
        if usd_rate is None:
            summary.failed_count += 1
            updates[(target.yaml_path, target.entry_index)] = _failure_update(now_, "coingecko_price_missing", target)
            continue

        peg_currency = target.peg_currency
        peg_rate = _parse_price(price_data.get(peg_currency)) if peg_currency else None
        if peg_currency is None or peg_rate is None:
            summary.skipped_unknown_peg += 1

        upstream_updated_at = _parse_upstream_timestamp(price_data.get("last_updated_at"))
        update = _success_update(target, usd_rate, peg_rate, peg_currency if peg_rate is not None else None, upstream_updated_at, now_)

        should_check_depeg = peg_rate is not None and target.category == "stablecoin"
        if should_check_depeg and _is_wrong_asset_guard_failure(target, peg_rate):
            summary.failed_count += 1
            updates[(target.yaml_path, target.entry_index)] = _failure_update(now_, "coingecko_price_missing", target)
            continue
        elif should_check_depeg and peg_rate < DEPEG_THRESHOLD:
            update["depegged_at"] = target.depegged_at or now_
            summary.depegged_count += 1
            if not _target_is_actionable(target):
                summary.unactionable_depegged_count += 1
                logger.warning("Depegged stablecoin entry %s/%s is not actionable for vault blacklisting", target.slug, target.name)

        updates[(target.yaml_path, target.entry_index)] = update
        fetched_ids.add(target.coingecko_id or "")

    summary.rates_fetched = len(fetched_ids)
    return _apply_refresh_updates(updates, summary, progress_bar=progress_bar)


def build_depegged_stablecoin_lookups(data_dir: Path = STABLECOINS_DATA_DIR) -> tuple[set[tuple[int, str]], set[str]]:
    """Build contract and unambiguous symbol lookups for depegged stablecoins."""
    depegged_contracts: set[tuple[int, str]] = set()
    symbol_owner_counts: dict[str, int] = {}
    depegged_symbol_candidates: set[str] = set()

    for target in iter_stablecoin_rate_targets(data_dir):
        normalised_symbol = normalise_token_symbol(target.symbol)
        if normalised_symbol and target.entry_index is None:
            symbol_owner_counts[normalised_symbol] = symbol_owner_counts.get(normalised_symbol, 0) + 1

        if target.depegged_at is None:
            continue
        depegged_contracts.update(target.contract_addresses)
        if normalised_symbol and target.entry_index is None:
            depegged_symbol_candidates.add(normalised_symbol)

    depegged_symbols = {symbol for symbol in depegged_symbol_candidates if symbol_owner_counts.get(symbol) == 1}
    return depegged_contracts, depegged_symbols


def build_stablecoin_rate_lookups(data_dir: Path = STABLECOINS_DATA_DIR) -> tuple[dict[tuple[int, str], DenominationTokenRate], dict[str, DenominationTokenRate]]:
    """Build contract and unambiguous symbol lookups for all known rate data."""
    contract_rates: dict[tuple[int, str], DenominationTokenRate] = {}
    symbol_candidates: dict[str, list[tuple[StablecoinRateTarget, DenominationTokenRate]]] = {}

    for target in iter_stablecoin_rate_targets(data_dir):
        rate = _target_to_denomination_rate(target)
        for contract_key in target.contract_addresses:
            contract_rates[contract_key] = rate

        normalised_symbol = normalise_token_symbol(target.symbol)
        if normalised_symbol and target.entry_index is None:
            symbol_candidates.setdefault(normalised_symbol, []).append((target, rate))

    symbol_rates = {symbol: matches[0][1] for symbol, matches in symbol_candidates.items() if len(matches) == 1}
    return contract_rates, symbol_rates


def apply_coingecko_mapping_file(data_dir: Path, mapping_path: Path, progress_bar: bool = False) -> int:
    """Apply explicit CoinGecko id mappings to stablecoin YAML files.

    The mapping JSON shape is intentionally simple:

    .. code-block:: json

        {
          "usdx": {
            "Kava USDX": {
              "coingecko_id": "usdx",
              "coingecko_link": "https://www.coingecko.com/en/coins/usdx"
            }
          }
        }

    Top-level keys are stablecoin slugs. For multi-entry files, the nested key
    can be the entry name or a numeric entry index encoded as a string. Standard
    files may use ``"default"``.
    """
    if not mapping_path.exists():
        raise FileNotFoundError(f"CoinGecko mapping file does not exist: {mapping_path}")

    raw = json.loads(mapping_path.read_text())
    if not isinstance(raw, dict):
        raise ValueError("CoinGecko mapping file must contain a JSON object")

    changed = 0
    for slug, slug_mapping in _progress(list(raw.items()), progress_bar, "Applying CoinGecko mappings", "file"):
        yaml_path = data_dir / f"{slug}.yaml"
        if not yaml_path.exists() or not isinstance(slug_mapping, dict):
            continue
        data = read_stablecoin_metadata(yaml_path)
        if "entries" in data:
            for entry_index, entry in enumerate(data["entries"]):
                mapping = slug_mapping.get(entry.get("name")) or slug_mapping.get(str(entry_index))
                if isinstance(mapping, dict):
                    _update_yaml_entry_fields(yaml_path, entry_index, _mapping_update(mapping))
                    changed += 1
        else:
            mapping = slug_mapping.get("default") or slug_mapping.get(data.get("name")) or slug_mapping
            if isinstance(mapping, dict) and mapping.get("coingecko_id"):
                _update_yaml_entry_fields(yaml_path, None, _mapping_update(mapping))
                changed += 1

    return changed


def _build_target(yaml_path: Path, entry_index: int | None, slug: str, symbol: str, category: str, entry: dict[str, Any]) -> StablecoinRateTarget:
    coingecko_id, coingecko_link, coingecko_id_source = resolve_coingecko_metadata(entry)
    peg_currency = _guess_peg_currency(symbol, f"{entry.get('name', '')} {entry.get('short_description', '')}")
    return StablecoinRateTarget(
        yaml_path=yaml_path,
        entry_index=entry_index,
        slug=slug,
        symbol=symbol,
        category=category,
        name=entry.get("name", ""),
        coingecko_id=coingecko_id,
        coingecko_link=coingecko_link,
        coingecko_id_source=coingecko_id_source,
        coingecko_id_verified_at=_parse_datetime(entry.get("coingecko_id_verified_at")),
        peg_currency=peg_currency,
        usd_rate=_parse_float(entry.get("usd_rate")),
        usd_rate_fetched_at=_parse_datetime(entry.get("usd_rate_fetched_at")),
        usd_rate_updated_at=_parse_datetime(entry.get("usd_rate_updated_at")),
        peg_rate=_parse_float(entry.get("peg_rate")),
        peg_rate_currency=_clean_string(entry.get("peg_rate_currency")),
        rate_fetch_failed_at=_parse_datetime(entry.get("rate_fetch_failed_at")),
        rate_fetch_failed_reason=_clean_string(entry.get("rate_fetch_failed_reason")),
        depegged_at=_parse_datetime(entry.get("depegged_at")),
        contract_addresses=_parse_contract_addresses(entry),
    )


def _parse_contract_addresses(entry: dict[str, Any]) -> list[tuple[int, str]]:
    raw = entry.get("contract_addresses") or []
    result = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        chain_id = _chain_slug_to_id(item.get("chain"))
        address = _clean_string(item.get("address"))
        if chain_id is not None and address:
            result.append((chain_id, address.lower()))
    return result


def _coingecko_fetch_error_types() -> tuple[type[BaseException], ...]:
    """Return exception types expected from CoinGecko HTTP/JSON fetches."""
    request_exception = getattr(getattr(requests, "exceptions", None), "RequestException", None)
    base_types: tuple[type[BaseException], ...] = (OSError, RuntimeError, TimeoutError, ValueError)
    if isinstance(request_exception, type):
        return (request_exception, *base_types)
    return base_types


def _chain_slug_to_id(chain: Any) -> int | None:
    if chain is None:
        return None
    if isinstance(chain, int):
        return chain
    slug = str(chain).strip().lower().replace(" ", "_")
    return _CHAIN_ALIASES_TO_ID.get(slug)


def _normalise_contract_key(chain_id: int | None, address: HexAddress | str | None) -> tuple[int, str] | None:
    if chain_id is None or not address:
        return None
    return int(chain_id), str(address).lower()


def _clean_string(value: Any) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _parse_datetime(value: Any) -> datetime.datetime | None:
    text = _clean_string(value)
    if not text:
        return None
    try:
        return datetime.datetime.fromisoformat(text.replace("Z", ""))
    except ValueError:
        return None


def _format_datetime(value: datetime.datetime | None) -> str:
    if value is None:
        return ""
    return value.replace(microsecond=0).isoformat()


def _parse_float(value: Any) -> float | None:
    text = _clean_string(value)
    if not text:
        return None
    try:
        parsed = float(text)
    except ValueError:
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def _parse_price(value: Any) -> float | None:
    parsed = _parse_float(value)
    if parsed is None or parsed <= 0:
        return None
    return parsed


def _parse_upstream_timestamp(value: Any) -> datetime.datetime | None:
    if value in (None, ""):
        return None
    try:
        return native_datetime_utc_fromtimestamp(float(value))
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _guess_peg_currency(symbol: str, name: str) -> str | None:
    symbol_lower = symbol.lower()
    name_words = set(re.findall(r"[a-z0-9]+", name.lower()))

    def symbol_has(code: str) -> bool:
        return code in symbol_lower

    def name_has(*terms: str) -> bool:
        return any(term in name_words for term in terms)

    if symbol_has("cad") or name_has("cad", "canadian"):
        return "cad"
    if symbol_has("jpy") or name_has("jpy", "yen", "japanese"):
        return "jpy"
    if symbol_has("gbp") or name_has("gbp", "pound", "sterling"):
        return "gbp"
    if symbol_has("aud") or name_has("aud", "australian"):
        return "aud"
    if symbol_has("hkd") or ("hong" in name_words and "kong" in name_words):
        return "hkd"
    if symbol_has("nzd") or ("new" in name_words and "zealand" in name_words):
        return "nzd"
    if symbol_has("eur") or name_has("eur", "euro"):
        return "eur"
    if symbol_has("chf") or name_has("chf", "franc"):
        return "chf"
    if symbol_has("sgd") or name_has("sgd", "singapore"):
        return "sgd"
    if symbol_has("try") or name_has("try", "turkish"):
        return "try"
    if symbol_has("xau") or symbol_lower == "paxg" or ("tether" in name_words and "gold" in name_words):
        return "xau"
    if symbol_has("usd") or name_has("usd", "dollar"):
        return "usd"
    return None


def _target_was_attempted_today(target: StablecoinRateTarget, now_: datetime.datetime) -> bool:
    today = now_.date()
    for value in (target.usd_rate_fetched_at, target.rate_fetch_failed_at):
        if value and value.date() == today:
            return True
    return False


def _target_failed_today(target: StablecoinRateTarget, now_: datetime.datetime) -> bool:
    """Return ``True`` if a target already recorded a failed refresh today."""
    return bool(target.rate_fetch_failed_at and target.rate_fetch_failed_at.date() == now_.date())


def _target_succeeded_today(target: StablecoinRateTarget, now_: datetime.datetime) -> bool:
    """Return ``True`` if a target already recorded a successful refresh today."""
    return bool(target.usd_rate_fetched_at and target.usd_rate_fetched_at.date() == now_.date())


def _success_update(
    target: StablecoinRateTarget,
    usd_rate: float,
    peg_rate: float | None,
    peg_currency: str | None,
    upstream_updated_at: datetime.datetime | None,
    now_: datetime.datetime,
) -> dict[str, Any]:
    update: dict[str, Any] = {
        "coingecko_id": target.coingecko_id or "",
        "coingecko_link": target.coingecko_link or (f"{COINGECKO_LINK_PREFIX}{target.coingecko_id}" if target.coingecko_id else ""),
        "coingecko_id_source": target.coingecko_id_source or "",
        "coingecko_id_verified_at": now_,
        "usd_rate": usd_rate,
        "usd_rate_fetched_at": now_,
        "usd_rate_updated_at": upstream_updated_at,
        "peg_rate": peg_rate,
        "peg_rate_currency": peg_currency or "",
        "rate_fetch_failed_at": "",
        "rate_fetch_failed_reason": "",
        "depegged_at": target.depegged_at,
    }
    return update


def _failure_update(now_: datetime.datetime, reason: str, target: StablecoinRateTarget) -> dict[str, Any]:
    update: dict[str, Any] = {
        "rate_fetch_failed_at": now_,
        "rate_fetch_failed_reason": reason,
    }
    if target.coingecko_id:
        update.update(
            {
                "coingecko_id": target.coingecko_id,
                "coingecko_link": target.coingecko_link or f"{COINGECKO_LINK_PREFIX}{target.coingecko_id}",
                "coingecko_id_source": target.coingecko_id_source or "",
            }
        )
    return update


def _is_wrong_asset_guard_failure(target: StablecoinRateTarget, peg_rate: float) -> bool:
    source = target.coingecko_id_source or ""
    verified = target.coingecko_id_verified_at is not None
    return peg_rate < MIN_PLAUSIBLE_STABLECOIN_RATE and source != "manual" and not verified


def _target_is_actionable(target: StablecoinRateTarget) -> bool:
    if target.contract_addresses:
        return True
    return target.entry_index is None and bool(normalise_token_symbol(target.symbol))


def _target_to_denomination_rate(target: StablecoinRateTarget) -> DenominationTokenRate:
    return DenominationTokenRate(
        coingecko_id=target.coingecko_id,
        usd_rate=target.usd_rate,
        usd_rate_fetched_at=target.usd_rate_fetched_at,
        usd_rate_source=STABLECOIN_RATE_SOURCE_COINGECKO if target.usd_rate is not None else None,
    )


def _progress(items: Sequence[T] | Iterator[T], enabled: bool, desc: str, unit: str) -> Iterator[T]:
    """Wrap an iterable in tqdm when requested."""
    if enabled:
        total = len(items) if isinstance(items, Sequence) else None
        yield from tqdm(items, total=total, desc=desc, unit=unit)
    else:
        yield from items


def _apply_refresh_updates(updates: dict[tuple[Path, int | None], dict[str, Any]], summary: StablecoinRateRefreshSummary, progress_bar: bool = False) -> StablecoinRateRefreshSummary:
    updated_paths: set[Path] = set()
    for (yaml_path, entry_index), fields in _progress(list(updates.items()), progress_bar, "Writing stablecoin YAML", "entry"):
        if fields:
            _update_yaml_entry_fields(yaml_path, entry_index, fields)
            updated_paths.add(yaml_path)
    summary.files_updated = len(updated_paths)
    return summary


def _mapping_update(mapping: dict[str, Any]) -> dict[str, Any]:
    coingecko_id = _clean_string(mapping.get("coingecko_id"))
    return {
        "coingecko_id": coingecko_id or "",
        "coingecko_link": _clean_string(mapping.get("coingecko_link")) or (f"{COINGECKO_LINK_PREFIX}{coingecko_id}" if coingecko_id else ""),
        "coingecko_id_source": _clean_string(mapping.get("coingecko_id_source")) or "manual",
    }


def _format_yaml_value(value: Any) -> str:
    if value is None or value == "":
        return "''"
    if isinstance(value, datetime.datetime):
        return f"'{_format_datetime(value)}'"
    if isinstance(value, float):
        return f"{value:.12g}"
    if isinstance(value, int):
        return str(value)
    text = str(value)
    if not text:
        return "''"
    if re.fullmatch(r"[A-Za-z0-9_.:/?#=&%+-]+", text):
        return text
    return json.dumps(text)


def _update_yaml_entry_fields(yaml_path: Path, entry_index: int | None, fields: dict[str, Any]) -> None:
    lines = yaml_path.read_text().splitlines(keepends=True)
    start, end, indent = _find_yaml_entry_block(lines, entry_index)
    existing = _find_existing_field_lines(lines, start, end, indent)

    rendered = [f"{indent}{key}: {_format_yaml_value(fields.get(key))}\n" for key in _RATE_FIELDS if key in fields]
    for key, line_index in sorted(existing.items(), key=lambda item: item[1], reverse=True):
        if key in fields:
            del lines[line_index]
            end -= 1

    insert_at = _find_insert_position(lines, start, end, indent)
    for offset, line in enumerate(rendered):
        lines.insert(insert_at + offset, line)

    _write_text_atomic(yaml_path, "".join(lines))


def _write_text_atomic(path: Path, text: str) -> None:
    """Write text to a file using a same-directory atomic replacement."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing_mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        existing_mode = None

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as tmp_file:
        tmp_file.write(text)
        tmp_path = Path(tmp_file.name)

    try:
        if existing_mode is not None:
            os.chmod(tmp_path, existing_mode)
        os.replace(tmp_path, path)
    except OSError:
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def _find_yaml_entry_block(lines: list[str], entry_index: int | None) -> tuple[int, int, str]:
    if entry_index is None:
        return 0, len(lines), ""

    entries_line = next((i for i, line in enumerate(lines) if line.startswith("entries:")), None)
    if entries_line is None:
        raise ValueError("Cannot update entry metadata from YAML without entries block")

    item_starts = [i for i in range(entries_line + 1, len(lines)) if re.match(r"^-\s+[A-Za-z_][A-Za-z0-9_]*\s*:", lines[i])]
    if entry_index >= len(item_starts):
        raise IndexError(f"Entry index {entry_index} out of range for {len(item_starts)} entries")

    start = item_starts[entry_index]
    end = item_starts[entry_index + 1] if entry_index + 1 < len(item_starts) else len(lines)
    for i in range(start + 1, len(lines)):
        if i >= end:
            break
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*:\s*", lines[i]):
            end = i
            break
    return start, end, "  "


def _find_existing_field_lines(lines: list[str], start: int, end: int, indent: str) -> dict[str, int]:
    existing = {}
    for i in range(start, end):
        for key in _RATE_FIELDS:
            if lines[i].startswith(f"{indent}{key}:"):
                existing[key] = i
    return existing


def _find_insert_position(lines: list[str], start: int, end: int, indent: str) -> int:
    for i in range(start, end):
        if lines[i].startswith(f"{indent}checks:"):
            return i
    return end
