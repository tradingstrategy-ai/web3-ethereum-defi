Upshift API
-----------

`Upshift <https://www.upshift.finance/>`__ vault protocol integration.

Upshift provides institutional onchain asset-management infrastructure for
non-custodial yield vaults. The metadata reader fetches public descriptions,
strategist identities and named operator wallets from the documented Upshift
vault API. Upshift does not publish an explicit curator field.

.. autosummary::
   :toctree: _autosummary_upshift
   :recursive:

   eth_defi.erc_4626.vault_protocol.upshift.vault
   eth_defi.erc_4626.vault_protocol.upshift.deployment
   eth_defi.erc_4626.vault_protocol.upshift.deposit_redeem
   eth_defi.erc_4626.vault_protocol.upshift.offchain_metadata

Deposits and withdrawals
~~~~~~~~~~~~~~~~~~~~~~~

Multi-asset vaults accept their reference asset as well as extra whitelisted
inputs, and mint a separate LP token. The manager includes the LP approval
needed to escrow shares for a queued withdrawal. Legacy single-asset vaults
use ``requestRedeem`` rather than the standard ERC-4626 ``redeem``.

After maturity an admitted caller can claim a funded request directly. Public
batch processing can also pay it; the existing completion-recovery hook matches
its date, shares and actual token transfer. One receiver/date obligation must
belong to one request. Outside additions halt settlement instead of allocating
an aggregate payment. Pending legacy claims retain their fixed net entitlement;
multi-asset claims retain floating share value until they are paid.

Zero-lag legacy requests pay in the request transaction; zero-lag multi-asset
vaults use their validated instant selector. Sender whitelists, receiver quotas,
blacklists, pauses, caps and payout liquidity remain deployment-specific.
A USDC input does not establish a USDC payout: strategies must verify both
conversion directions before using a vault whose reference asset differs.
