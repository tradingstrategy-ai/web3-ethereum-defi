Risk and fees across Tori exposures
-----------------------------------

The reviewed inventory contains 17 Ethereum and Monad vaults as at 29 October
2026. It includes native staking, ecosystem allocations, lending, senior and
junior tranches, locked Pendle yield tokens and Stake DAO liquidity positions.
These are separate products with their own infrastructure and operators.
The exact addresses and supporting evidence are maintained in
``docs/protocol-research/tori-vault-risk-and-fees.md`` and
:py:data:`eth_defi.erc_4626.vault_protocol.tori.disclosures.TORI_EXPOSURES`.
This is a dated inventory, not automatic discovery of every future integration.

Technical risk
~~~~~~~~~~~~~~

Native strUSD is assessed as High technical risk because of its upgradeable
contracts, privileged loss reporting and issuer valuation/reporting dependencies.
This is a conservative manual judgement, not a mechanical score or a predicted
probability of loss. Public
`source verification <https://sourcify.dev/server/v2/contract/1/0x6561272e3EbC9f2e07cDB12B7c474Db9132b977a?fields=all>`__
and the `audit inventory <https://docs.tori.finance/security/audits>`__
provide evidence for review but do not remove those dependencies.

Third-party protocol and existing product ratings are retained. In particular,
lending against Tori collateral introduces credit and collateral risk; that
alone does not justify changing a lender's technical rating. The notes identify
where a product-specific assessment of wrapper contracts remains unavailable.
Existing Severe ratings, stricter individual reviews and incident exclusions
remain in force.

Public notes explain economic risks separately: trading losses, custody,
depegs, borrower defaults, liquidations, pool imbalances, finite tranche
protection and yield-token maturity. Senior tranches are not guaranteed;
junior holders absorb losses first. A locked yield token is a claim to yield
until expiry, not a claim to the underlying principal. The reviewed YT rights
expire on 26 November 2026; afterwards only previously earned amounts can
remain claimable under each lock's rules.

Fee accounting
~~~~~~~~~~~~~~

Native strUSD has zero separate management, deposit and withdrawal charges.
Tori's `staking terms <https://docs.tori.finance/products/strusd>`__ say the
protocol retains some trading income before paying rewards. The percentage
is undisclosed, while the accounting mode is known to be internalised.
Keep the gross performance-fee percentage unknown; investor share-price
returns already reflect the retained income and must not deduct it again.

The Upshift ecosystem vault has its own charges. At Ethereum block 26299349
on 29 October 2026 these were 0.2% annual management, 10% performance,
zero standard withdrawal fee and 5% optional instant redemption. The earlier
fee waiver concerned pre-deposits. Current contract settings take precedence
over the historical launch schedule, and can change.

Further deployed-source and live-state checks on 29 October 2026 established:

* Royco senior, junior and yield-share protocol-fee settings were all zero
  at Ethereum block 26299721. Yield transferred between the tranches remains
  a separate economic cost.
* The two Pendle locks had no separate wrapper fee in the verified deployed
  source. Their underlying YT interest and reward fee rates were each 5%
  at block 26299746, on separate bases. Neither YT listed a separate onchain
  reward token at block 26299800. Both locks had withdrawals paused at
  block 26299714. Cash yield earned by YT held in the trUSD lock accrues to
  the lock itself, with no onchain distribution path to holders. Its
  administrator can transfer non-YT funds out; separate manual distributions
  have not been verified. The strUSD lock has per-holder claims that work
  during the withdrawal-only pause, though an administrator emergency pause
  also stops claims. Its administrator can reopen withdrawals immediately;
  upgrades still require the timelock.
* The three Stake DAO wrappers shared a 16.5% fee on fee-bearing rewards and
  separate 0.025% harvest compensation at block 26299708. These are reward
  charges, not a flat charge on all LP returns or principal. The Safe controlling
  those fee settings required one approval at block 26299746. Source caps the
  combined protocol and harvest rate at 40%.
* Term's RockawayX Tori vault charged 10% of reported profit through newly
  minted shares at block 26299700. Fees in its underlying Morpho allocation
  are an additional layer with a separate calculation base.
* Curvance charged 10% of borrower interest through newly minted protocol
  shares at Monad block 117664073, reducing lenders' interest income. Deposits
  are lent to borrowers; unpaid debt and failed liquidations can cause losses,
  and exits depend on unborrowed strUSD. The fee is separate from native
  strUSD rewards.

The evidence record identifies verified source, deployment relationships,
raw getters, denominators and control checks for these findings. A match
between each deployed version and a published audit remains incomplete;
source verification does not establish that a contract is safe.

Morpho and Accountable fees continue to use their maintained readers.
Some other products still lack supported scalar fee readers despite the
verified figures above. Those numerical fields remain unknown; the sourced
notes report the verified costs and access restrictions. Generic ERC-4626
does not define a fee interface. Reward-only and borrower-interest fees
must not be treated as a flat performance fee on all share-price returns.
No native staking fee assumption is applied to third-party products.

Missing fee figures and canonical sources
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Relevant vault notes include a labelled fee-model or fee-disclosure sentence
with a Markdown link to the official documentation. Native strUSD and positions
holding its yield explain that Tori has not published its retained-income
percentage. Royco, Pendle, Stake DAO, Term and Curvance notes link the relevant
product mechanics, distinguishing protocol charges, reward fees, interest fees
and transfers between tranches. A missing general fee percentage is not treated
as a zero charge or as proof that every product fee is undisclosed.

Protocol fee descriptions provide the corresponding links. Accountable's copy
also distinguishes NAV-strategy and loan-product fees, replacing the incorrect
claim that fee terms are not available in contract configuration.

Refresh and verification
~~~~~~~~~~~~~~~~~~~~~~~~

New migration ``013-tori-vault-risk-and-fees.py`` refreshes persisted fee fields
for present, matching rows in the reviewed inventory. It validates every
identity before RPC and completes all fee reads before staging any update.
Missing rows are reported without creation. Current risk and product disclosures resolve
during export. Disclosures are appended after the final incident, Morpho and
abnormal-value checks, preserving their explanations. The migration preserves
protocol and curator attribution, cached risk, flags, saved notes, prices, discovery
boundaries, reader state and timestamp caches. It does not backfill prices.

Use the guarded preview/apply and restart commands in :doc:`deployment` after
merge. Migration 012 is the prerequisite native classification repair;
013 is a separate subsequent refresh, so a successful 012 is not rerun.
Review the complete pending batch and expect 17 inspected rows when all
reviewed products are present. Check missing targets before declaring the
rollout complete. Repeated unchanged transformations produce no updates.

Run the optional read-only provider check with the supplied private environment
and a local catalogue containing the reviewed products::

    source .local-test.env && PYTHONPATH="$PWD" RUN_TORI_EXPOSURE_LIVE_TEST=1 timeout 180s poetry run pytest tests/vault/test_tori_exposure_live.py -q --log-cli-level=info

It reads Ethereum and recent Monad state and stages changes in memory. It
asserts the saved catalogue remains byte-identical. The runner, rather than
this test, owns production preview, apply, locks and persistence.
