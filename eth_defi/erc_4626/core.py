"""ERC-4626 core functions.

- Access ERC-4626 ABI
- Feature flags vaults can have
"""

import enum
from typing import Type

from eth_typing import HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.abi import get_contract, get_deployed_contract


class ERC4626Feature(enum.Enum):
    """Additional extensinons ERc-4626 vault may have.

    - Flag ERC-4626 matches in the scan
    - Use name/known calls to flag the protocol for which the vault belongs
    """

    #: Failed when probing with multicall, Deposit() event likely for other protocol
    broken = "broken"

    #: Asynchronous vault extension (ERC-7540)
    #: https://eips.ethereum.org/EIPS/eip-7540
    erc_7540_like = "erc_7540_like"

    #: Multi-asset vault extension (ERC-7575)
    #: https://eips.ethereum.org/EIPS/eip-7575
    erc_7575_like = "erc_7575_like"

    #: Lagoon protocol
    lagoon_like = "lagoon_like"

    #: Ipor protocol
    ipor_like = "ipor_like"

    #: Moonwell protocol
    moonwell_like = "moonwell_like"

    #: Morpho protocol
    morpho_like = "morpho_like"

    #: Harvest Finance like protocol
    harvest_finance = "harvest_finance"

    #: Panoptic
    #: https://panoptic.xyz/
    panoptic_like = "panoptic_like"

    #: Baklavaf
    #: BRT2
    baklava_space_like = "baklava_space_like"

    #: https://astrolab.fi/
    astrolab_like = "astrolab_like"

    #: Gains network
    #: https://github.com/GainsNetwork
    gains_like = "gains_like"

    #: Return Finacne
    return_finance_like = "return_finance_like"

    #: Arcadia Finance
    #: https://defillama.com/protocol/arcadia-finance
    arcadia_finance_like = "arcadia_finance_like"

    #: SATS DAO
    #: https://github.com/satsDAO/Satoshi
    satoshi_stablecoin = "satoshi_stablecoin"

    #: Athena
    #: https://www.athenafinance.io/
    athena_like = "athena_like"

    #: Reserve
    #: https://reserve.org/
    reserve_like = "reserve_like"

    #: Fluid
    #: https://docs.fluid.instadapp.io/
    fluid_like = "fluid_like"


def get_vault_protocol_name(features: set[ERC4626Feature]) -> str:
    """Deduct vault protocol name based on Vault smart contract features.

    At least one feature must match.
    """
    if ERC4626Feature.broken in features:
        return "<broken>"
    elif ERC4626Feature.morpho_like in features:
        return "Morpho"
    elif ERC4626Feature.fluid_like in features:
        return "Fluid"
    elif ERC4626Feature.harvest_finance in features:
        return "Harvest Finance"
    elif ERC4626Feature.ipor_like in features:
        return "IPOR"
    elif ERC4626Feature.lagoon_like in features:
        return "Lagoon"
    elif ERC4626Feature.morpho_like in features:
        return "Morpho"
    elif ERC4626Feature.panoptic_like in features:
        return "Panoptic"
    elif ERC4626Feature.astrolab_like in features:
        return "Astrolab"
    elif ERC4626Feature.baklava_space_like in features:
        return "Baklava"
    elif ERC4626Feature.gains_like in features:
        return "Gains Network"
    elif ERC4626Feature.return_finance_like in features:
        return "Return Finance"
    elif ERC4626Feature.arcadia_finance_like in features:
        return "Arcadia Finance"
    elif ERC4626Feature.satoshi_stablecoin in features:
        return "SATS Token"
    elif ERC4626Feature.athena_like in features:
        return "Athena Finance"
    elif ERC4626Feature.reserve_like in features:
        return "Reserve"
    elif ERC4626Feature.erc_7540_like in features:
        return "<generic 7540>"
    return "<generic 4626>"


def get_erc_4626_contract(web3: Web3) -> Type[Contract]:
    """Get IERC4626 interface."""
    return get_contract(
        web3,
        "lagoon/IERC4626.json",
    )


def get_deployed_erc_4626_contract(web3: Web3, address: HexAddress) -> Contract:
    """Get IERC4626 deployed at some address."""
    return get_deployed_contract(
        web3,
        "lagoon/IERC4626.json",
        address=address,
    )