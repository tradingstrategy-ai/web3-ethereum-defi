"""Fetch helpers for Xerberus public REST API endpoints.

Thin wrappers around individual endpoints. Each function takes a
:py:class:`~eth_defi.xerberus.session.XerberusSession` and returns parsed data
or raises :py:class:`~eth_defi.xerberus.errors.XerberusAPIError`.

Authenticated calls use headers set on the session (``x-api-key`` and
``x-user-email``). Build the session with an explicit ``api_email`` or with
operator-supplied ``XERBERUS_API_EMAIL`` — do not invent email values.

See `Xerberus API documentation <https://xerberus.gitbook.io/documentation/>`__
and ``README-xerberus.md`` for authentication details.
"""

import logging
from collections.abc import Sequence

from eth_defi.xerberus.constants import XERBERUS_DEFAULT_TIMEOUT
from eth_defi.xerberus.errors import XerberusAPIError
from eth_defi.xerberus.session import XerberusSession

logger = logging.getLogger(__name__)


def _unwrap_success_data(payload: dict | list, *, endpoint: str) -> list | dict:
    """Unwrap a Xerberus success envelope or raise :class:`XerberusAPIError`.

    Success shape: ``{"status": "success", "data": ...}``.

    Error shapes from the public docs:

    - Auth middleware: ``{"error": "Missing Email or API key"}``
    - Validation: ``{"status": "error", "message": "..."}``
    - Application: ``{"status": "error", "error": {"message": "...", "code": "..."}}``

    :param payload:
        Parsed JSON body.
    :param endpoint:
        Human-readable endpoint name for error messages.
    :return:
        The ``data`` field on success.
    """
    if not isinstance(payload, dict):
        raise XerberusAPIError(f"Xerberus {endpoint}: expected JSON object envelope, got {type(payload).__name__}")

    if "error" in payload and payload.get("status") is None:
        # Auth middleware style: {"error": "..."} without status
        raise XerberusAPIError(f"Xerberus {endpoint}: {payload.get('error')}")

    status = payload.get("status")
    if status == "error":
        message = payload.get("message")
        if message is None:
            err = payload.get("error")
            if isinstance(err, dict):
                message = err.get("message") or err.get("code") or str(err)
            else:
                message = str(err) if err is not None else "unknown error"
        raise XerberusAPIError(f"Xerberus {endpoint}: {message}")

    if status != "success":
        raise XerberusAPIError(f"Xerberus {endpoint}: unexpected status {status!r}")

    if "data" not in payload:
        raise XerberusAPIError(f"Xerberus {endpoint}: success envelope missing data")

    return payload["data"]


def fetch_registry_scores(
    session: XerberusSession,
    types: Sequence[str] | None = None,
    timeout: float = XERBERUS_DEFAULT_TIMEOUT,
) -> list[dict]:
    """Fetch composite scores from ``GET /registry/scores``.

    :param session:
        Xerberus API session.
    :param types:
        Optional entity types to include (e.g. ``("pool", "protocol")``).
        Comma-joined into the ``type`` query parameter. Omit for all types.
    :param timeout:
        HTTP request timeout in seconds.
    :return:
        List of entity dicts with type, id, name, chain, address, chainId, score.
    """
    url = f"{session.api_url}/registry/scores"
    params: dict[str, str] = {}
    if types is not None:
        params["type"] = ",".join(types)
    resp = session.get(url, params=params or None, timeout=timeout)
    resp.raise_for_status()
    data = _unwrap_success_data(resp.json(), endpoint="registry/scores")
    if not isinstance(data, list):
        raise XerberusAPIError(f"Xerberus registry/scores: expected list data, got {type(data).__name__}")
    return data


def fetch_vault_list(
    session: XerberusSession,
    platform: str,
    timeout: float = XERBERUS_DEFAULT_TIMEOUT,
) -> list[dict]:
    """Fetch active vaults for one platform from ``GET /vault/list``.

    :param session:
        Xerberus API session.
    :param platform:
        Platform slug: ``ipor``, ``morpho``, ``spark``, or ``t3tris``.
    :param timeout:
        HTTP request timeout in seconds.
    :return:
        List of vault dicts with id, name, platform, chain, address, chainId, score.
    """
    url = f"{session.api_url}/vault/list"
    resp = session.get(url, params={"platform": platform}, timeout=timeout)
    resp.raise_for_status()
    data = _unwrap_success_data(resp.json(), endpoint=f"vault/list?platform={platform}")
    if not isinstance(data, list):
        raise XerberusAPIError(f"Xerberus vault/list: expected list data, got {type(data).__name__}")
    return data


def fetch_vault_report_url(
    session: XerberusSession,
    address: str,
    timeout: float = XERBERUS_DEFAULT_TIMEOUT,
) -> str | None:
    """Fetch dendrogram report URL for a vault address.

    :param session:
        Xerberus API session.
    :param address:
        EVM vault address.
    :param timeout:
        HTTP request timeout in seconds.
    :return:
        Report URL string, or ``None`` if the address is not mapped (HTTP 404).
    """
    url = f"{session.api_url}/vault/reports/download/{address}"
    resp = session.get(url, timeout=timeout)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    data = _unwrap_success_data(resp.json(), endpoint="vault/reports/download")
    if not isinstance(data, str):
        raise XerberusAPIError(f"Xerberus vault/reports/download: expected string data, got {type(data).__name__}")
    return data


def fetch_healthcheck(
    session: XerberusSession,
    group: str = "registry",
    timeout: float = XERBERUS_DEFAULT_TIMEOUT,
) -> str:
    """Fetch plain-text liveness for a route group.

    Healthchecks are unauthenticated and return plain text (not a JSON
    envelope). They should not be rate-limited aggressively.

    :param session:
        Xerberus API session (headers are still sent but not required).
    :param group:
        Route group, e.g. ``registry`` or ``vault``.
    :param timeout:
        HTTP request timeout in seconds.
    :return:
        Response body text.
    """
    url = f"{session.api_url}/{group}/healthcheck"
    resp = session.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.text
