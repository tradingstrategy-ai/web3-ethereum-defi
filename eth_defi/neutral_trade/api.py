"""Bounded, read-only Neutral Trade API client.

Responses preserve integer amount strings and provenance without assuming PPS
or share-supply scales. See https://api.neutral.trade/openapi.json for contracts.
"""

import datetime
import json
import logging
import math
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Self
from urllib.parse import quote

import requests

from eth_defi.solana import is_solana_address

# ruff: noqa: EM101, PLR2004

logger = logging.getLogger(__name__)

API_URL = "https://api.neutral.trade"
WSOL_MINT = "So11111111111111111111111111111111111111112"
_ADDRESS = re.compile(r"(?:0x[0-9a-fA-F]{40}|[1-9A-HJ-NP-Za-km-z]{32,44})\Z")


class NeutralTradeAPIError(RuntimeError):
    """A bounded read failed or returned an invalid response."""


@dataclass(slots=True, frozen=True)
class NeutralTradeVault:
    """Directory identity and asset information, without accounting assumptions.

    Raw configuration is retained for inspection against the documented API.
    No numeric share-price conversion is performed before real verification.
    """

    #: Exact case-sensitive API bundle key.
    address: str
    #: Source chain, such as ``solana`` or ``evm:4663``.
    chain: str
    #: Source display name.
    name: str | None
    #: Denomination symbol, which may be unknown.
    symbol: str | None
    #: Exact denomination mint/address.
    mint: str
    #: Asset decimals; these do not establish share decimals.
    decimals: int
    #: Whether the vault is approved and public in the directory.
    public: bool
    #: Why collection is excluded, or ``None``.
    skip_reason: str | None
    #: Unmodified directory configuration.
    raw: dict[str, Any]


def parse_vault(raw: object) -> NeutralTradeVault:
    """Validate a directory identity and apply the WSOL exclusion.

    Missing asset identity is an error rather than permission to collect an
    unknown denomination. Symbols alone cannot override the canonical WSOL mint.

    :param raw: One decoded directory record.
    :return: Typed vault with original identifiers and values.
    :raises NeutralTradeAPIError: If required identity fields are invalid.
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("asset"), dict):
        raise NeutralTradeAPIError("Invalid vault/asset object")
    asset = raw["asset"]
    address, chain, mint = raw.get("bundleKey"), raw.get("chain"), asset.get("mint")
    decimals, symbol = asset.get("decimals"), asset.get("symbol")
    if not isinstance(address, str) or not _ADDRESS.fullmatch(address):
        raise NeutralTradeAPIError("Invalid vault address")
    if not isinstance(chain, str) or not re.fullmatch(r"solana|evm:[1-9][0-9]*", chain):
        raise NeutralTradeAPIError("Invalid source chain")
    if not isinstance(mint, str) or not _ADDRESS.fullmatch(mint):
        raise NeutralTradeAPIError("Invalid denomination identity")
    if (chain == "solana") == address.startswith("0x") or (chain == "solana") == mint.startswith("0x"):
        raise NeutralTradeAPIError("Vault/asset address does not match chain family")
    if chain == "solana" and not all(is_solana_address(value) for value in (address, mint)):
        raise NeutralTradeAPIError("Invalid 32-byte Solana vault/asset address")
    if isinstance(decimals, bool) or not isinstance(decimals, int) or not 0 <= decimals <= 18:
        raise NeutralTradeAPIError("Invalid asset decimals")
    if symbol is not None and not isinstance(symbol, str):
        raise NeutralTradeAPIError("Invalid asset symbol")
    skip = "unsupported_denomination" if mint == WSOL_MINT or (symbol or "").upper() == "WSOL" else None
    if not symbol:
        skip = skip or "unresolved_denomination"
    public = raw.get("visible") is True and raw.get("private") is False
    return NeutralTradeVault(address, chain, raw.get("name"), symbol, mint, decimals, public, skip, raw)


def _read_json(response: requests.Response, deadline: float) -> dict:
    """Decode a bounded response without exposing its body on failure.

    Streaming bounds memory and checks the total operation deadline between
    chunks. The socket read timeout also bounds a stalled chunk.

    :param response: Successful HTTP response owned by the caller.
    :param deadline: Absolute monotonic operation deadline.
    :return: Validated JSON envelope with an object or null data field.
    """
    chunks = []
    size = 0
    for chunk in response.iter_content(chunk_size=65536):
        size += len(chunk)
        if size > 4 * 1024 * 1024 or time.monotonic() > deadline:
            raise NeutralTradeAPIError("Response exceeded size/time budget")
        chunks.append(chunk)
    try:
        payload = json.loads(b"".join(chunks))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise NeutralTradeAPIError("Invalid JSON response") from None
    if not isinstance(payload, dict) or "data" not in payload or (payload["data"] is not None and not isinstance(payload["data"], dict)):
        raise NeutralTradeAPIError("Invalid API envelope")
    return payload


class NeutralTradeClient:
    """Own an HTTP session for bounded mainnet partner reads.

    Authentication is sent only in a header to the fixed production origin.
    HTTP errors omit response bodies and request exception text to avoid
    disclosing credentials or private provider diagnostics.

    :param api_key: Partner credential, optional for public yields only.
    """

    def __init__(self, api_key: str | None = None) -> None:
        """Create a session without making requests.

        The session is explicitly closed by the context manager.

        :param api_key: Secret partner key, never logged.
        """
        self._session = requests.Session()
        if api_key:
            self._session.headers["x-api-key"] = api_key
        self._authenticated = bool(api_key)

    def __enter__(self) -> Self:
        """Return the client for context-managed inspection.

        The same session is reused for the bounded request sequence.

        :return: This client.
        """
        return self

    def __exit__(self, *_: object) -> None:
        """Close the session after successful or failed inspection.

        Exceptions propagate to the caller without suppressing failures.

        :return: None.
        """
        self._session.close()

    def _fetch(self, path: str, params: dict[str, str | int] | None = None) -> dict:
        """Read one JSON object within a bounded request budget.

        Retry transient failures at most three times, honouring numeric
        Retry-After within the 60-second operation budget. Redirects are refused.

        :param path: Internal relative endpoint.
        :param params: Query parameters excluding credentials.
        :return: Validated response envelope.
        """
        if path.startswith("/v2/") and not self._authenticated:
            raise NeutralTradeAPIError("NEUTRAL_TRADE_API_KEY is required for partner reads")
        deadline = time.monotonic() + 60
        for attempt in range(3):
            delay = 1.0
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                with self._session.get(API_URL + path, params=params, timeout=(min(10, remaining), min(20, remaining)), allow_redirects=False, stream=True) as response:
                    status = response.status_code
                    if status == 429 or 500 <= status <= 599:
                        retry_after = response.headers.get("Retry-After", "1")
                        try:
                            delay = max(1.0, float(retry_after))
                        except ValueError:
                            delay = 1.0
                        if not math.isfinite(delay):
                            delay = float("inf")
                    elif status != 200:
                        raise NeutralTradeAPIError(f"Neutral Trade HTTP {status}")
                    else:
                        payload = _read_json(response, deadline)
                        if payload["data"] is None and not path.endswith("/metrics"):
                            raise NeutralTradeAPIError("Required API data is unavailable")
                        return payload
            except requests.RequestException:
                # Retry transport failures without exposing provider diagnostics.
                pass
            if attempt == 2 or delay >= deadline - time.monotonic():
                break
            logger.warning("Neutral Trade %s transient read failure; retry %s/3", path, attempt + 2)
            time.sleep(delay)
        logger.error("Neutral Trade %s failed after bounded retries", path)
        raise NeutralTradeAPIError("Neutral Trade read failed after bounded retries")

    def fetch_public_yields(self) -> dict:
        """Read the unauthenticated aggregator feed.

        This feed does not replace the authenticated directory or history.

        :return: Original response including provenance.
        """
        return self._fetch("/public/yields")

    def fetch_status(self) -> dict:
        """Read projection health without any blockchain RPC.

        Inspect freshness before interpreting price observations.

        :return: Original health response.
        """
        return self._fetch("/v2/status")

    def fetch_vaults(self) -> Iterator[NeutralTradeVault]:
        """Yield directory leads including excluded and unavailable decisions.

        Unavailable IDs are logged by count; omitted records are never inferred
        retired. The inspector only requests details for public eligible leads.

        :return: Iterator over validated available directory entries.
        """
        data = self._fetch("/v2/vaults")["data"]
        if not isinstance(data.get("vaults"), list) or not isinstance(data.get("unavailableVaults"), list):
            raise NeutralTradeAPIError("Invalid directory response")
        logger.info("Directory unavailable vaults: %s", len(data["unavailableVaults"]))
        rejected = 0
        for row in data["vaults"]:
            try:
                vault = parse_vault(row)
            except NeutralTradeAPIError:
                rejected += 1
                continue
            yield vault
        if rejected:
            logger.warning("Directory records rejected: %s; catalogue inspection is incomplete", rejected)

    def fetch_vault(self, vault: NeutralTradeVault) -> dict:
        """Read one eligible public vault's configuration.

        Enforce exclusions even when the caller explicitly selects the vault.

        :param vault: Validated directory entry.
        :return: Detail envelope with original scales.
        """
        self._check_eligible(vault)
        return self._fetch(f"/v2/vault/{quote(vault.address, safe='')}")

    def fetch_metrics(self, vault: NeutralTradeVault) -> dict:
        """Read provider performance metrics for a public eligible vault.

        Values remain provider-reported and must not be taken as independently
        verified net returns or used to infer accounting scales.

        :param vault: Validated directory entry.
        :return: Original metrics envelope, including nullable data.
        """
        self._check_eligible(vault)
        return self._fetch(f"/v2/vault/{quote(vault.address, safe='')}/metrics")

    @staticmethod
    def _check_eligible(vault: NeutralTradeVault) -> None:
        """Reject excluded or unapproved vaults before network reads.

        This protects manual selection from bypassing collection scope.

        :param vault: Validated directory entry.
        :return: None when eligible.
        """
        if vault.skip_reason or not vault.public:
            raise NeutralTradeAPIError("Vault is excluded or lacks public catalogue approval")

    def fetch_history_pages(self, vault: NeutralTradeVault, endpoint: str, *, max_pages: int = 2, page_size: int = 2, start_date: datetime.date | None = None, end_date: datetime.date | None = None, cursor: str | None = None) -> Iterator[dict]:
        """Yield bounded history pages without changing raw amount strings.

        Detect repeated cursors and mismatched identities. The final cursor
        indicates whether the sample is complete; a page cap is not completion.

        :param vault: Eligible public directory entry.
        :param endpoint: ``share-price`` or ``snapshots``.
        :param max_pages: Positive inspection page cap.
        :param page_size: Provider page size, between one and 100.
        :param start_date: Inclusive UTC date, if bounded.
        :param end_date: Inclusive UTC date, if bounded.
        :param cursor: Persisted opaque continuation for the same date range.
        :return: Iterator of original page envelopes.
        """
        self._check_eligible(vault)
        if endpoint not in {"share-price", "snapshots"} or max_pages < 1 or not 1 <= page_size <= 100:
            raise ValueError("Invalid history endpoint or page bounds")
        if start_date and end_date and start_date > end_date:
            raise ValueError("START_DATE must not exceed END_DATE")
        params: dict[str, str | int] = {"limit": page_size}
        if cursor:
            params["cursor"] = cursor
        if start_date:
            params["startDate"] = start_date.isoformat()
        if end_date:
            params["endDate"] = end_date.isoformat()
        seen = {cursor} if cursor else set()
        for _ in range(max_pages):
            page = self._fetch(f"/v2/vault/{quote(vault.address, safe='')}/{endpoint}", params)
            data = page["data"]
            key = "series" if endpoint == "share-price" else "snapshots"
            response_id = data.get("vaultId")
            same_identity = response_id == vault.address if vault.chain == "solana" else isinstance(response_id, str) and response_id.lower() == vault.address.lower()
            if not same_identity or not isinstance(data.get(key), list) or not all(isinstance(row, dict) for row in data[key]) or "nextCursor" not in data:
                raise NeutralTradeAPIError("Invalid history identity or page")
            cursor = data["nextCursor"]
            if cursor is not None and (not isinstance(cursor, str) or not cursor or cursor in seen):
                raise NeutralTradeAPIError("Invalid or repeating history cursor")
            yield page
            if cursor is None:
                return
            seen.add(cursor)
            params["cursor"] = cursor
