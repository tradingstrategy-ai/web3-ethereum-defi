# Infinit contract interfaces

Verified implementation ABIs fetched from Etherscan v2 on 2026-10-26,
Arbitrum One (42161). The deployed clones and manager remain distinct from
their implementation contracts.

| Interface | Deployment | Implementation source |
| --- | --- | --- |
| VaultManager | `0xaFE480f375EBd13dF703ef50b429357d29D162Ee` | [VaultManager](https://arbiscan.io/address/0x0bb4f37f9bd8fe3a72c8acb53509ae95ca5343ab#code) |
| Vault | `0xe64655E24b66073C73300aA924cD6dF5C716638F` | [Vault](https://arbiscan.io/address/0x3bdabf4bb61069b7ec117241c437f32b269306cf#code) |
| TokenVault | `0xb5Fb43EfD9b607C6ca863A56d22C491c82275c08` | [TokenVault](https://arbiscan.io/address/0x40247dc12a4f3b9ccac8095e349827c37fd276a3#code) |

The actual vault and receipt were created in block 483358787, transaction
`0xbef5388cf1f6da875e50693ae33632a5d2ad3c366dd6ff802bf7d6d36cf53506`.
The manager was deployed in block 472227568, transaction
`0xd3c76048ae1e816c38a082a5d4c861c854b6a42880752ffe91079b07aef6a46b`.

Page 4 of the PeckShield report linked from the [official audit
page](https://docs.infinit.tech/peckshield-report) identifies
`https://github.com/Saprolings/vault-contracts.git`, reviewed commit
`d1fd00f8`. The repository returns 404. The downloaded report is a release
candidate and leaves the post-fix commit as TBD. These ABIs describe verified
deployed source; no comparison against the inaccessible audited commit is
claimed.

## Fee treatment review

Reviewed the verified Solidity source on 2026-10-26 and checked deployment
bindings at Arbitrum block **518459406**. The manager's ERC-1967 slot still
points to the implementation above. The actual vault and receipt token have
EIP-1167 runtimes embedding the respective implementation addresses above;
the factory's current implementation getter alone would not prove which
implementation an existing clone uses.

The deployed contracts do **not implement the advertised strategy fees**:

- `VaultManager.deposit(index, amount)` sends the entire USDC amount to
  `safeAddress`, then calls the vault to mint exactly `amount` receipts to
  the depositor. There is no entry-fee deduction or fee-recipient split.
- `Vault.deposit(user, amount)` calls `TokenVault.mint(user, amount)`.
  The receipt token checks its supply cap and uses ordinary ERC-20 mint/burn
  accounting. There is no fee-share minting branch or transfer-tax override.
- `VaultManager.withdraw(index, shares)` checks maturity and delegates to the
  vault. `Vault.withdraw(user, shares)` snapshots its **local USDC balance**
  and receipt supply, calculates `floor(shares * balance / supply)`, burns
  the shares, and transfers that whole calculated amount to the user.
  `previewWithdraw` uses the same proportional calculation.
- There are no management/performance fee rates, fee recipient, daily accrual
  timestamps, high-water-mark storage, fee collection method or realised-profit
  calculation in these contracts. The manager remains upgradeable, so this
  conclusion is scoped to the verified implementation at the checked block.

The [published terms](https://docs.infinit.tech/infinit-ai-managed-trading-strategy-marketplace/vault-terms)
instead specify management fees accruing daily on AUM and performance fees on
realised profit above the high-water mark, at 2% annually and 20% respectively
for Apex. These are documented commercial terms, not parameters enforced by
the reviewed Solidity. Fee accounting/extraction therefore requires external
operator or custody/settlement processing. For example, funding the redemption
vault after retaining fees would make the final withdrawal ratio reflect those
fees; the contracts neither require nor verify that processing.

This does not establish whether the **application's historical price series**
reserves accrued fees. A contemporaneous public API/RPC check observed about
549,313.70 USDC of published assets versus 549,149.57 USDC of combined native
and `xyz` trading equity (approximately 0.03% difference). The vault contract's
USDC balance was zero. The close agreement is consistent with account-equity
pricing, but fees might already have been removed from that account, and the
observations are not timestamp-synchronised. It is not proof of gross or net
fee treatment.

Keep `VAULT_PROTOCOL_FEE_MATRIX["Infinit"] = None`: neither `feeless`,
`externalised`, `internalised_skimming` nor `internalised_minting` is established
for the canonical API share-price source. Investor net returns remain unknown
until application accounting evidence or a reconciled settlement establishes
the treatment. No fee matrix or numerical fee values were changed by this review.
