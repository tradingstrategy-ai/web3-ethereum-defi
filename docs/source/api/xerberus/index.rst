Xerberus risk intelligence API
------------------------------

`Xerberus <https://xerberus.io/>`__ vault risk classification for composite
scores on DeFi vault pools and protocols.

Unlike Core3 (protocol-level Probability of Loss), Xerberus primarily rates
individual vaults by ``(chain_id, address)``. Protocol scores are used as a
fallback and exported under the top-level ``xerberus_protocols`` JSON key.

See ``eth_defi/xerberus/README-xerberus.md`` for operator documentation.

Authentication
~~~~~~~~~~~~~~

Live API calls require **both**:

- ``XERBERUS_API_KEY`` (or ``create_xerberus_session(api_key=...)``)
- ``XERBERUS_API_EMAIL`` (or ``create_xerberus_session(api_email=...)``),
  the email registered with the key, sent as the ``x-user-email`` header

Prefer passing credentials explicitly in code. Agents must **not** invent or
probe candidate emails; only use operator-supplied environment variables or
explicit arguments. Alias env var: ``XERBERUS_USER_EMAIL``.

Features:

- Dual-auth REST session (key + registered email) with paced rate limiting
- DuckDB storage of registry snapshots, vault lists and derived daily scores
- Per-vault export via ``calculate_lifetime_metrics``
- Top-level ``xerberus_protocols`` metadata parallel to ``core3_protocols``

.. autosummary::
   :toctree: _autosummary_xerberus
   :recursive:

   eth_defi.xerberus.session
   eth_defi.xerberus.api
   eth_defi.xerberus.database
   eth_defi.xerberus.scanner
   eth_defi.xerberus.constants
   eth_defi.xerberus.mappings
   eth_defi.xerberus.vault_export
