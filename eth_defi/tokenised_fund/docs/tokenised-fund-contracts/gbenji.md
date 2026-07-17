# Franklin OnChain U.S. Government Money Fund (gBENJI) contract research

Checked on 2026-07-17. This is contract-discovery research, not an assessment
of investor eligibility, legal rights, custody, or investment suitability.

## Result

| Fund name | Chain and token | Smart-contract name | Description | GitHub | Docs |
| --- | --- | --- | --- | --- | --- |
| Franklin OnChain U.S. Government Money Fund, international share class (`gBENJI`) | Stellar classic issued asset: `gBENJI:GD5J73EKK5IYL5XS3FBTHHX7CZIYRP7QXDL57XFWGC2WVYWT326OBXRP` | **No custom smart contract.** Classic Stellar `credit_alphanum12` asset, administered by its issuer account; it has the protocol-provided Stellar Asset Contract (SAC) identity `CAZGJD4BG6RLFQIAGPDPSX3IR73CBSVDEIBUDQGDZ3RCGGSOYSVBDSM7` for Soroban interoperability. | A seven-decimal, issuer-controlled Stellar fund-share asset. Holders use trustlines, and the issuer has required-authorization, revocation, and clawback flags. The SAC identifier is a built-in wrapper for a classic asset, not evidence of Franklin-deployed Soroban/Wasm token code. There is no EVM contract. | No public gBENJI Soroban/Wasm programme repository was found. The Stellar asset model is protocol-native; [Stellar's SAC implementation documentation](https://developers.stellar.org/docs/tokens/stellar-asset-contract) is the framework reference. | [Franklin Stellar TOML](https://www.franklintempleton.com/.well-known/stellar.toml), [Franklin BENJI contract/address hub](https://digitalassets.franklintempleton.com/benji/benji-contracts/), [Stellar.expert asset](https://stellar.expert/explorer/public/asset/gBENJI-GD5J73EKK5IYL5XS3FBTHHX7CZIYRP7QXDL57XFWGC2WVYWT326OBXRP) |

## Identification: classic asset, not a custom Soroban token

gBENJI is unambiguously a **classic Stellar issued asset**. Its identifier has
the Stellar classic form `asset_code:issuer_G_account`; the asset code
`gBENJI` is seven characters and the issuer is the `G...` account
`GD5J73EKK5IYL5XS3FBTHHX7CZIYRP7QXDL57XFWGC2WVYWT326OBXRP`.
Horizon reports its type as `credit_alphanum12`, rather than a contract token
with a `C...` contract address.

Franklin Templeton's issuer-controlled
[`stellar.toml`](https://www.franklintempleton.com/.well-known/stellar.toml)
lists that exact account as the gBENJI issuer. It describes the asset as one
share of the Franklin OnChain U.S. Government Money Fund, identifies the
underlying fund/share class as `LU2900381208`, specifies seven display
decimals, and directs investors to the gBENJI institutional website for buying,
selling, and transfers. Franklin's
[developer address hub](https://digitalassets.franklintempleton.com/benji/benji-contracts/)
independently lists the same Stellar fund-token issuer address.

The associated official
[Horizon asset record](https://horizon.stellar.org/assets?asset_code=gBENJI&asset_issuer=GD5J73EKK5IYL5XS3FBTHHX7CZIYRP7QXDL57XFWGC2WVYWT326OBXRP&limit=1)
reports:

| Field | Finding |
| --- | --- |
| Asset type | `credit_alphanum12` |
| Asset code / issuer | `gBENJI` / `GD5J…BXRP` |
| Asset contract ID | `CAZGJD4BG6RLFQIAGPDPSX3IR73CBSVDEIBUDQGDZ3RCGGSOYSVBDSM7` |
| Authorised trustlines | 14 at the time queried |
| Issuer controls | `auth_required: true`, `auth_revocable: true`, `auth_clawback_enabled: true` |

Stellar distinguishes classic assets issued by `G...` accounts from custom
contract tokens issued by `C...` addresses. A classic asset is uniquely
identified by its code plus issuer, exactly as gBENJI is. The
[`GD5J…BXRP` issuer account on Stellar.expert](https://stellar.expert/explorer/public/account/GD5J73EKK5IYL5XS3FBTHHX7CZIYRP7QXDL57XFWGC2WVYWT326OBXRP)
is therefore the relevant on-chain control point, not an EVM address or
custom-token contract.

## What the Soroban contract ID means

Every Stellar classic asset has a deterministic/reserved address for the
protocol-provided **Stellar Asset Contract** (SAC). Stellar documents that the
SAC is a special built-in contract implementing CAP-46-6 and the SEP-41 token
interface, through which Soroban contracts can interact with classic
trustline-based assets. Anyone can initiate deployment of that built-in wrapper;
the asset issuer need not be involved.

Consequently, Horizon's gBENJI `contract_id`
`CAZG…DSM7` should be recorded as the SAC identity if a Soroban integration
needs one, but it is not a Franklin-authored Soroban programme, an independently
auditable Wasm deployment, or a replacement for the `gBENJI:GD5J…BXRP`
asset identifier. The classic asset's issuance, trustlines, authorisation and
clawback policy remain governed by Stellar protocol operations and the issuer
account flags.

No public Franklin source identifies a custom gBENJI Soroban programme, calls,
Wasm hash, or separate transfer-agent contract. A GitHub/web search of the
issuer, exact asset identifier, `gBENJI`, and `Soroban` found no such
repository or contract. Etherscan and Sourcify do not apply to this
non-EVM asset.

## Asset behaviour and controls

| Area | Classic Stellar mechanism | Effect for gBENJI |
| --- | --- | --- |
| Asset identity | `gBENJI:issuer` | Both code and issuer must match; a ticker alone is not sufficient to identify the asset. |
| Holding | Trustline on the holder account | A recipient needs a gBENJI trustline and issuer authorisation. The asset record showed authorised and unauthorised trustlines, consistent with a permissioned fund asset. |
| Issue/distribution | Issuer account sends the credit asset | There is no public `mint()` ABI. Issuance is a Stellar payment/issuer-account operation and is controlled through the issuer's multi-signature account policy. |
| Transfers | Stellar payment operations between authorised trustlines | The issuer's `AUTH_REQUIRED` setting prevents an unapproved trustline from holding the asset. The institutional transfer process remains subject to Franklin onboarding. |
| Revocation and clawback | `AUTH_REVOCABLE` and `AUTH_CLAWBACK_ENABLED` issuer flags | The issuer retains protocol-level ability to revoke authorisation and claw back assets under Stellar's defined operations; this is a material administrative control. |
| Soroban use | Built-in SAC at `CAZG…DSM7` | Permits SEP-41-style interactions if the SAC is used, while preserving the classic asset's issuer controls. It does not make gBENJI a custom smart-contract token. |

## Fund and protocol conclusion

**Conclusion: Franklin Templeton gBENJI is a permissioned classic Stellar
fund-share asset, not a custom Soroban contract and not an EVM smart contract
— high confidence.**

The issuer-controlled TOML, Franklin address hub, and Horizon's
`credit_alphanum12` classification agree on the code-plus-issuer asset. The
fund's restrictions are implemented using Stellar's native trustline and issuer
authorisation/clawback framework. This is the classic Stellar protocol family,
with the generic built-in SAC available only as the interoperable Soroban
surface.

Franklin's public multi-chain developer hub lists modular EVM contracts for
BENJI on Ethereum-compatible chains, but for gBENJI it lists only the Stellar
fund-token issuer address. Do not project the EVM registry/authorisation/
transfer-agent module architecture onto this Stellar asset without direct
issuer evidence.

## On-chain supply and ABI price availability

At 2026-07-17, Horizon reported **54,534,218.0235468 gBENJI** in authorised
trustline balances. As a Stellar classic asset, gBENJI has no issuer-written
token ABI and no on-chain share-price accessor; its deterministic Stellar Asset
Contract wrapper does not change that fact.

## Integration implications

- Store the canonical Stellar identifier as
  `gBENJI:GD5J73EKK5IYL5XS3FBTHHX7CZIYRP7QXDL57XFWGC2WVYWT326OBXRP`,
  not only `gBENJI` and not the SAC ID alone.
- Model it as a classic asset with trustlines and issuer-controlled
  authorisation, revocation, and clawback, rather than ERC-20/ERC-4626 or a
  custom Soroban contract.
- For a Soroban integration, use the deterministic SAC identity only after
  confirming the target interface and current issuer policy; do not assume it
  offers a fund-subscription/redemption API.
- Use Franklin's institutional gBENJI route for investment, redemption and
  transfer eligibility. An authorised on-chain trustline does not replace the
  issuer's legal and operational onboarding requirements.

## Primary sources

- [Franklin Templeton `stellar.toml`](https://www.franklintempleton.com/.well-known/stellar.toml)
- [Franklin BENJI developer address hub](https://digitalassets.franklintempleton.com/benji/benji-contracts/)
- [Stellar Horizon gBENJI asset record](https://horizon.stellar.org/assets?asset_code=gBENJI&asset_issuer=GD5J73EKK5IYL5XS3FBTHHX7CZIYRP7QXDL57XFWGC2WVYWT326OBXRP&limit=1)
- [Stellar.expert gBENJI asset](https://stellar.expert/explorer/public/asset/gBENJI-GD5J73EKK5IYL5XS3FBTHHX7CZIYRP7QXDL57XFWGC2WVYWT326OBXRP)
- [Stellar assets: classic versus contract tokens](https://developers.stellar.org/docs/learn/fundamentals/stellar-data-structures/assets)
- [Stellar Asset Contract documentation](https://developers.stellar.org/docs/tokens/stellar-asset-contract)
