Avantis
=======

`Avantis <https://www.veranta.xyz/>`__ supplies leveraged markets on Base.
The unified avUSDC vault pools USDC as counterparty liquidity, earns trading
fees and bears trader-payout risk after the protocol buffer. See the
`vault documentation <https://docs.veranta.xyz/liquidity-providers/veranta-lp-vault-avusdc>`__.
The protocol renamed itself Veranta in September 2026; this adapter retains
Avantis as the name of the existing avUSDC deployment.

Supported deployment
--------------------

Only Base ``0x944766f715b51967e56afde5f0aa76ceacc9e7f9`` is classified.
Legacy tranche deployments are excluded. The verified implementation ABI
and provenance are maintained under ``eth_defi/abi/avantis/``.

Deposits and redemptions are synchronous ERC-4626 transactions. Withdrawal
fees depend on collateral health and are deducted from the payout. There is
no epoch or queue; utilisation and liquidity limits can still prevent exits.
Quotes use the deployed previews, retaining exact fee rounding. Standard
ERC-4626 redemption has no minimum-payout argument: a fee-tier change between
preflight and execution can reduce proceeds, and changed liquidity can cause
a revert.

Account admission is permissionless for the two reviewed implementations.
Unreviewed upgrades return unknown admission rather than inheriting that
classification. This is separate from deposit caps and withdrawal capacity.

Curator attribution
-------------------

Avantis is the protocol curator of the reviewed avUSDC pool. The curator profile
shares the protocol's feed sources and artwork under the ``avantis`` slug.
Attribution follows the identified protocol; an independently operated wrapper
is not attributed to Avantis merely because its name mentions Avantis or avUSDC.
Once migration 030 or a normal rescan identifies the protocol as Avantis,
metrics export resolves the curator. No additional curator migration is needed.

Technical risk and fee assessment
---------------------------------

The technical risk classification is **Severe**. The vault, manager and their
implementation sources are verified on Base, and published reports include
both vault and manager contracts. However, the audited core-contract
repository is not publicly accessible as a maintained development repository.
Public SDK development and public audit reports (PDFs) do not establish ongoing
contract-development transparency required by the
`technical risk framework <https://tradingstrategy.ai/blog/announcing-vault-technical-risk-framework-beta>`__.

The same Safe address is vault manager governance and owns the vault
ProxyAdmin. On 4 November 2026, block 53,497,176, that Safe had a three-of-five
owner threshold.
Upgradeable contracts and privileged configuration remain part of the technical
control surface. This assessment is separate from trader-payout market risk.
Audit scope does not establish an exact match to every subsequent upgrade.
The official audit index still says v2 reports will be added, despite attached
PDFs; complete coverage of the live generation remains unconfirmed.

Fee mode is **externalised**: the withdrawal charge reduces the investor's
payout and is not already deducted from the quoted gross share price. The
reviewed unified implementations charge zero deposit balancing fees. The
collateral-health withdrawal rate is read dynamically; it was 1.5% at block
53,497,070. Management, performance and separate protocol fee rates remain
unknown. The platform's trading-fee revenue split is not an LP performance fee.

The risk label is resolved from the maintained registry during export. No
additional database migration is needed for this risk-only assessment, and
existing manual ``_risk`` overrides remain untouched. The original numbered
onboarding migration 030 is still required for protocol identity and fee data.

Execution evidence
------------------

``tests/erc_4626/vault_protocol/test_avantis.py`` exercises the public factory
and the full lifecycle at ``BASE_MIDNIGHT_BLOCK`` and fixed post-upgrade block
53,496,246 through SimpleVaultV0 with a separate, guarded asset manager.
The additional pooled fork covers the current implementation, which is absent
at the canonical block. Generic guard rejection checks stay on the canonical fork.

.. list-table:: Manager call coverage
   :header-rows: 1

   * - Call
     - Guard configuration and evidence
   * - USDC approval
     - ``whitelistERC4626`` approval destination; guarded execution and redirected-approval refusal.
   * - ``deposit(uint256,address)``
     - Standard ERC-4626 policy; shares and receipt analysis, redirected-receiver refusal and cap preflight.
   * - ``redeem(uint256,address,address)``
     - Standard ERC-4626 policy; full share burn and net payout, redirected receiver/owner refusal and liquidity preflight.
   * - Settlement
     - Synchronous; ``force_settle(None)`` confirms no settlement is required.

Source and security references
------------------------------

- `Official SDK organisation <https://github.com/Avantis-Labs>`__
- `Audit index <https://docs.veranta.xyz/security/audits>`__
- `Zellic report <https://reports.zellic.io/publications/avantis/>`__
- `Withdrawal fee rules <https://docs.veranta.xyz/liquidity-providers/risk-management>`__
- `DefiLlama listing, now Veranta <https://defillama.com/protocol/veranta>`__

The historical audits do not establish coverage of every later upgrade.

.. toctree::
   :maxdepth: 1

   ../../api/avantis/index
