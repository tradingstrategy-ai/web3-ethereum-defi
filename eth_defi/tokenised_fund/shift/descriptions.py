"""Curated, deterministic descriptions for reviewed ShiftVault products.

Shift's public app currently embeds its product catalogue in the versioned
frontend bundle instead of exposing a stable product-metadata endpoint. Keep
the descriptions here so scanner output does not depend on downloading and
parsing a changing application bundle at runtime.

Sources used for this curation:

- Shift application product pages: https://app.shiftprotocol.xyz/
- Shift documentation: https://shiftprotocol.gitbook.io/shift
- Shift's official announcements: https://x.com/shiftprotocol_

External point programmes, target rates and allocations can change. The text
below describes the published strategy design rather than promising a return,
reward allocation or immediate redemption liquidity.
"""

from dataclasses import dataclass

from eth_typing import HexAddress


@dataclass(frozen=True, slots=True)
class ShiftVaultDescription:
    """Display text for one reviewed ShiftVault deployment.

    :param short_description:
        Concise listing summary.
    :param long_description:
        Strategy, asset-flow and risk-aware explanation for scanner exports.
    """

    short_description: str
    long_description: str


#: Shift app catalogue and Shift-authored documentation/social announcements,
#: reviewed on 2026-07-27. Key by chain and lower-case share-token address so
#: a matching contract address on a different chain cannot inherit a product
#: description accidentally.
SHIFT_VAULT_DESCRIPTIONS: dict[tuple[int, HexAddress], ShiftVaultDescription] = {
    (8453, "0xaf69bf9ea9e0166498c0502af5b5945980ed1e0e"): ShiftVaultDescription(
        short_description="Liquid USDC token representing an allocation to the Paradex Gigavault.",
        long_description=(
            "[ltPARA](https://app.shiftprotocol.xyz/strategies/lt-para) is Shift's liquid-token wrapper for the Paradex Gigavault. Shift deploys USDC when capacity is available in the underlying product and issues an ERC-20 receipt token intended for DeFi composability.\n\n"
            "## Strategy\n\n"
            "- The token's value depends on the Paradex allocation and its underlying trading and funding outcomes, rather than an interest rate promised by Shift.\n"
            "- Shift's [address registry](https://shiftprotocol.gitbook.io/shift/resources/addresses) identifies the supported contract and network.\n\n"
            "## Rewards and liquidity\n\n"
            "Shift's [official announcements](https://x.com/shiftprotocol_) describe Paradex XP and Shift points as possible additional programme rewards. Those programmes, their transferability and any bonus are external, time-dependent and not part of the vault's guaranteed share value. Deposits and redemptions follow Shift's executor-approved request and batch-settlement process, so a liquid token does not mean immediate redemption from the vault; see the [Shift FAQ](https://shiftprotocol.gitbook.io/shift/resources/faq) for the product lifecycle."
        ),
    ),
    (8453, "0x4ce3ec1b7b4ffb33a0b70c64a0560a3f341aa2e1"): ShiftVaultDescription(
        short_description="Market-neutral USDC strategy combining Extended exposure with a hedged BTC basis position.",
        long_description=(
            "[extUSD](https://app.shiftprotocol.xyz/strategies/ext-usd) is Shift's Extended Basis USD product. The app describes it as a hybrid strategy using Extended's XVS vault alongside a BTC market-neutral basis trade, with capital deployed across Extended and Coinbase.\n\n"
            "## Strategy\n\n"
            "- Shift describes the intended leverage range as conservative, between 1x and 3x.\n"
            "- The strategy remains exposed to venue, basis, hedge, funding, execution and oracle risks.\n\n"
            "## Announcements and liquidity\n\n"
            "Shift's [official announcement feed](https://x.com/shiftprotocol_) has described extUSD as a market-neutral yield strategy with eligibility for Extended-related points. Shift also provides a [Pendle community pool](https://shiftprotocol.gitbook.io/shift/using-shift/pendle-yield-pools) for separating principal and yield/points exposure. Neither a displayed APR nor an external points programme is a fixed return. Share valuation depends on the Shift TVL feed, and withdrawals remain subject to the protocol's request, executor and timelock lifecycle described in the [Shift FAQ](https://shiftprotocol.gitbook.io/shift/resources/faq)."
        ),
    ),
    (42161, "0x956bdd9c18b786b082fd50c52722d254f0cb6964"): ShiftVaultDescription(
        short_description="Hedged Lighter LLP wrapper designed to retain a market-neutral profile while meeting LIT staking requirements.",
        long_description=(
            "[ltLLP](https://app.shiftprotocol.xyz/strategies/lt-llp) is Shift's wrapper around Lighter LLP. The app describes capital being deployed to Lighter, with the LIT exposure hedged while the strategy meets LIT staking requirements and deposits into LLP.\n\n"
            "## Strategy\n\n"
            "- The resulting ERC-20 receipt is intended to make this allocation composable.\n"
            "- It does not turn the underlying venue position into a risk-free or instantly redeemable asset.\n\n"
            "## Rewards and liquidity\n\n"
            "Lighter participation and its associated points programme are presented in [Shift announcements](https://x.com/shiftprotocol_) as potential additional rewards. Their rules, eligibility and value are controlled by external programmes and may change. Holders remain exposed to Lighter, hedge, funding, liquidity, smart-contract, TVL-feed and Shift executor risks; withdrawals require the normal Shift request and batch-resolution process described in the [Shift FAQ](https://shiftprotocol.gitbook.io/shift/resources/faq)."
        ),
    ),
}


def get_shift_vault_description(chain_id: int, vault_address: HexAddress | str) -> ShiftVaultDescription | None:
    """Look up static ShiftVault description by chain and share-token address.

    :param chain_id:
        EVM chain hosting the reviewed ShiftVault deployment.
    :param vault_address:
        ShiftVault ERC-20 share-token address.
    :return:
        Curated description when the deployment is in the reviewed registry.
    """

    return SHIFT_VAULT_DESCRIPTIONS.get((chain_id, HexAddress(vault_address.lower())))
