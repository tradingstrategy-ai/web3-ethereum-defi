Veranta
=======

`Veranta <https://www.veranta.xyz/>`__, formerly known as Avantis, supplies leveraged markets on Base.
The unified avUSDC vault pools USDC as counterparty liquidity, earns trading
fees and bears trader-payout risk after the protocol buffer. See the
`vault documentation <https://docs.veranta.xyz/liquidity-providers/veranta-lp-vault-avusdc>`__.
The protocol changed its name in September 2026. Public protocol and curator
names and integration identifiers use Veranta; the onchain avUSDC token retains
its historical Avantis name.

Supported deployment
--------------------

Only Base ``0x944766f715b51967e56afde5f0aa76ceacc9e7f9`` is classified.
Legacy tranche deployments are excluded. The verified implementation ABI
and provenance are maintained under ``eth_defi/abi/veranta/``.

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

Veranta is the protocol curator of the reviewed avUSDC pool. The curator profile
shares the protocol's feed sources and artwork under the ``veranta`` slug.
Attribution follows the identified protocol; an independently operated wrapper
is not attributed to Veranta merely because its name mentions Veranta, Avantis or avUSDC.
Once migration 030 or a normal rescan identifies the protocol as Veranta,
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
actual collateral-health withdrawal rate is dynamic; it was 1.5% at block
53,497,070 and 0.5% at block 55,297,428. Published fee metadata and estimated net
returns use a fixed 0.5% withdrawal reference. Exact
transaction previews still use the contract's actual fee and fee switch.
The verified vault and manager fee paths establish zero separate direct LP
management and performance fees for the
reviewed implementation pairs. The adapter checks both proxies and manager
wiring at the requested block before reporting these zeros. Unknown upgrades
remain unknown, and a separate protocol fee rate remains unverified. The
platform's trading-fee revenue split is already reflected in pool income and
is not an additional LP performance fee.

The risk label is resolved from the maintained registry during export. No
additional database migration is needed for this risk-only assessment, and
existing manual ``_risk`` overrides remain untouched. The original numbered
onboarding migration 030 is still required for protocol identity and fee data.

Individual vault descriptions
-----------------------------

The reviewed Base deployment exposes maintained Markdown and a one-sentence
summary through the ordinary vault accessors. The scanner persists them as
``_description`` and ``_short_description``; individual vault exports publish
``description`` and ``short_description`` for the website. Protocol YAML and
curator profiles do not fill these fields.

Migration ``031-veranta-vault-description.py`` repairs only these two fields
on the existing row. It needs no RPC reads or price backfill and leaves
successful onboarding migration 030 unchanged. Follow the production reminder
in ``docs/README-vault-description-formatting.md`` for preview, apply and normal
export/publication. Text accessors do not establish current withdrawal liquidity.

Execution evidence
------------------

``tests/erc_4626/vault_protocol/test_veranta.py`` exercises the public factory
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

   ../../api/veranta/index

Fee publication repair
----------------------

Migration ``055-veranta-fees.py`` refreshes only ``_fees``, ``Mgmt fee``,
``Perf fee``, ``Deposit fee``, ``Withdraw fee``, ``Protocol fee`` and the
full maintained long description, including the fee disclosure, for the
confirmed Base row. It validates identity and the source-reviewed fee snapshot
before staging updates. Missing metadata is reported without creation;
unreviewed implementations or failed reads stop the migration. Short
descriptions, notes, curator identity, risk, flags, capabilities and historical state are
preserved. Successful migrations 030 and 031 must not be edited or rerun.

**Run after merge:** record the PR URL in migration 055 on the feature branch
before merge. Follow the `numbered migration runbook <https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/.claude/docs/migration-scripts.md#production-runbook>`__.
Inspect production ``docker-compose.yml`` and persistent mounts,
build the stamped merged image and stop persistent and competing writers.
From ``~/vault-scanner/web3-ethereum-defi``, use the same image throughout:

.. code-block:: shell

    source ../vault-rpc.env && docker compose stop vault-scanner-looped post-scanner
    source ../vault-rpc.env && docker compose run --rm --no-deps --entrypoint vault-migrate vault-scanner-oneshot list
    source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=true --entrypoint vault-migrate vault-scanner-oneshot run
    source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=false --entrypoint vault-migrate vault-scanner-oneshot run
    source ../vault-rpc.env && docker compose up -d --no-deps --force-recreate vault-scanner-looped post-scanner

Review the entire pending batch before apply. Expected for 055: at most one
existing row, six fee fields and its long description, a pinned Base source
block and no price backfill or reader reset. Inspect ``missing_specs`` during preview: a missing
row is a successful no-op, so investigate an unexpectedly missing target before
applying and recording the migration as complete. The runner acquires its
lifecycle guard before the writer lock. After normal export/upload, verify zero
management and performance fees, a fixed 0.5% published withdrawal fee,
externalised fee mode and available estimated net return data in the individual
vault JSON and website. The trading-revenue
allocation must not be deducted a second time. Preserve the existing price
history range; migration success alone does not publish the repair.

The source review establishes no separate direct LP management/performance
rate, not a guarantee about undistributed manager income. Keeper and governance
roles retain distribution and buffer-withdrawal discretion. Re-review the
static public fee description whenever either proxy is upgraded, alongside
the implementation-pair allowlist.
