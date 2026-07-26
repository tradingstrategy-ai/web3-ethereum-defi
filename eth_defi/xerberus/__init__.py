"""Xerberus vault risk classification integration.

This module provides tools for fetching and storing composite risk scores from
the `Xerberus <https://xerberus.io>`__ public REST API. Unlike Core3 protocol-level
Probability of Loss (PoL), Xerberus rates individual DeFi vault pools by
``(chain_id, address)`` as well as protocols.

Live API authentication requires **both** an API key and the registered email
(``XERBERUS_API_KEY`` + ``XERBERUS_API_EMAIL``, or explicit ``api_key`` /
``api_email`` arguments to :py:func:`~eth_defi.xerberus.session.create_xerberus_session`).
Agents must not invent the email; only use operator-supplied environment or
explicit parameters. See ``README-xerberus.md`` (Authentication section).

See :py:mod:`eth_defi.xerberus.session` for HTTP session management,
:py:mod:`eth_defi.xerberus.database` for DuckDB storage,
:py:mod:`eth_defi.xerberus.scanner` for the scan orchestrator, and
:py:mod:`eth_defi.xerberus.vault_export` for JSON export helpers.

Canonical operator documentation lives in ``README-xerberus.md``.
"""
