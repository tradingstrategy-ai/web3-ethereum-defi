Morpho API
----------

`Morpho protocol <https://morpho.org/>`__ integration.

Morpho is a decentralised lending protocol. Its vaults allocate deposited
assets to lending markets or other configured allocations. The library
distinguishes two vault architectures:

**Morpho V1 (MetaMorpho)**: The original Morpho vault architecture that directly
integrates with Morpho markets. Vaults expose the ``MORPHO()`` function for identification.

**Morpho V2**: A newer adapter-based architecture that enables flexible allocation
across multiple yield sources through smart contract adapters. V2 vaults use
``adaptersLength()`` for identification and support:

- Multi-protocol yield allocation via adapters
- Granular ID & Cap system for risk management
- Performance fees (up to 50%) and management fees (up to 5%)
- Timelocked governance with curator/allocator roles
- Non-custodial exits via ``forceDeallocate``

.. _morpho-vault-classification:

Vault features and adapter selection
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This is the canonical guide to the Morpho-related
:py:class:`~eth_defi.erc_4626.core.ERC4626Feature` flags. Features describe
interfaces and select contract readers; they do not establish who operates a
vault, whether an investor may use it, or whether a trade can settle.

:py:func:`~eth_defi.erc_4626.classification.identify_vault_features` combines
the existing multicall probe results. The Morpho-specific rules are:

.. list-table:: Morpho interface detection
   :header-rows: 1
   :widths: 24 38 38

   * - Features
     - Probe result
     - Selected adapter
   * - ``morpho_like`` without ``vault_morpho_like``
     - ``MORPHO()`` succeeds; ``tvlCap()`` fails or is not one 32-byte word.
     - :py:class:`~eth_defi.erc_4626.vault_protocol.morpho.vault_v1.MorphoV1Vault`.
   * - ``morpho_like`` and ``vault_morpho_like``
     - ``MORPHO()`` succeeds and ``tvlCap()`` succeeds with a 32-byte result.
     - :py:class:`~eth_defi.erc_4626.vault_protocol.vault_morpho.vault.VaultMorphoVault`.
   * - ``morpho_v2_like``
     - ``adaptersLength()`` succeeds and ``MORPHO()`` fails.
     - :py:class:`~eth_defi.erc_4626.vault_protocol.morpho.vault_v2.MorphoV2Vault`.

Canonical MetaMorpho exposes ``MORPHO()`` as the address of its underlying
Morpho Blue lending contract. Compatible implementations can expose the same
getter while changing deposit rules. Consequently, a successful probe is an
interface signal, not verification of the deployed bytecode. Other detected
features may coexist with these flags.

The shared factory,
:py:func:`~eth_defi.erc_4626.classification.create_vault_instance`, checks the
unattributed VaultMorpho flag before the ordinary V1 flag; protocol features matched earlier
in the factory retain precedence over this Morpho-specific branch. Both flags
are retained: ``morpho_like`` supplies lending-protocol classification used by
lending readers, while the more specific feature overrides protocol attribution;
ignoring ``vault_morpho_like`` would select a reader that misses its cap.
Do not duplicate this selection in scanners, strategies or CLI consumers.

Third-party implementations
^^^^^^^^^^^^^^^^^^^^^^^^^^^

A ``MORPHO()`` getter establishes a lending dependency, not official protocol
provenance. The capped ``VaultMorpho`` implementation is maintained separately;
see :ref:`vault-morpho-unattributed` for its verified source and unresolved
operator identity. Its additional feature selects a generic ERC-4626 adapter,
not :py:class:`~eth_defi.erc_4626.vault_protocol.morpho.vault_v1.MorphoV1Vault`.
The catalogue must not attach Morpho's website, API coverage or protocol risk
and fee defaults to that implementation.

Canonical Solidity references are `MetaMorpho
<https://github.com/morpho-org/metamorpho/blob/87ee5faf910d7224cee70120a94b0921c15ecab7/src/MetaMorpho.sol>`__
and `Morpho Vault V2
<https://github.com/morpho-org/vault-v2/blob/bcad37068507dba4193f79c48d539b241faa7f37/src/VaultV2.sol>`__.
These are source references for the canonical contracts, not substitutes for a
third-party implementation's own source repository.

Metadata requests
~~~~~~~~~~~~~~~~~

Warning and curator enrichment uses
:py:mod:`eth_defi.erc_4626.vault_protocol.morpho.session` for a shared request
budget and persisted provider cooldown. Existing warning data survives a
transient API failure; a successful refresh can clear obsolete warnings.

Deposit permissions
~~~~~~~~~~~~~~~~~~~

Canonical MetaMorpho V1 deposits are permissionless; compatible implementations
still need their own admission assessment. Morpho V2 is also permissionless
when ``receiveSharesGate`` and ``sendAssetsGate`` are both zero. A V2
deployment may configure custom implementations of the `Morpho gate interfaces
<https://github.com/morpho-org/vault-v2/blob/main/src/interfaces/IGate.sol>`__
for the share receiver and asset sender independently. The adapter therefore
reports a zero-gate deployment as permissionless and leaves an unrecognised
custom gate unknown instead of assuming either KYC or public access.

Robinhood Chain temporary API workaround
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Robinhood Chain (ID ``4663``) has active Morpho V2 vaults whose public API
coverage is currently incomplete. Until the API returns every production
Robinhood vault by address, a missing Morpho API record on that chain does not
apply the ``not_in_morpho_api`` hard-blacklist flag. The scanner still uses all
available on-chain metadata, but omits API-provided warning enrichment when no
record is available. This is intentionally a temporary exception and must be
removed once Morpho's Robinhood API coverage is complete.

Links
~~~~~

- `Listing <https://tradingstrategy.ai/vaults/protocols/morpho>`__
- `Homepage <https://morpho.org/>`__
- `App <https://app.morpho.org/>`__
- `Documentation <https://docs.morpho.org/>`__
- `Morpho V2 docs <https://docs.morpho.org/learn/concepts/vault-v2/>`__
- `GitHub (V2) <https://github.com/morpho-org/vault-v2>`__
- `Twitter <https://x.com/MorphoLabs>`__
- `DefiLlama <https://defillama.com/protocol/morpho>`__

Audits
~~~~~~

- `Morpho audits <https://docs.morpho.org/security/audits/>`__

Morpho V1 API
~~~~~~~~~~~~~

.. autosummary::
   :toctree: _autosummary_vault_morpho
   :recursive:

   eth_defi.erc_4626.vault_protocol.morpho.vault_v1

Morpho V2 API
~~~~~~~~~~~~~

.. autosummary::
   :toctree: _autosummary_morpho_v2
   :recursive:

   eth_defi.erc_4626.vault_protocol.morpho.vault_v2
