"""Automatic investment strategy classifications for Arcus pTokens.

Arcus describes its pTokens as automatically rebalanced, leveraged claims on
perpetual accounts in its `product announcement
<https://arcus.xyz/blog/ptokens-a-new-primitive-on-arcus>`__. These defaults apply
to the contract family detected through its Robinhood bridge vault, including
new products without address-specific display metadata.
"""

from eth_typing import HexAddress

from eth_defi.vault.strategy_tag import StrategyTag


def get_strategy_tags(_address: HexAddress) -> set[StrategyTag]:
    """Return the strategy tags shared by every detected Arcus pToken.

    Classification follows the verified contract-family detection, rather than
    parsing token names or keeping a static list of existing products. Return
    a new set so callers cannot mutate the defaults.

    :param _address:
        Address of a vault already classified as an Arcus pToken.
    :return:
        Directional leverage and perpetual futures strategy tags.
    """
    return {StrategyTag.directional_leverage, StrategyTag.perpetual_futures}
