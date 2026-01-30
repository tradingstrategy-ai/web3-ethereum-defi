"""ERC-20 token deployment and manipulation.

Deploy ERC-20 tokens to be used within your test suite.

`Read also unit test suite for tokens to see how ERC-20 can be manipulated in pytest <https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/tests/test_token.py>`_.
"""

import datetime
import json
import logging
import os
import warnings
from collections import OrderedDict, defaultdict
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from functools import cached_property
from pathlib import Path
from typing import Any, Iterable, Optional, TypeAlias, TypedDict, Union

import cachetools
from web3.contract.contract import ContractFunction, ContractFunctions

from eth_defi.compat import native_datetime_utc_now
from eth_defi.event_reader.conversion import convert_int256_bytes_to_int, convert_solidity_bytes_to_string
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult, read_multicall_chunked
from eth_defi.event_reader.web3factory import Web3Factory
from eth_defi.provider.named import get_provider_name
from eth_defi.sqlite_cache import PersistentKeyValueStore

with warnings.catch_warnings():
    # DeprecationWarning: pkg_resources is deprecated as an API. See https://setuptools.pypa.io/en/latest/pkg_resources.html
    warnings.simplefilter("ignore")
    try:
        from eth_tester.exceptions import TransactionFailed
    except ImportError:
        # New Web3.py versions got rid of this?
        # Mock here
        class TransactionFailed(Exception):
            pass


from eth_typing import HexAddress
from requests.exceptions import ReadTimeout
from web3 import Web3
from web3.contract import Contract
from web3.exceptions import BadFunctionCallOutput, ContractLogicError

from eth_defi.abi import get_deployed_contract
from eth_defi.deploy import deploy_contract
from eth_defi.utils import sanitise_string

logger = logging.getLogger(__name__)

#: List of exceptions JSON-RPC provider can through when ERC-20 field look-up fails
#: TODO: Add exceptios from real HTTPS/WSS providers
#: `ValueError` is raised by Ganache
_call_missing_exceptions = (TransactionFailed, BadFunctionCallOutput, ValueError, ContractLogicError)

#: By default we cache 1024 token details using LRU in the process memory.
#:
DEFAULT_TOKEN_CACHE = cachetools.LRUCache(1024)

#: ERC-20 address, 0x prefixed string
TokenAddress: TypeAlias = str


#: Addresses of wrapped native token (WETH9) of different chains
WRAPPED_NATIVE_TOKEN: dict[int, HexAddress | str] = {
    # Mainnet
    1: "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
    # Base
    8453: "0x4200000000000000000000000000000000000006",
    # WBNB
    56: "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",
    # WETH: Arbitrum
    42161: "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
    # WAVAX
    43114: "0xB31f66AA3C1e785363F0875A1B74E27b85FD66c7",
    # WETH: Arbitrum Sepolia
    421614: "0x7b79995e5f793A07Bc00c21412e50Ecae098E7f9",
}

#: Addresses of USDC of different chains
USDC_NATIVE_TOKEN: dict[int, HexAddress | str] = {
    # Mainnet
    1: "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
    # Base
    8453: "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    # Ava
    43114: "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E",
    # Arbitrum
    42161: "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
    # BNB
    # https://www.coingecko.com/en/coins/binance-bridged-usdc-bnb-smart-chain
    56: "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d",
    # Arbitrum Sepolia
    421614: "0x75faf114eafb1BDbe2F0316DF893fd58CE46AA4d",
}

#: Bridged USDC of different chains
BRIDGED_USDC_TOKEN: dict[int, HexAddress | str] = {
    # Arbitrum
    42161: "0xff970a61a04b1ca14834a43f5de4533ebddb5cc8",
}


#: Used in fork testing
USDC_WHALE: dict[int, HexAddress | str] = {
    # Base
    #
    8453: "0x40EbC1Ac8d4Fedd2E144b75fe9C0420BE82750c6",
    # Arbitrum
    # Coinbase 10
    # https://arbiscan.io/token/0xaf88d065e77c8cc2239327c5edb3a432268e5831#balances
    42161: "0x3DD1D15b3c78d6aCFD75a254e857Cbe5b9fF0aF2",
    # To find large holder accounts, use polygonscan <https://polygonscan.com/token/0x2791bca1f2de4661ed88a30c99a7a9449aa84174#balances>
}

# Bridged USDC.e
# Used in fork testing
USDCE_WHALE: dict[int, HexAddress | str] = {
    # To find large holder accounts, use polygonscan <https://polygonscan.com/token/0x2791bca1f2de4661ed88a30c99a7a9449aa84174#balances>
    #
    # 137: "0x611f7bF868a6212f871e89F7e44684045DdFB09d",
    # Okex https://intel.arkm.com/explorer/token/bridged-usdc-polygon-pos-bridge
    137: "0x343d752bB710c5575E417edB3F9FA06241A4749A",
}

#: Used in fork testing
USDT_WHALE: dict[int, HexAddress | str] = {
    # BNB Chain
    # https://bscscan.com/token/0x55d398326f99059ff775485246999027b3197955#balances
    56: Web3.to_checksum_address("0x128463A60784c4D3f46c23Af3f65Ed859Ba87974"),
    # Arbitrum
    # https://arbiscan.io/token/0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9#balances
    42161: "0x9E36CB86a159d479cEd94Fa05036f235Ac40E1d5",
}

#: Addresses USDT Tether of different chains
USDT_NATIVE_TOKEN: dict[int, HexAddress] = {
    # Mainnet
    1: "0xdAC17F958D2ee523a2206206994597C13D831ec7",
    56: "0x55d398326f99059fF775485246999027B3197955",
    # Avalanche USDT.E
    43114: "0x9702230A8Ea53601f5cD2dc00fDBc13d4dF4A8c7",
    # Arbitrum
    42161: "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9",
}


#: Sky (MakerDAO) new tokens
SUSDS_NATIVE_TOKEN: dict[int, HexAddress] = {
    # Base
    8453: "0x5875eEE11Cf8398102FdAd704C9E96607675467a",
}

#: Berachain
#: 0xFCBD14DC51f0A4d49d5E53C2E0950e0bC26d0Dce
#: https://docs.berachain.com/learn/pol/tokens/honey
HONEY_NATIVE_TOKEN: dict[int, HexAddress] = {
    # Berachain
    80094: "0xFCBD14DC51f0A4d49d5E53C2E0950e0bC26d0Dce",
}


#: Token symbols that are stablecoin like.
#: Note that it is *not* safe to to check the token symbol to know if a token is a specific stablecoin,
#: but you always need to check the contract address.
#: Checking against this list only works
#: USDf and USDF
STABLECOIN_LIKE = set(
    [
        "ALUSD",
        "AUDT",
        "AUSD",
        "BAC",
        "BDO",
        "BEAN",
        "BOB",
        "BOLD",
        "BUSD",
        "BYUSD",
        "CADC",
        "CEUR",
        "CJPY",
        "CNHT",
        "CRVUSD",
        "CUSD",
        "csUSD",
        "DAI",
        "DJED",
        "DOLADUSD",
        "EOSDT",
        "EURA",
        "EURCV",
        "EUROC",
        "EUROe",
        "EURS",
        "EURT",
        "EURe",
        "EUSD",
        "FDUSD",
        "FEI",
        "FLEXUSD",
        "feUSD",
        "FUSD",
        "FXD",
        "FXUSD",
        "GBPT",
        "GHO",
        "GHST",
        "GUSD",
        "GYD",
        "GYEN",
        "HAI",
        "HUSD",
        "IRON",
        "JCHF",
        "JPYC",
        "KDAI",
        "LISUSD",
        "LUSD",
        "MIM",
        "MIMATIC",
        "MKUSD",
        "MUSD",
        "ONC",
        "OUSD",
        "PAR",
        "PAXG",
        "PYUSD",
        "RAI",
        "RLUSD",
        "RUSD",
        "SAI",
        "SDAI",
        "SEUR",
        "SFRAX",
        "SILK",
        "STUSD",
        "SUSD",
        "TCNH",
        "TOR",
        "TRYB",
        "TUSD",
        "USC",
        "USD+",
        "USD0",
        "USD1",
        "USD8",
        "USDA",
        "USDB",
        "USDC",
        "USDC.e",
        "USDCV",
        "USDD",
        "USDE",
        "USDe",  # Mantle
        "USDF",
        "USDH",
        "USDHLUSDG",
        "USDM",
        "USDN",
        "USDO",
        "USDP",
        "USDR",
        "USDS",
        "USDT",
        "USDT.e",
        "USDT0",
        "USD₮",
        "USDV",
        "USDX",
        "USDXL",
        "USDai",
        "USDbC",
        "USDe",
        "USDf",
        "USDs",
        "USDt",
        "USD₮0USDU",
        "USH",
        "USK",
        "USR",
        "UST",
        "USTC",
        "USDtb",
        "USAT",
        "FIDD",
        "USXAU",
        "UTY",
        "UUSD",
        "VAI",
        "VEUR",
        "VST",
        "VUSD",
        "WXDAI",
        "XAUT",
        "XDAI",
        "XIDR",
        "XSGD",
        "XSTUSD",
        "XUSD",
        "YUSD",
        "ZCHF",
        "ZSD",
        "ZUSD",
        "avUSD",
        "bvUSD",
        "crvUSD",
        "dUSD",
        "deUSD",
        "frxUSD",
        "ftUSD",
        "gmUSD",
        "iUSD",
        "jEUR",
        "kUSD",
        "lvlUSD",
        "mUSD",
        "meUSDT",
        "msUSD",
        "plUSD",
        "reUSD",
        "sUSDC",
        "satUSD",
        "scUSD",
        "sosUSDT",
        "vbUSDC",
        "vbUSDT",
        "wM",
        "xUSD",
        "MTUSD",
        "ysUSDC",
        "mtUSDC",
        "mtUSDT",
    ]
)


#: Stablecoins which can be used as collateral, but which also have built-in yield bearing function
#: with rebasing.
YIELD_BEARING_STABLES = {"sfrxUSD", "sUSDe", "sUSDai", "sBOLD", "sAUSD", "ynUSDx"}

#: Stablecoins plus their interest wrapped counterparts on Compound and Aave.
#: Also contains other derivates.
WRAPPED_STABLECOIN_LIKE = {"cUSDC", "cUSDT", "sUSD", "aDAI", "cDAI", "tfUSDC", "alUSD", "agEUR", "gmdUSDC", "gDAI", "blUSD"}

#: All stablecoin likes - both interested bearing and non interest bearing.
ALL_STABLECOIN_LIKE = STABLECOIN_LIKE | WRAPPED_STABLECOIN_LIKE | YIELD_BEARING_STABLES


class StablecoinInfo(TypedDict):
    """Metadata for a single stablecoin-like token project."""

    #: Full human-readable name of the token
    name: str
    #: Homepage URL for the project (empty string if unknown)
    homepage: str
    #: Three-sentence description of the token
    description: str
    #: CoinGecko listing URL (empty string if not listed)
    coingecko: str
    #: DefiLlama listing URL (empty string if not listed)
    defillama: str
    #: Twitter/X account URL (empty string if not found)
    twitter: str


#: Full name and homepage for all coins in :py:data:`ALL_STABLECOIN_LIKE`.
#:
#: Each symbol maps to a list of :py:class:`StablecoinInfo` entries.
#: Where a symbol maps to multiple known projects (fuzzy matches),
#: multiple entries are listed. The resulting structure is
#: JSON-compatible for easy serialisation.
STABLECOIN_METADATA: dict[str, list[StablecoinInfo]] = {
    # STABLECOIN_LIKE members
    "ALUSD": [{"name": "Alchemix USD", "homepage": "https://alchemix.fi/", "description": "Alchemix USD (alUSD) is a synthetic stablecoin issued by the Alchemix protocol on Ethereum. It is backed by yield-bearing assets and allows users to take self-repaying loans against their deposits. The protocol automatically repays debt using the yield generated from deposited collateral.", "coingecko": "https://www.coingecko.com/en/coins/alchemix-usd", "defillama": "https://defillama.com/stablecoin/alchemix-usd", "twitter": "https://x.com/AlchemixFi"}],
    "AUDT": [{"name": "Australian Dollar Token", "homepage": "https://audt.to/", "description": "AUDT is a fully regulated Australian Dollar-backed stablecoin pegged 1:1 to AUD. Each token is backed by Australian dollars held in regulated custody. It enables digital transactions denominated in AUD on blockchain networks.", "coingecko": "https://www.coingecko.com/en/coins/audt", "defillama": "", "twitter": "https://x.com/AUDTofficial"}],
    "AUSD": [
        {"name": "Agora Dollar", "homepage": "https://www.agora.finance/", "description": "Agora Dollar (AUSD) is a digital dollar stablecoin minted 1:1 with USD fiat. It uses institutional-grade custodians, a Big Four auditor, and a top-tier fund manager to safeguard reserves. AUSD is gas-optimised, making it cost-efficient for trading and payments.", "coingecko": "https://www.coingecko.com/en/coins/agora-dollar", "defillama": "https://defillama.com/stablecoin/agora-dollar", "twitter": "https://x.com/AgoraCurrency"},
        {"name": "Acala Dollar", "homepage": "https://acala.network/", "description": "Acala Dollar (aUSD) is the native decentralised stablecoin of the Acala network on Polkadot. It is overcollateralised and multi-collateral-backed, designed for cross-chain DeFi. aUSD can be minted by depositing various crypto assets as collateral.", "coingecko": "https://www.coingecko.com/en/coins/acala-dollar", "defillama": "https://defillama.com/stablecoin/acala-dollar", "twitter": "https://x.com/AcalaNetwork"},
    ],
    "BAC": [{"name": "Basis Cash", "homepage": "https://basis.cash/", "description": "Basis Cash was an algorithmic stablecoin protocol inspired by the original Basis project. It used a seigniorage shares model with three tokens: BAC (stablecoin), BAS (shares), and BAB (bonds). The project is now defunct after failing to maintain its dollar peg.", "coingecko": "https://www.coingecko.com/en/coins/basis-cash", "defillama": "", "twitter": ""}],
    "BDO": [{"name": "bDollar", "homepage": "https://bdollar.fi/", "description": "bDollar (BDO) was an algorithmic stablecoin on Binance Smart Chain inspired by Basis Cash. It used a seigniorage model with boardroom and bond mechanisms to maintain its peg. The project is now defunct.", "coingecko": "https://www.coingecko.com/en/coins/bdollar", "defillama": "", "twitter": ""}],
    "BEAN": [{"name": "Bean", "homepage": "https://bean.money/", "description": "Bean is the USD-pegged stablecoin issued by the Beanstalk protocol on Ethereum. It uses a credit-based model rather than collateral to maintain its peg through protocol-native financial incentives. Beanstalk was exploited in April 2022 but has since relaunched.", "coingecko": "https://www.coingecko.com/en/coins/bean", "defillama": "https://defillama.com/stablecoin/bean", "twitter": "https://x.com/BeanstalkFarms"}],
    "BOB": [{"name": "BOB (zkBob)", "homepage": "https://bob.zkbob.com/", "description": "BOB is a privacy-focused stablecoin created by zkBob, built using zero-knowledge proofs. It enables private transactions on Ethereum and Polygon while maintaining compliance. BOB is backed by DAI and can be minted through the zkBob application.", "coingecko": "https://www.coingecko.com/en/coins/bob", "defillama": "https://defillama.com/stablecoin/bob", "twitter": "https://x.com/zkbob_"}],
    "BOLD": [{"name": "Liquity BOLD", "homepage": "https://www.liquity.org/", "description": "BOLD is the USD-pegged stablecoin issued by Liquity V2, fully decentralised and overcollateralised. It is backed exclusively by WETH, wstETH, and rETH with a minimum collateral ratio of 110%. BOLD is redeemable for $1 worth of underlying collateral and is immutable with no governance.", "coingecko": "https://www.coingecko.com/en/coins/liquity-bold", "defillama": "https://defillama.com/stablecoin/liquity-bold", "twitter": "https://x.com/LiquityProtocol"}],
    "BUSD": [{"name": "Binance USD", "homepage": "https://www.binance.com/", "description": "Binance USD (BUSD) was a USD-pegged stablecoin issued by Paxos in partnership with Binance. It was fully backed by US dollar reserves and regulated by the New York State Department of Financial Services. Paxos ceased minting new BUSD in February 2023.", "coingecko": "https://www.coingecko.com/en/coins/binance-usd", "defillama": "https://defillama.com/stablecoin/binance-usd", "twitter": "https://x.com/binance"}],
    "BYUSD": [{"name": "Bybit USD", "homepage": "https://www.bybit.com/", "description": "BYUSD is a stablecoin associated with the Bybit cryptocurrency exchange. Details about its backing mechanism are limited. It may be related to Bybit's internal settlement or trading systems.", "coingecko": "", "defillama": "", "twitter": "https://x.com/Bybit_Official"}],
    "CADC": [{"name": "Canadian Dollar Coin", "homepage": "https://www.cadcoin.ca/", "description": "Canadian Dollar Coin (CADC) is a Canadian Dollar-backed stablecoin pegged 1:1 to CAD. It is fully collateralised with Canadian dollars held in regulated Canadian financial institutions. CADC enables digital transactions denominated in Canadian dollars on Ethereum.", "coingecko": "https://www.coingecko.com/en/coins/cad-coin", "defillama": "", "twitter": ""}],
    "CEUR": [{"name": "Celo Euro", "homepage": "https://celo.org/", "description": "Celo Euro (cEUR) is a Euro-pegged stablecoin native to the Celo blockchain. It is overcollateralised by a diversified reserve of crypto assets managed by the Celo Reserve. cEUR is designed for mobile-first payments and cross-border transactions.", "coingecko": "https://www.coingecko.com/en/coins/celo-euro", "defillama": "https://defillama.com/stablecoin/celo-euro", "twitter": "https://x.com/Celo"}],
    "CJPY": [{"name": "Convertible JPY Token (Yamato Protocol)", "homepage": "https://yamato.fi/", "description": "CJPY is a Japanese Yen-pegged stablecoin issued by the Yamato Protocol on Ethereum. It is overcollateralised by ETH deposits and designed for the Japanese DeFi ecosystem. Users can mint CJPY by depositing ETH as collateral with a minimum collateral ratio.", "coingecko": "https://www.coingecko.com/en/coins/convertible-jpy-token", "defillama": "", "twitter": "https://x.com/YamatoProtocol"}],
    "CNHT": [{"name": "CNH Tether", "homepage": "https://tether.to/", "description": "CNH Tether (CNHT) is an offshore Chinese Yuan-pegged stablecoin issued by Tether. Each CNHT token is backed 1:1 by CNH reserves held by Tether. It enables digital trading and settlement denominated in offshore Chinese Yuan.", "coingecko": "https://www.coingecko.com/en/coins/cnh-tether", "defillama": "", "twitter": "https://x.com/Tether_to"}],
    "CRVUSD": [{"name": "Curve USD", "homepage": "https://curve.fi/", "description": "crvUSD is the native stablecoin of Curve Finance, a leading decentralised exchange for stablecoin trading. It uses a novel LLAMMA (Lending-Liquidating AMM Algorithm) mechanism for soft liquidations. crvUSD is overcollateralised and can be minted against various crypto assets.", "coingecko": "https://www.coingecko.com/en/coins/crvusd", "defillama": "https://defillama.com/stablecoin/crvusd", "twitter": "https://x.com/CurveFinance"}],
    "CUSD": [{"name": "Celo Dollar", "homepage": "https://celo.org/", "description": "Celo Dollar (cUSD) is a USD-pegged stablecoin native to the Celo blockchain. It is overcollateralised by a diversified reserve of crypto assets managed by the Celo Reserve. cUSD is designed for mobile-first payments and accessible financial services.", "coingecko": "https://www.coingecko.com/en/coins/celo-dollar", "defillama": "https://defillama.com/stablecoin/celo-dollar", "twitter": "https://x.com/Celo"}],
    "csUSD": [{"name": "Unknown", "homepage": "", "description": "", "coingecko": "", "defillama": "", "twitter": ""}],
    "DAI": [{"name": "Dai", "homepage": "https://makerdao.com/", "description": "Dai is a decentralised USD-pegged stablecoin issued by the MakerDAO protocol on Ethereum. It is overcollateralised by various crypto assets deposited into Maker Vaults. Dai is one of the most widely used decentralised stablecoins in DeFi.", "coingecko": "https://www.coingecko.com/en/coins/dai", "defillama": "https://defillama.com/stablecoin/dai", "twitter": "https://x.com/MakerDAO"}],
    "DJED": [{"name": "Djed", "homepage": "https://djed.xyz/", "description": "Djed is an overcollateralised stablecoin protocol on the Cardano blockchain. It uses a reserve coin (SHEN) to maintain its USD peg through an algorithmic mechanism. Djed was developed by IOG (Input Output Global) and deployed by COTI.", "coingecko": "https://www.coingecko.com/en/coins/djed", "defillama": "https://defillama.com/stablecoin/djed", "twitter": "https://x.com/DjedStablecoin"}],
    "DOLADUSD": [{"name": "DOLA (Inverse Finance)", "homepage": "https://www.inverse.finance/", "description": "DOLA is a decentralised stablecoin issued by Inverse Finance on Ethereum. It is backed by various crypto collateral through the FiRM lending market. DOLADUSD appears to be a data artefact or trading pair label combining DOLA and USD.", "coingecko": "https://www.coingecko.com/en/coins/dola-usd", "defillama": "https://defillama.com/stablecoin/dola", "twitter": "https://x.com/InverseFinance"}],
    "EOSDT": [{"name": "EOSDT (Equilibrium)", "homepage": "https://eosdt.com/", "description": "EOSDT was a USD-pegged stablecoin on the EOS blockchain created by Equilibrium. It was overcollateralised by EOS tokens deposited as collateral. The project has largely become inactive.", "coingecko": "https://www.coingecko.com/en/coins/eosdt", "defillama": "", "twitter": "https://x.com/EquilibriumDeFi"}],
    "EURA": [{"name": "EURA (Angle Protocol)", "homepage": "https://www.angle.money/", "description": "EURA (formerly agEUR) is a Euro-pegged stablecoin issued by Angle Protocol. It is backed by a combination of overcollateralised loans and yield-bearing reserves. EURA is available on multiple chains including Ethereum, Polygon, and Arbitrum.", "coingecko": "https://www.coingecko.com/en/coins/ageur", "defillama": "https://defillama.com/stablecoin/ageur", "twitter": "https://x.com/AngleProtocol"}],
    "EURCV": [{"name": "EUR CoinVertible (SG-FORGE)", "homepage": "https://www.sgforge.com/", "description": "EUR CoinVertible (EURCV) is a Euro-denominated security token issued by SG-FORGE, a subsidiary of Societe Generale. It is a regulated digital asset backed by Euro reserves for institutional use. EURCV bridges traditional finance and DeFi.", "coingecko": "https://www.coingecko.com/en/coins/coinvertible", "defillama": "", "twitter": "https://x.com/SG_FORGE"}],
    "EUROC": [{"name": "Euro Coin (Circle)", "homepage": "https://www.circle.com/eurc", "description": "Euro Coin (EUROC, now EURC) is a Euro-backed stablecoin issued by Circle, the company behind USDC. Each EUROC is fully backed by Euro reserves held in regulated financial institutions. It is available on Ethereum, Avalanche, and other major blockchains.", "coingecko": "https://www.coingecko.com/en/coins/euro-coin", "defillama": "https://defillama.com/stablecoin/euro-coin", "twitter": "https://x.com/circle"}],
    "EUROe": [{"name": "EUROe (Membrane Finance)", "homepage": "https://www.euroe.com/", "description": "EUROe is a Euro-backed stablecoin issued by Membrane Finance, a Finnish fintech company. It is fully regulated under EU law and backed 1:1 by Euro reserves. EUROe is available on Ethereum, Polygon, Arbitrum, and other networks.", "coingecko": "https://www.coingecko.com/en/coins/euroe-stablecoin", "defillama": "https://defillama.com/stablecoin/euroe-stablecoin", "twitter": "https://x.com/membrane_fi"}],
    "EURS": [{"name": "STASIS Euro", "homepage": "https://stasis.net/", "description": "STASIS Euro (EURS) is a Euro-backed stablecoin issued by STASIS, a European fintech company. Each EURS is fully backed by Euros held in reserve accounts. EURS is designed for institutional and retail use in the European digital asset ecosystem.", "coingecko": "https://www.coingecko.com/en/coins/stasis-eurs", "defillama": "https://defillama.com/stablecoin/stasis-eurs", "twitter": "https://x.com/staborasisnet"}],
    "EURT": [{"name": "Tether Euro", "homepage": "https://tether.to/", "description": "Tether EURt is a Euro-pegged stablecoin issued by Tether, the company behind USDT. Each EURt is backed by Tether's reserves denominated in Euros. It provides Euro-denominated digital transactions on blockchain networks.", "coingecko": "https://www.coingecko.com/en/coins/tether-eurt", "defillama": "https://defillama.com/stablecoin/tether-eurt", "twitter": "https://x.com/Tether_to"}],
    "EURe": [{"name": "Monerium EUR emoney", "homepage": "https://monerium.com/", "description": "EURe is a Euro-backed electronic money token issued by Monerium, a licensed e-money institution in Europe. Each EURe is fully backed by Euros held in European bank accounts. It is available on Ethereum, Polygon, and Gnosis Chain.", "coingecko": "https://www.coingecko.com/en/coins/monerium-eur-money", "defillama": "https://defillama.com/stablecoin/monerium-eur-money", "twitter": "https://x.com/maboronerium"}],
    "EUSD": [{"name": "eUSD (Lybra Finance)", "homepage": "https://lybra.finance/", "description": "eUSD is a USD-pegged stablecoin issued by Lybra Finance, backed by liquid staking derivatives. It generates yield for holders through the staking rewards of its ETH-based collateral. eUSD is designed to be a stable, interest-bearing asset in DeFi.", "coingecko": "https://www.coingecko.com/en/coins/lybra-finance-eusd", "defillama": "https://defillama.com/stablecoin/eusd", "twitter": "https://x.com/LybraFinance"}],
    "FDUSD": [{"name": "First Digital USD", "homepage": "https://firstdigitallabs.com/", "description": "First Digital USD (FDUSD) is a USD-backed stablecoin issued by First Digital Labs, based in Hong Kong. It is fully backed by US dollars and US Treasury bills held in segregated accounts. FDUSD is widely traded on Binance and other major exchanges.", "coingecko": "https://www.coingecko.com/en/coins/first-digital-usd", "defillama": "https://defillama.com/stablecoin/first-digital-usd", "twitter": "https://x.com/FDLabsHQ"}],
    "FEI": [{"name": "Fei USD", "homepage": "https://fei.money/", "description": "Fei USD was a decentralised stablecoin that used protocol-controlled value (PCV) to maintain its peg. It was governed by the Tribe DAO and backed by ETH and other assets in the protocol's treasury. The project shut down in 2023 and returned funds to holders.", "coingecko": "https://www.coingecko.com/en/coins/fei-usd", "defillama": "https://defillama.com/stablecoin/fei-usd", "twitter": ""}],
    "FLEXUSD": [{"name": "flexUSD (CoinFLEX)", "homepage": "https://flexusd.com/", "description": "flexUSD was a yield-bearing stablecoin issued by CoinFLEX exchange. It earned interest from lending markets while maintaining a USD peg. CoinFLEX filed for restructuring in 2022 and flexUSD is no longer actively maintained.", "coingecko": "https://www.coingecko.com/en/coins/flex-usd", "defillama": "", "twitter": ""}],
    "feUSD": [{"name": "Felix feUSD", "homepage": "https://www.usefelix.xyz/", "description": "feUSD is a stablecoin issued by the Felix protocol for decentralised lending and borrowing. It is designed to be used within the Felix DeFi ecosystem. Details about its collateral backing and mechanism are limited.", "coingecko": "", "defillama": "", "twitter": "https://x.com/usefelix"}],
    "FUSD": [{"name": "Fantom USD", "homepage": "https://fantom.foundation/", "description": "Fantom USD (fUSD) was a stablecoin native to the Fantom blockchain. It could be minted by depositing FTM and other assets as collateral in the Fantom DeFi suite. The stablecoin was part of Fantom's native DeFi ecosystem.", "coingecko": "https://www.coingecko.com/en/coins/fantom-usd", "defillama": "https://defillama.com/stablecoin/fantom-usd", "twitter": "https://x.com/FantomFDN"}],
    "FXD": [{"name": "Fathom Dollar", "homepage": "https://fathom.fi/", "description": "Fathom Dollar (FXD) is a decentralised stablecoin on the XDC Network. It is overcollateralised by XDC and other assets deposited into the Fathom protocol. FXD is designed for enterprise and DeFi use cases on the XDC ecosystem.", "coingecko": "https://www.coingecko.com/en/coins/fathom-dollar", "defillama": "https://defillama.com/stablecoin/fathom-dollar", "twitter": "https://x.com/FathomProtocol"}],
    "FXUSD": [{"name": "f(x) Protocol fxUSD", "homepage": "https://fx.aladdin.club/", "description": "fxUSD is a stablecoin issued by the f(x) Protocol built on Aladdin DAO. It uses a leveraged-yield-bearing mechanism to maintain its peg. fxUSD is backed by liquid staking derivatives with built-in leverage and yield.", "coingecko": "https://www.coingecko.com/en/coins/f-x-protocol-fxusd", "defillama": "", "twitter": "https://x.com/protocol_fx"}],
    "GBPT": [{"name": "poundtoken", "homepage": "https://poundtoken.io/", "description": "Poundtoken (GBPT) is a British Pound-backed stablecoin pegged 1:1 to GBP. It is fully backed by Sterling reserves held in UK bank accounts. GBPT enables digital transactions denominated in British Pounds on Ethereum.", "coingecko": "https://www.coingecko.com/en/coins/poundtoken", "defillama": "", "twitter": "https://x.com/poundtoken"}],
    "GHO": [{"name": "GHO (Aave)", "homepage": "https://aave.com/", "description": "GHO is a decentralised multi-collateral stablecoin native to the Aave Protocol. It is overcollateralised by assets deposited in Aave V3 markets. GHO introduces facilitators that can trustlessly mint and burn the stablecoin within approved parameters.", "coingecko": "https://www.coingecko.com/en/coins/gho", "defillama": "https://defillama.com/stablecoin/gho", "twitter": "https://x.com/aave"}],
    "GHST": [{"name": "Aavegotchi GHST", "homepage": "https://aavegotchi.com/", "description": "GHST is the governance and utility token of Aavegotchi, a DeFi-staked NFT gaming platform built on Aave. It is not a stablecoin but is included in the stablecoin-like set for tracking purposes. Aavegotchi combines DeFi yield farming with NFT gaming mechanics.", "coingecko": "https://www.coingecko.com/en/coins/aavegotchi", "defillama": "", "twitter": "https://x.com/aavegotchi"}],
    "GUSD": [{"name": "Gemini Dollar", "homepage": "https://gemini.com/dollar", "description": "Gemini Dollar (GUSD) is a USD-backed stablecoin issued by the Gemini exchange. It is fully backed by US dollars held at a State Street Bank and regulated by the New York State Department of Financial Services. GUSD was one of the first regulated stablecoins in the US.", "coingecko": "https://www.coingecko.com/en/coins/gemini-dollar", "defillama": "https://defillama.com/stablecoin/gemini-dollar", "twitter": "https://x.com/Gemini"}],
    "GYD": [{"name": "Gyroscope Dollar", "homepage": "https://www.gyro.finance/", "description": "Gyroscope Dollar (GYD) is a decentralised stablecoin designed with an all-weather reserve structure. It uses concentrated liquidity pools and a novel redemption mechanism to maintain its peg. GYD aims to be resilient against black swan events through diversified backing.", "coingecko": "https://www.coingecko.com/en/coins/gyroscope-gyd", "defillama": "", "twitter": "https://x.com/GyroscopeFi"}],
    "GYEN": [{"name": "GMO JPY (GMO Trust)", "homepage": "https://www.gmo-trust.com/", "description": "GYEN is a Japanese Yen-backed stablecoin issued by GMO-Z.com Trust Company. It is fully backed by JPY reserves and regulated in the United States. GYEN enables digital transactions denominated in Japanese Yen on Ethereum.", "coingecko": "https://www.coingecko.com/en/coins/gyen", "defillama": "", "twitter": "https://x.com/gabormo_trust"}],
    "HAI": [{"name": "HAI (Let's Get HAI)", "homepage": "https://www.letsgethai.com/", "description": "HAI is a multi-collateral stablecoin built on Optimism, forked from the RAI model. It uses a redemption rate mechanism rather than a hard peg to maintain stability. HAI is governed by the Let's Get HAI community.", "coingecko": "https://www.coingecko.com/en/coins/hai", "defillama": "https://defillama.com/stablecoin/hai", "twitter": "https://x.com/laboretsgethai"}],
    "HUSD": [{"name": "HUSD (Stable Universal)", "homepage": "https://www.stableuniversal.com/", "description": "HUSD was a USD-backed stablecoin issued by Stable Universal and associated with the Huobi exchange. It was fully backed by US dollar reserves. HUSD has been deprecated and is no longer actively maintained.", "coingecko": "https://www.coingecko.com/en/coins/husd", "defillama": "https://defillama.com/stablecoin/husd", "twitter": ""}],
    "IRON": [{"name": "Iron (Iron Finance)", "homepage": "https://iron.finance/", "description": "Iron was a partially algorithmic stablecoin by Iron Finance on Polygon. It suffered a bank-run-style collapse in June 2021 when its TITAN collateral token lost nearly all value. The project is now defunct and serves as a cautionary example of algorithmic stablecoin design.", "coingecko": "https://www.coingecko.com/en/coins/iron-finance", "defillama": "", "twitter": ""}],
    "JCHF": [{"name": "Jarvis Synthetic Swiss Franc", "homepage": "https://www.jarvis.network/", "description": "jCHF is a synthetic Swiss Franc stablecoin issued by Jarvis Network. It is overcollateralised by USDC and uses Chainlink oracles for price feeds. jCHF enables on-chain exposure to CHF without needing a Swiss bank account.", "coingecko": "https://www.coingecko.com/en/coins/jarvis-synthetic-swiss-franc", "defillama": "", "twitter": "https://x.com/Jarvis_Network"}],
    "JPYC": [{"name": "JPY Coin", "homepage": "https://jpyc.jp/", "description": "JPYC is a Japanese Yen-pegged stablecoin issued by JPYC Inc. It is designed for payments and remittances within the Japanese crypto ecosystem. JPYC is available on Ethereum, Polygon, and other networks.", "coingecko": "https://www.coingecko.com/en/coins/jpyc", "defillama": "", "twitter": "https://x.com/jaborpyc"}],
    "KDAI": [{"name": "Klaytn DAI", "homepage": "https://klaytn.foundation/", "description": "KDAI is a version of Dai bridged to the Klaytn blockchain. It maintains a 1:1 peg with DAI on Ethereum through a cross-chain bridge. KDAI enables DAI-denominated transactions within the Klaytn ecosystem.", "coingecko": "", "defillama": "", "twitter": "https://x.com/klaboraytn_official"}],
    "LISUSD": [{"name": "lisUSD (Lista DAO)", "homepage": "https://lista.org/", "description": "lisUSD is a decentralised stablecoin issued by Lista DAO on BNB Chain. It is overcollateralised by liquid staking tokens and other crypto assets. lisUSD is designed for borrowing and DeFi activities within the Lista ecosystem.", "coingecko": "https://www.coingecko.com/en/coins/lista-usd", "defillama": "https://defillama.com/stablecoin/lista-usd", "twitter": "https://x.com/lista_dao"}],
    "LUSD": [{"name": "Liquity USD", "homepage": "https://www.liquity.org/", "description": "Liquity USD (LUSD) is a decentralised stablecoin issued by the Liquity Protocol on Ethereum. It is overcollateralised by ETH with a minimum collateral ratio of 110% and is fully redeemable for ETH. LUSD is governance-free and immutable, with no admin keys.", "coingecko": "https://www.coingecko.com/en/coins/liquity-usd", "defillama": "https://defillama.com/stablecoin/liquity-usd", "twitter": "https://x.com/LiquityProtocol"}],
    "MIM": [{"name": "Magic Internet Money (Abracadabra)", "homepage": "https://abracadabra.money/", "description": "Magic Internet Money (MIM) is a USD-pegged stablecoin issued by Abracadabra.money. It is minted by depositing interest-bearing tokens (like yvTokens) as collateral. MIM is available on multiple chains and widely used in DeFi lending.", "coingecko": "https://www.coingecko.com/en/coins/magic-internet-money", "defillama": "https://defillama.com/stablecoin/magic-internet-money", "twitter": "https://x.com/MIM_Spell"}],
    "MIMATIC": [{"name": "MAI (QiDAO)", "homepage": "https://www.mai.finance/", "description": "MAI (MIMATIC) is a decentralised stablecoin issued by QiDAO on multiple chains. It is overcollateralised by various crypto assets with interest-free borrowing. MAI is one of the most widely deployed multi-chain stablecoins.", "coingecko": "https://www.coingecko.com/en/coins/mai", "defillama": "https://defillama.com/stablecoin/mai", "twitter": "https://x.com/QiDaoProtocol"}],
    "MKUSD": [{"name": "Prisma mkUSD", "homepage": "https://prismafinance.com/", "description": "mkUSD is a decentralised stablecoin issued by Prisma Finance on Ethereum. It is backed by liquid staking tokens (wstETH, rETH, cbETH, sfrxETH) as collateral. mkUSD allows users to earn yield while borrowing against their staked ETH.", "coingecko": "https://www.coingecko.com/en/coins/prisma-mkusd", "defillama": "https://defillama.com/stablecoin/prisma-mkusd", "twitter": "https://x.com/PrismaFi"}],
    "MUSD": [{"name": "mStable USD", "homepage": "https://www.mstable.com/", "description": "mStable USD (mUSD) was a meta-stablecoin that combined multiple USD stablecoins into a single token. It aggregated USDC, DAI, and USDT to reduce single-stablecoin risk. The mStable protocol has been deprecated.", "coingecko": "https://www.coingecko.com/en/coins/musd", "defillama": "https://defillama.com/stablecoin/musd", "twitter": "https://x.com/maborstable"}],
    "ONC": [{"name": "One Cash", "homepage": "", "description": "One Cash (ONC) was an algorithmic stablecoin inspired by Basis Cash. It used a seigniorage model to maintain its dollar peg. The project is now defunct.", "coingecko": "https://www.coingecko.com/en/coins/one-cash", "defillama": "", "twitter": ""}],
    "OUSD": [{"name": "Origin Dollar", "homepage": "https://ousd.com/", "description": "Origin Dollar (OUSD) is a yield-bearing stablecoin issued by Origin Protocol on Ethereum. It automatically earns yield from DeFi strategies while sitting in holders' wallets with no staking required. OUSD is backed by USDC, DAI, and USDT.", "coingecko": "https://www.coingecko.com/en/coins/origin-dollar", "defillama": "https://defillama.com/stablecoin/origin-dollar", "twitter": "https://x.com/OriginProtocol"}],
    "PAR": [{"name": "Parallel (MIMO Protocol)", "homepage": "https://par.mimo.capital/", "description": "PAR is a Euro-pegged stablecoin issued by MIMO Protocol (Parallel). It is overcollateralised by crypto assets deposited in MIMO vaults. PAR enables Euro-denominated DeFi activities on Ethereum and Polygon.", "coingecko": "https://www.coingecko.com/en/coins/par-stablecoin", "defillama": "", "twitter": "https://x.com/mimodefi"}],
    "PAXG": [{"name": "Pax Gold", "homepage": "https://paxos.com/paxgold/", "description": "Pax Gold (PAXG) is a gold-backed token issued by Paxos, where each token represents one fine troy ounce of gold. The gold is stored in LBMA-accredited London vaults and is regulated by the New York State Department of Financial Services. PAXG enables fractional ownership of physical gold on blockchain.", "coingecko": "https://www.coingecko.com/en/coins/pax-gold", "defillama": "", "twitter": "https://x.com/PaxosGlobal"}],
    "PYUSD": [{"name": "PayPal USD", "homepage": "https://www.paypal.com/pyusd", "description": "PayPal USD (PYUSD) is a USD-backed stablecoin issued by PayPal in partnership with Paxos Trust Company. It is fully backed by US dollar deposits, US Treasuries, and similar cash equivalents. PYUSD is available on Ethereum and Solana.", "coingecko": "https://www.coingecko.com/en/coins/paypal-usd", "defillama": "https://defillama.com/stablecoin/paypal-usd", "twitter": "https://x.com/PayPal"}],
    "RAI": [{"name": "Rai (Reflexer)", "homepage": "https://reflexer.finance/", "description": "RAI is a non-pegged stablecoin issued by Reflexer Labs on Ethereum. It uses a redemption rate mechanism to dampen price volatility rather than targeting a fixed dollar peg. RAI is backed solely by ETH and is governance-minimised.", "coingecko": "https://www.coingecko.com/en/coins/rai", "defillama": "https://defillama.com/stablecoin/rai", "twitter": "https://x.com/reflexaborerfinance"}],
    "RLUSD": [{"name": "Ripple USD", "homepage": "https://ripple.com/solutions/stablecoin/", "description": "Ripple USD (RLUSD) is a USD-backed stablecoin issued by Ripple. It is fully backed by US dollar deposits, US government bonds, and cash equivalents. RLUSD is available on Ethereum and the XRP Ledger.", "coingecko": "https://www.coingecko.com/en/coins/ripple-usd", "defillama": "https://defillama.com/stablecoin/ripple-usd", "twitter": "https://x.com/Ripple"}],
    "RUSD": [
        {"name": "Reservoir rUSD", "homepage": "https://www.reservoir.xyz/", "description": "Reservoir rUSD is a stablecoin associated with the Reservoir protocol. It is designed for DeFi lending and liquidity provision. Details about its specific mechanism are limited.", "coingecko": "", "defillama": "", "twitter": ""},
        {"name": "f(x) rUSD", "homepage": "https://fx.aladdin.club/", "description": "f(x) rUSD is a stablecoin issued by the f(x) Protocol built on Aladdin DAO. It uses a leveraged-yield mechanism similar to fxUSD. rUSD is backed by liquid staking derivatives.", "coingecko": "", "defillama": "", "twitter": "https://x.com/protocol_fx"},
    ],
    "SAI": [{"name": "Single Collateral Dai (MakerDAO legacy)", "homepage": "https://makerdao.com/", "description": "SAI (Single Collateral Dai) was the original Dai stablecoin backed solely by ETH. It was replaced by Multi-Collateral Dai (DAI) in November 2019. SAI is now deprecated and holders were encouraged to migrate to DAI.", "coingecko": "https://www.coingecko.com/en/coins/sai", "defillama": "", "twitter": "https://x.com/MakerDAO"}],
    "SDAI": [{"name": "Savings DAI (Sky/MakerDAO)", "homepage": "https://sky.money/", "description": "Savings DAI (sDAI) is a yield-bearing token representing DAI deposited in the Dai Savings Rate (DSR) module. It automatically accrues interest from MakerDAO's stability fees. sDAI is part of the Sky (formerly MakerDAO) ecosystem.", "coingecko": "https://www.coingecko.com/en/coins/savings-dai", "defillama": "https://defillama.com/stablecoin/savings-dai", "twitter": "https://x.com/SkyEcosystem"}],
    "SEUR": [{"name": "Synthetix EUR", "homepage": "https://synthetix.io/", "description": "sEUR is a synthetic Euro token issued by the Synthetix protocol. It tracks the price of the Euro through Chainlink oracle price feeds. sEUR is backed by SNX tokens staked in the Synthetix system.", "coingecko": "https://www.coingecko.com/en/coins/seur", "defillama": "", "twitter": "https://x.com/synthetix_io"}],
    "SFRAX": [{"name": "Staked FRAX", "homepage": "https://frax.finance/", "description": "Staked FRAX (sFRAX) is a yield-bearing version of FRAX that earns interest from T-bill and repo agreements. It represents FRAX deposited in the Frax staking vault. sFRAX is part of the Frax Finance ecosystem.", "coingecko": "https://www.coingecko.com/en/coins/staked-frax", "defillama": "https://defillama.com/stablecoin/staked-frax", "twitter": "https://x.com/fraboraxfinance"}],
    "SILK": [{"name": "Silk (Shade Protocol)", "homepage": "https://shadeprotocol.io/", "description": "Silk is a privacy-preserving stablecoin on Secret Network issued by Shade Protocol. It is pegged to a basket of currencies and commodities rather than a single fiat currency. Silk leverages Secret Network's encryption for transaction privacy.", "coingecko": "https://www.coingecko.com/en/coins/silk", "defillama": "", "twitter": "https://x.com/Shade_Protocol"}],
    "STUSD": [{"name": "stUSD (Angle Protocol)", "homepage": "https://www.angle.money/", "description": "stUSD is a yield-bearing USD stablecoin from Angle Protocol. It earns yield from T-bills and other real-world assets through the Angle Savings mechanism. stUSD is part of the Angle Protocol ecosystem alongside USDA and EURA.", "coingecko": "https://www.coingecko.com/en/coins/staked-usda", "defillama": "", "twitter": "https://x.com/AngleProtocol"}],
    "SUSD": [{"name": "Synthetix sUSD", "homepage": "https://synthetix.io/", "description": "sUSD is the native stablecoin of the Synthetix protocol, pegged to the US Dollar. It is backed by SNX tokens staked by participants in the Synthetix system. sUSD is used as the base trading asset for Synthetix's synthetic assets.", "coingecko": "https://www.coingecko.com/en/coins/susd", "defillama": "https://defillama.com/stablecoin/susd", "twitter": "https://x.com/synthetix_io"}],
    "TCNH": [{"name": "TrueUSD CNH", "homepage": "https://trueusd.com/", "description": "TCNH is an offshore Chinese Yuan-pegged stablecoin associated with the TrueUSD ecosystem. It is pegged 1:1 to the CNH (offshore Chinese Yuan). TCNH enables digital transactions in CNH on blockchain networks.", "coingecko": "https://www.coingecko.com/en/coins/truecnh", "defillama": "", "twitter": "https://x.com/TrueUSD"}],
    "TOR": [{"name": "TOR (Hector Finance)", "homepage": "https://hector.network/", "description": "TOR was a stablecoin issued by Hector Finance on Fantom. It was designed for use within the Hector ecosystem for lending and staking. The Hector project has been wound down.", "coingecko": "https://www.coingecko.com/en/coins/tor", "defillama": "", "twitter": ""}],
    "TRYB": [{"name": "BiLira Turkish Lira", "homepage": "https://www.bilira.co/", "description": "BiLira (TRYB) is a Turkish Lira-backed stablecoin pegged 1:1 to TRY. It is fully collateralised with Turkish Lira reserves held in regulated Turkish banks. TRYB enables digital transactions denominated in Turkish Lira.", "coingecko": "https://www.coingecko.com/en/coins/bilira", "defillama": "", "twitter": "https://x.com/AboriBiLira"}],
    "TUSD": [{"name": "TrueUSD", "homepage": "https://trueusd.com/", "description": "TrueUSD (TUSD) is a USD-backed stablecoin with real-time on-chain attestations of its reserves. It was one of the early regulated stablecoins with third-party attestation. TUSD has faced depegging issues and reduced adoption since 2023.", "coingecko": "https://www.coingecko.com/en/coins/true-usd", "defillama": "https://defillama.com/stablecoin/true-usd", "twitter": "https://x.com/TrueUSD"}],
    "USC": [{"name": "USC (Orby Network)", "homepage": "https://orby.network/", "description": "USC is a stablecoin issued by Orby Network. It is designed for decentralised finance applications. Details about its specific mechanism and backing are limited.", "coingecko": "", "defillama": "", "twitter": "https://x.com/OrbyNetwork"}],
    "USD+": [{"name": "USD+ (Overnight Finance)", "homepage": "https://overnight.fi/", "description": "USD+ is a yield-bearing stablecoin by Overnight Finance that automatically generates yield from conservative DeFi strategies. It is collateralised by USDC and maintains its peg through daily rebasing. USD+ is available on multiple chains including Ethereum, Arbitrum, and Base.", "coingecko": "https://www.coingecko.com/en/coins/usd", "defillama": "https://defillama.com/stablecoin/usd+", "twitter": "https://x.com/overnight_fi"}],
    "USD0": [{"name": "Usual USD", "homepage": "https://usual.money/", "description": "USD0 is a USD-backed stablecoin issued by Usual, backed by real-world assets including US Treasury Bills. It is designed to redistribute value to users through the USUAL governance token. USD0 can be staked as USD0++ for additional yield.", "coingecko": "https://www.coingecko.com/en/coins/usual-usd", "defillama": "https://defillama.com/stablecoin/usual-usd", "twitter": "https://x.com/usaborualprotocol"}],
    "USD1": [{"name": "World Liberty Financial USD", "homepage": "https://worldlibertyfinancial.com/", "description": "USD1 is a USD-backed stablecoin issued by World Liberty Financial (WLFI). It is fully backed by US Treasuries, US dollar deposits, and other cash equivalents. USD1 is associated with the Trump-linked DeFi project.", "coingecko": "https://www.coingecko.com/en/coins/usd1-wlfi", "defillama": "https://defillama.com/stablecoin/usd1", "twitter": "https://x.com/worldlaboribertyfi"}],
    "USD8": [{"name": "Unknown", "homepage": "", "description": "", "coingecko": "", "defillama": "", "twitter": ""}],
    "USDA": [{"name": "Angle USDA", "homepage": "https://www.angle.money/", "description": "USDA is a USD-pegged stablecoin issued by Angle Protocol. It is backed by a diversified set of reserves including US Treasuries and other yield-generating assets. USDA is part of the Angle Protocol ecosystem alongside EURA.", "coingecko": "https://www.coingecko.com/en/coins/angle-usd", "defillama": "https://defillama.com/stablecoin/angle-usd", "twitter": "https://x.com/AngleProtocol"}],
    "USDB": [{"name": "USDB (Blast)", "homepage": "https://blast.io/", "description": "USDB is the native rebasing stablecoin of the Blast L2 network. It automatically earns yield from T-bill rates through MakerDAO's DSR. USDB is the default stablecoin within the Blast ecosystem.", "coingecko": "https://www.coingecko.com/en/coins/usdb", "defillama": "https://defillama.com/stablecoin/usdb", "twitter": "https://x.com/Blast_L2"}],
    "USDC": [{"name": "USD Coin (Circle)", "homepage": "https://www.circle.com/usdc", "description": "USD Coin (USDC) is a fully-reserved USD stablecoin issued by Circle. It is backed by cash and short-dated US government obligations held at regulated financial institutions. USDC is one of the most widely used stablecoins across DeFi and CeFi.", "coingecko": "https://www.coingecko.com/en/coins/usd-coin", "defillama": "https://defillama.com/stablecoin/usd-coin", "twitter": "https://x.com/circle"}],
    "USDC.e": [{"name": "Bridged USDC", "homepage": "https://www.circle.com/usdc", "description": "USDC.e is a bridged version of USDC on various L2 and alt-L1 chains. It represents USDC locked on Ethereum and bridged to another network via a canonical bridge. USDC.e is being phased out as Circle deploys native USDC on more chains.", "coingecko": "https://www.coingecko.com/en/coins/bridged-usdc", "defillama": "", "twitter": "https://x.com/circle"}],
    "USDCV": [{"name": "USD CoinVertible (SG-FORGE)", "homepage": "https://www.sgforge.com/", "description": "USD CoinVertible (USDCV) is a USD-denominated security token issued by SG-FORGE, a subsidiary of Societe Generale. It is a regulated digital asset backed by USD reserves for institutional use. USDCV is designed to bridge traditional finance and DeFi.", "coingecko": "", "defillama": "", "twitter": "https://x.com/SG_FORGE"}],
    "USDD": [{"name": "USDD (TRON)", "homepage": "https://usdd.io/", "description": "USDD is a decentralised stablecoin on the TRON network backed by the TRON DAO Reserve. It is overcollateralised by TRX, BTC, USDT, and other assets. USDD uses a hybrid algorithmic and reserve-backed mechanism.", "coingecko": "https://www.coingecko.com/en/coins/usdd", "defillama": "https://defillama.com/stablecoin/usdd", "twitter": "https://x.com/usaboreddio"}],
    "USDE": [{"name": "Ethena USDe", "homepage": "https://ethena.fi/", "description": "USDe is a synthetic dollar protocol by Ethena Labs that uses delta-hedging staked ETH positions. It generates yield through staking rewards and futures funding rates. USDe is one of the fastest-growing stablecoins by market capitalisation.", "coingecko": "https://www.coingecko.com/en/coins/ethena-usde", "defillama": "https://defillama.com/stablecoin/ethena-usde", "twitter": "https://x.com/ethena_labs"}],
    "USDe": [{"name": "Ethena USDe", "homepage": "https://ethena.fi/", "description": "USDe is a synthetic dollar protocol by Ethena Labs that uses delta-hedging staked ETH positions. It generates yield through staking rewards and futures funding rates. USDe is one of the fastest-growing stablecoins by market capitalisation.", "coingecko": "https://www.coingecko.com/en/coins/ethena-usde", "defillama": "https://defillama.com/stablecoin/ethena-usde", "twitter": "https://x.com/ethena_labs"}],
    "USDF": [
        {"name": "Falcon USD", "homepage": "https://falcon.finance/", "description": "Falcon USD is a stablecoin issued by Falcon Finance, a protocol focused on delta-neutral strategies. It aims to provide stable value through hedged positions. Falcon Finance is built for institutional and DeFi use cases.", "coingecko": "https://www.coingecko.com/en/coins/falcon-finance", "defillama": "https://defillama.com/stablecoin/falcon-finance", "twitter": "https://x.com/FalcaboronFinance"},
        {"name": "USDF Consortium", "homepage": "https://usdfconsortium.com/", "description": "The USDF Consortium is a network of FDIC-insured banks creating a bank-minted stablecoin. USDF is designed for interbank settlement and compliant digital transactions. The consortium includes multiple US banking institutions.", "coingecko": "", "defillama": "", "twitter": "https://x.com/USaborDFConsortium"},
    ],
    "USDH": [{"name": "USDH (Hyperliquid / Native Markets)", "homepage": "https://hubbleprotocol.io/", "description": "USDH was a stablecoin associated with Hubble Protocol on Solana. It was overcollateralised by various crypto assets including SOL, BTC, and ETH. The project has been deprecated.", "coingecko": "https://www.coingecko.com/en/coins/hubble", "defillama": "", "twitter": ""}],
    "USDHLUSDG": [{"name": "Unknown", "homepage": "", "description": "", "coingecko": "", "defillama": "", "twitter": ""}],
    "USDM": [{"name": "Mountain Protocol USD", "homepage": "https://mountainprotocol.com/", "description": "USDM is a yield-bearing stablecoin by Mountain Protocol, backed by US Treasury Bills. It is a regulated token that accrues yield daily through rebasing. USDM is designed for both DeFi and institutional use.", "coingecko": "https://www.coingecko.com/en/coins/mountain-protocol-usdm", "defillama": "https://defillama.com/stablecoin/mountain-protocol-usdm", "twitter": "https://x.com/MountainPrtcl"}],
    "USDN": [
        {"name": "Noble Dollar", "homepage": "https://noble.xyz/", "description": "Noble Dollar (USDN) is a stablecoin native to the Noble blockchain in the Cosmos ecosystem. It is backed by US dollar reserves and designed for cross-chain IBC transfers. USDN is used across Cosmos-based chains.", "coingecko": "https://www.coingecko.com/en/coins/noble-dollar", "defillama": "https://defillama.com/stablecoin/noble-dollar", "twitter": "https://x.com/noble_xyz"},
        {"name": "Neutrino USD", "homepage": "https://neutrino.at/", "description": "Neutrino USD (USDN) was an algorithmic stablecoin on the Waves blockchain backed by WAVES. It depegged significantly in 2022 and was rebranded to XTN. The protocol is now largely inactive.", "coingecko": "https://www.coingecko.com/en/coins/neutrino", "defillama": "https://defillama.com/stablecoin/neutrino", "twitter": ""},
    ],
    "USDO": [{"name": "OpenDollar (OpenEden)", "homepage": "https://openeden.com/", "description": "USDO is a stablecoin associated with OpenEden, a protocol providing tokenised access to US Treasuries. It is backed by short-term US government securities. USDO is designed for institutional DeFi participants.", "coingecko": "", "defillama": "", "twitter": "https://x.com/OpenEdenLabs"}],
    "USDP": [{"name": "Pax Dollar (Paxos)", "homepage": "https://paxos.com/", "description": "Pax Dollar (USDP) is a USD-backed stablecoin issued by Paxos Trust Company. It is fully backed by cash and cash equivalents and regulated by the New York State Department of Financial Services. USDP was formerly known as Paxos Standard (PAX).", "coingecko": "https://www.coingecko.com/en/coins/pax-dollar", "defillama": "https://defillama.com/stablecoin/pax-dollar", "twitter": "https://x.com/PaxosGlobal"}],
    "USDR": [{"name": "Real USD (Tangible / re.al)", "homepage": "https://www.re.al/", "description": "Real USD (USDR) was a stablecoin by Tangible backed by tokenised real estate and DAI. It lost its peg in October 2023 due to liquidity issues. The project rebranded to re.al and USDR is being wound down.", "coingecko": "https://www.coingecko.com/en/coins/real-usd", "defillama": "https://defillama.com/stablecoin/real-usd", "twitter": "https://x.com/re_aboralRWA"}],
    "USDS": [{"name": "USDS (Sky)", "homepage": "https://sky.money/", "description": "USDS (Sky Dollar) is the upgraded version of DAI, part of the Sky (formerly MakerDAO) ecosystem rebrand. It maintains a 1:1 USD peg backed by diversified crypto and real-world assets. USDS can be converted from DAI through the Sky protocol.", "coingecko": "https://www.coingecko.com/en/coins/usds", "defillama": "https://defillama.com/stablecoin/usds", "twitter": "https://x.com/SkyEcosystem"}],
    "USDT": [{"name": "Tether USD", "homepage": "https://tether.to/", "description": "Tether (USDT) is the largest stablecoin by market capitalisation, pegged 1:1 to the US Dollar. It is issued by Tether Limited and backed by reserves including cash, cash equivalents, and other assets. USDT is the most widely traded cryptocurrency by volume.", "coingecko": "https://www.coingecko.com/en/coins/tether", "defillama": "https://defillama.com/stablecoin/tether", "twitter": "https://x.com/Tether_to"}],
    "USDT.e": [{"name": "Bridged USDT", "homepage": "https://tether.to/", "description": "USDT.e is a bridged version of USDT on various L2 and alt-L1 chains. It represents USDT locked on Ethereum and bridged to another network via a canonical bridge. USDT.e is being replaced by native USDT deployments on more chains.", "coingecko": "", "defillama": "", "twitter": "https://x.com/Tether_to"}],
    "USDT0": [{"name": "USDT0 (Tether + LayerZero)", "homepage": "https://usdt0.to/", "description": "USDT0 is an omnichain version of USDT powered by LayerZero's OFT standard. It enables native USDT transfers across multiple blockchains without traditional bridging. USDT0 is backed 1:1 by locked USDT.", "coingecko": "https://www.coingecko.com/en/coins/usdt0", "defillama": "", "twitter": "https://x.com/usdt0_to"}],
    "USD₮": [{"name": "Tether USD", "homepage": "https://tether.to/", "description": "USD\u20ae is the Unicode variant of the USDT ticker symbol used by Tether. It represents the same Tether USD stablecoin. USD\u20ae appears on some blockchain explorers and exchanges as an alternate representation.", "coingecko": "https://www.coingecko.com/en/coins/tether", "defillama": "https://defillama.com/stablecoin/tether", "twitter": "https://x.com/Tether_to"}],
    "USDV": [{"name": "Verified USD", "homepage": "https://usdv.money/", "description": "Verified USD (USDV) is a stablecoin backed by STBT (Short-Term Treasury Bill Token). It is designed for multi-chain deployment with built-in yield from US Treasury exposure. USDV uses LayerZero for cross-chain transfers.", "coingecko": "https://www.coingecko.com/en/coins/verified-usd", "defillama": "https://defillama.com/stablecoin/verified-usd", "twitter": "https://x.com/verifiedaborusd"}],
    "USDX": [
        {"name": "USDX (Stables Labs)", "homepage": "https://usdx.money/", "description": "USDX is a stablecoin by Stables Labs designed as a synthetic dollar. It uses delta-neutral strategies similar to Ethena to maintain its peg. USDX is focused on providing stable value with yield opportunities.", "coingecko": "https://www.coingecko.com/en/coins/usdx-money", "defillama": "https://defillama.com/stablecoin/usdx-money", "twitter": "https://x.com/USDaborx_Money"},
        {"name": "USDX (Kava)", "homepage": "https://www.kava.io/", "description": "USDX was the native stablecoin of the Kava blockchain. It was overcollateralised by multiple crypto assets deposited on the Kava platform. The Kava ecosystem has shifted focus away from USDX.", "coingecko": "https://www.coingecko.com/en/coins/kava-lend", "defillama": "", "twitter": "https://x.com/KABORAVA_CHAIN"},
    ],
    "USDXL": [{"name": "Last USD", "homepage": "", "description": "Last USD (USDXL) is an obscure stablecoin with limited public information. Details about its backing, mechanism, and team are not readily available. It appears to have minimal market presence.", "coingecko": "", "defillama": "", "twitter": ""}],
    "USDai": [{"name": "USDai (Permian Labs / USD.AI)", "homepage": "https://usd.ai/", "description": "USDai is a stablecoin by USD.AI (Permian Labs) that leverages AI for risk management. It is designed to maintain its peg through algorithmic and reserve-backed mechanisms. USDai is focused on providing stable, yield-generating digital dollars.", "coingecko": "https://www.coingecko.com/en/coins/usdai", "defillama": "https://defillama.com/stablecoin/usdai", "twitter": "https://x.com/usd_ai"}],
    "USDbC": [{"name": "USD Base Coin (bridged USDC on Base)", "homepage": "https://www.circle.com/usdc", "description": "USDbC is the bridged version of USDC on the Base network via the Ethereum canonical bridge. It was the primary USDC representation on Base before Circle deployed native USDC. USDbC is being phased out in favour of native USDC on Base.", "coingecko": "https://www.coingecko.com/en/coins/bridged-usd-coin-base", "defillama": "", "twitter": "https://x.com/circle"}],
    "USDf": [{"name": "Falcon USD (Falcon Finance)", "homepage": "https://falcon.finance/", "description": "USDf is a stablecoin issued by Falcon Finance, focused on delta-neutral yield strategies. It provides stable value backed by hedged crypto positions. Falcon Finance is designed for both institutional and DeFi use.", "coingecko": "https://www.coingecko.com/en/coins/falcon-finance", "defillama": "https://defillama.com/stablecoin/falcon-finance", "twitter": "https://x.com/FalconFinance"}],
    "USDs": [{"name": "Sperax USD", "homepage": "https://sperax.io/", "description": "Sperax USD (USDs) is a yield-generating stablecoin on Arbitrum. It auto-compounds yield from DeFi strategies while maintaining a dollar peg. USDs is designed to earn passive income for holders without staking.", "coingecko": "https://www.coingecko.com/en/coins/sperax-usd", "defillama": "https://defillama.com/stablecoin/sperax-usd", "twitter": "https://x.com/SperaxUSD"}],
    "USDt": [{"name": "Tether USD", "homepage": "https://tether.to/", "description": "USDt is an alternate casing of the USDT ticker used on some chains and explorers. It represents the same Tether USD stablecoin, the largest by market capitalisation. USDt appears primarily on Tron and some other networks.", "coingecko": "https://www.coingecko.com/en/coins/tether", "defillama": "https://defillama.com/stablecoin/tether", "twitter": "https://x.com/Tether_to"}],
    "USD₮0USDU": [{"name": "Unknown (likely data artefact combining USD\u20ae0 and USDU)", "homepage": "", "description": "", "coingecko": "", "defillama": "", "twitter": ""}],
    "USH": [{"name": "unshETH", "homepage": "https://unsheth.xyz/", "description": "USH is the governance token of unshETH, a protocol for diversified ETH staking. It is not a stablecoin but is included in the stablecoin-like set for tracking purposes. unshETH provides a basket of liquid staking derivatives.", "coingecko": "https://www.coingecko.com/en/coins/unsheth", "defillama": "", "twitter": "https://x.com/unshETHbor_xyz"}],
    "USK": [{"name": "USK (Kujira)", "homepage": "https://kujira.app/", "description": "USK is a decentralised stablecoin native to the Kujira blockchain in the Cosmos ecosystem. It is overcollateralised by ATOM, wETH, wBTC and other assets. USK is minted through the ORCA liquidation platform.", "coingecko": "https://www.coingecko.com/en/coins/usk", "defillama": "https://defillama.com/stablecoin/usk", "twitter": "https://x.com/TeamKujira"}],
    "USR": [{"name": "Resolv USR", "homepage": "https://resolv.xyz/", "description": "USR is a stablecoin issued by Resolv, a delta-neutral stablecoin protocol. It is backed by ETH and hedged with short perpetual futures positions to maintain its peg. USR generates yield from funding rates and staking rewards.", "coingecko": "https://www.coingecko.com/en/coins/resolv-usr", "defillama": "https://defillama.com/stablecoin/resolv-usr", "twitter": "https://x.com/ResolvLabs"}],
    "UST": [{"name": "TerraUSD", "homepage": "https://terra.money/", "description": "TerraUSD (UST) was an algorithmic stablecoin on the Terra blockchain pegged to USD via LUNA burns and mints. It suffered a catastrophic collapse in May 2022, losing its peg entirely. The UST/LUNA collapse wiped out approximately $40 billion in value.", "coingecko": "https://www.coingecko.com/en/coins/terrausd", "defillama": "https://defillama.com/stablecoin/terrausd", "twitter": ""}],
    "USTC": [{"name": "TerraUSD Classic", "homepage": "https://terra.money/", "description": "TerraUSD Classic (USTC) is the renamed version of UST after the Terra blockchain forked. It trades at a deep discount to $1 following the May 2022 collapse. USTC remains on Terra Classic (LUNC) chain.", "coingecko": "https://www.coingecko.com/en/coins/terrausd", "defillama": "", "twitter": ""}],
    "USDtb": [{"name": "Ethena USDtb", "homepage": "https://usdtb.money/", "description": "USDtb is a stablecoin by Ethena Labs backed primarily by BlackRock's BUIDL tokenised treasury fund. It provides a more conservatively backed alternative to USDe. USDtb is designed for institutional use with traditional asset backing.", "coingecko": "https://www.coingecko.com/en/coins/usdtb", "defillama": "https://defillama.com/stablecoin/usdtb", "twitter": "https://x.com/ethena_labs"}],
    "USAT": [{"name": "USA_t (Tether)", "homepage": "https://tether.to/", "description": "USAT is a token associated with Tether's US-regulated offerings. Details about its specific mechanism are limited. It appears to be part of Tether's broader stablecoin product line.", "coingecko": "", "defillama": "", "twitter": "https://x.com/Tether_to"}],
    "FIDD": [{"name": "Fidelity Digital Dollar", "homepage": "https://www.fidelitydigitalassets.com/", "description": "Fidelity Digital Dollar (FIDD) is reportedly a stablecoin initiative by Fidelity Investments' digital assets division. Details about its launch status and mechanism are limited. Fidelity Digital Assets is one of the largest institutional crypto custodians.", "coingecko": "", "defillama": "", "twitter": ""}],
    "USXAU": [{"name": "Unknown", "homepage": "", "description": "", "coingecko": "", "defillama": "", "twitter": ""}],
    "UTY": [{"name": "Unity (XSY.fi)", "homepage": "https://xsy.fi/", "description": "Unity (UTY) is a token from the XSY.fi protocol. It is designed for use within the XSY ecosystem. Details about its stablecoin mechanism are limited.", "coingecko": "", "defillama": "", "twitter": "https://x.com/xsy_fi"}],
    "UUSD": [{"name": "Utopia USD", "homepage": "https://u.is/", "description": "Utopia USD (UUSD) is a stablecoin from the Utopia ecosystem, a privacy-focused platform. It is designed for anonymous transactions within the Utopia network. UUSD maintains a USD peg within the privacy-preserving Utopia ecosystem.", "coingecko": "", "defillama": "", "twitter": ""}],
    "VAI": [{"name": "Vai (Venus Protocol)", "homepage": "https://venus.io/", "description": "VAI is a decentralised stablecoin issued by the Venus Protocol on BNB Chain. It is minted by depositing crypto assets as collateral in Venus markets. VAI has historically experienced peg instability.", "coingecko": "https://www.coingecko.com/en/coins/vai", "defillama": "https://defillama.com/stablecoin/vai", "twitter": "https://x.com/VenusProtocol"}],
    "VEUR": [{"name": "VNX Euro", "homepage": "https://vnx.li/", "description": "VNX Euro (VEUR) is a Euro-backed stablecoin issued by VNX, a Liechtenstein-regulated fintech. Each VEUR is backed 1:1 by Euro reserves. VEUR enables regulated Euro-denominated digital transactions.", "coingecko": "https://www.coingecko.com/en/coins/vnx-euro", "defillama": "", "twitter": "https://x.com/VNX_fi"}],
    "VST": [{"name": "Vesta Stable", "homepage": "https://vestafinance.xyz/", "description": "Vesta Stable (VST) was a decentralised stablecoin issued by Vesta Finance on Arbitrum. It was overcollateralised by ETH, GLP, and other assets. Vesta Finance has been deprecated.", "coingecko": "https://www.coingecko.com/en/coins/vesta-stable", "defillama": "https://defillama.com/stablecoin/vesta-stable", "twitter": ""}],
    "VUSD": [
        {"name": "Virtual USD", "homepage": "https://vusd.com/", "description": "Virtual USD (VUSD) is a stablecoin with limited public information available. Details about its backing and mechanism are unclear. It appears to have minimal market presence.", "coingecko": "", "defillama": "", "twitter": ""},
        {"name": "Virtue USD", "homepage": "https://virtue.money/", "description": "Virtue USD is a stablecoin from the Virtue protocol. Details about its mechanism and backing are limited. It appears to target DeFi use cases.", "coingecko": "", "defillama": "", "twitter": ""},
    ],
    "WXDAI": [{"name": "Wrapped xDAI (Gnosis)", "homepage": "https://www.gnosis.io/", "description": "Wrapped xDAI (WXDAI) is the ERC-20 wrapped version of xDAI, the native gas token of Gnosis Chain. xDAI is a DAI-backed stablecoin bridged from Ethereum to Gnosis Chain. WXDAI enables xDAI to be used in DeFi protocols that require ERC-20 tokens.", "coingecko": "https://www.coingecko.com/en/coins/wrapped-xdai", "defillama": "", "twitter": "https://x.com/gabornosischain"}],
    "XAUT": [{"name": "Tether Gold", "homepage": "https://gold.tether.to/", "description": "Tether Gold (XAUT) is a gold-backed token issued by TG Commodities Limited, associated with Tether. Each XAUT token represents ownership of one fine troy ounce of physical gold stored in a Swiss vault. XAUT enables fractional ownership of physical gold on blockchain.", "coingecko": "https://www.coingecko.com/en/coins/tether-gold", "defillama": "", "twitter": "https://x.com/Tether_to"}],
    "XDAI": [{"name": "xDAI (Gnosis Chain)", "homepage": "https://www.gnosis.io/", "description": "xDAI is the native gas token of Gnosis Chain (formerly xDai Chain), backed 1:1 by DAI. It is bridged from Ethereum DAI to serve as the native currency of Gnosis Chain. xDAI enables low-cost, stable-value transactions on Gnosis Chain.", "coingecko": "https://www.coingecko.com/en/coins/xdai", "defillama": "", "twitter": "https://x.com/gabornosischain"}],
    "XIDR": [{"name": "StraitsX IDR", "homepage": "https://www.straitsx.com/", "description": "XIDR is an Indonesian Rupiah-backed stablecoin issued by StraitsX, a licensed payment institution in Singapore. Each XIDR is backed 1:1 by IDR reserves. It enables digital transactions denominated in Indonesian Rupiah.", "coingecko": "https://www.coingecko.com/en/coins/straitsx-indonesia-rupiah", "defillama": "", "twitter": "https://x.com/StraitsX"}],
    "XSGD": [{"name": "StraitsX SGD", "homepage": "https://www.straitsx.com/", "description": "XSGD is a Singapore Dollar-backed stablecoin issued by StraitsX, a licensed payment institution in Singapore. Each XSGD is backed 1:1 by SGD reserves held in regulated financial institutions. XSGD is available on Ethereum, Polygon, and other networks.", "coingecko": "https://www.coingecko.com/en/coins/xsgd", "defillama": "", "twitter": "https://x.com/StraitsX"}],
    "XSTUSD": [{"name": "SORA Synthetic USD", "homepage": "https://sora.org/", "description": "XSTUSD is a synthetic USD stablecoin on the SORA network. It is designed for the SORA decentralised economic system. XSTUSD uses a token bonding curve mechanism for price stability.", "coingecko": "", "defillama": "", "twitter": "https://x.com/saborora_xor"}],
    "XUSD": [
        {"name": "StraitsX USD", "homepage": "https://www.straitsx.com/", "description": "XUSD is a USD-backed stablecoin issued by StraitsX, a licensed payment institution in Singapore. Each XUSD is backed 1:1 by USD reserves. It enables digital transactions denominated in US dollars on blockchain.", "coingecko": "", "defillama": "", "twitter": "https://x.com/StraitsX"},
        {"name": "xDollar", "homepage": "https://xdollar.fi/", "description": "xDollar was a multi-chain stablecoin protocol inspired by MakerDAO. It allowed minting xUSD stablecoins against various collateral types. The project has limited current activity.", "coingecko": "", "defillama": "", "twitter": ""},
    ],
    "YUSD": [
        {"name": "YUSD (Yeti Finance)", "homepage": "https://yeti.finance/", "description": "YUSD was a decentralised stablecoin issued by Yeti Finance on Avalanche. It was overcollateralised by yield-bearing assets including LP tokens and staked AVAX. Yeti Finance has been deprecated.", "coingecko": "https://www.coingecko.com/en/coins/yeti-finance", "defillama": "", "twitter": ""},
        {"name": "yUSD (YieldFi)", "homepage": "https://yield.fi/", "description": "yUSD is a stablecoin associated with the YieldFi protocol. It is designed for yield generation in DeFi. Details about its specific mechanism are limited.", "coingecko": "", "defillama": "", "twitter": ""},
    ],
    "ZCHF": [{"name": "Frankencoin", "homepage": "https://www.frankencoin.com/", "description": "Frankencoin (ZCHF) is a Swiss Franc-pegged decentralised stablecoin on Ethereum. It uses an auction-based collateral mechanism rather than traditional CDP models. Frankencoin is designed for the Swiss and European DeFi market.", "coingecko": "https://www.coingecko.com/en/coins/frankencoin", "defillama": "", "twitter": "https://x.com/frankaborcencoin"}],
    "ZSD": [{"name": "Zephyr Stable Dollar", "homepage": "https://www.zephyrprotocol.com/", "description": "Zephyr Stable Dollar (ZSD) is a private stablecoin on the Zephyr Protocol, a Monero-forked privacy blockchain. It uses an overcollateralised djed-style mechanism with ZEPH as the reserve asset. ZSD offers privacy-preserving stable transactions.", "coingecko": "https://www.coingecko.com/en/coins/zephyr-protocol", "defillama": "", "twitter": "https://x.com/zaborephyrprotocol"}],
    "ZUSD": [{"name": "Z.com USD (GMO)", "homepage": "https://stablecoin.z.com/zusd/", "description": "ZUSD is a USD-backed stablecoin issued by GMO-Z.com Trust Company. It is fully backed by US dollars and regulated by the New York State Department of Financial Services. ZUSD is part of GMO Internet Group's digital assets offering.", "coingecko": "https://www.coingecko.com/en/coins/zusd", "defillama": "", "twitter": ""}],
    "avUSD": [{"name": "Avant USD", "homepage": "https://www.avantprotocol.com/", "description": "Avant USD (avUSD) is a stablecoin from the Avant Protocol. It uses delta-neutral strategies to maintain its dollar peg. Avant is designed for yield generation through hedged positions.", "coingecko": "https://www.coingecko.com/en/coins/avant-usd", "defillama": "", "twitter": "https://x.com/avantprotocol"}],
    "bvUSD": [{"name": "BitVault USD", "homepage": "", "description": "BitVault USD (bvUSD) is a stablecoin with limited public information. Details about its backing and mechanism are not readily available. It appears to have minimal market presence.", "coingecko": "", "defillama": "", "twitter": ""}],
    "crvUSD": [{"name": "Curve USD", "homepage": "https://curve.fi/", "description": "crvUSD is the native stablecoin of Curve Finance, using the LLAMMA mechanism for soft liquidations. It is the same asset as CRVUSD but with different casing used on some platforms. crvUSD is overcollateralised and widely used in DeFi.", "coingecko": "https://www.coingecko.com/en/coins/crvusd", "defillama": "https://defillama.com/stablecoin/crvusd", "twitter": "https://x.com/CurveFinance"}],
    "dUSD": [
        {"name": "StandX DUSD", "homepage": "https://www.dusd.com/", "description": "DUSD by StandX is a stablecoin designed for decentralised payments. It aims to provide stable digital currency for everyday transactions. Details about its collateral mechanism are limited.", "coingecko": "", "defillama": "", "twitter": ""},
        {"name": "DefiDollar", "homepage": "https://dusd.finance/", "description": "DefiDollar (DUSD) was a meta-stablecoin that indexed multiple stablecoins to reduce risk. It combined USDC, DAI, USDT, and sUSD into a single index token. The project is no longer actively maintained.", "coingecko": "https://www.coingecko.com/en/coins/defidollar", "defillama": "", "twitter": ""},
    ],
    "deUSD": [{"name": "Elixir deUSD", "homepage": "https://www.elixir.xyz/", "description": "deUSD is a decentralised synthetic dollar by Elixir Protocol. It uses institutional-grade market making and delta-neutral strategies. Elixir focuses on providing deep liquidity across orderbook exchanges.", "coingecko": "https://www.coingecko.com/en/coins/elixir-deusd", "defillama": "https://defillama.com/stablecoin/elixir-deusd", "twitter": "https://x.com/ElixirProtocol"}],
    "frxUSD": [{"name": "Frax USD", "homepage": "https://frax.finance/", "description": "frxUSD is the latest stablecoin from Frax Finance, fully backed by US dollar reserves and BlackRock's BUIDL fund. It replaces the partially algorithmic FRAX model with full reserve backing. frxUSD is designed for use across the Frax ecosystem.", "coingecko": "https://www.coingecko.com/en/coins/frax-usd", "defillama": "https://defillama.com/stablecoin/frax-usd", "twitter": "https://x.com/fraboraxfinance"}],
    "ftUSD": [{"name": "Flying Tulip USD", "homepage": "https://flyingtulip.com/", "description": "ftUSD is a stablecoin from Flying Tulip, an AMM and lending protocol by Andre Cronje. It is designed for capital-efficient DeFi operations. Flying Tulip uses adaptive curve mechanisms for improved trading.", "coingecko": "", "defillama": "", "twitter": "https://x.com/flyingtulaborip"}],
    "gmUSD": [{"name": "GND Protocol gmUSD", "homepage": "https://gndprotocol.com/", "description": "gmUSD is a stablecoin from the GND Protocol designed around GMX's GLP and GM tokens. It uses delta-neutral strategies on GMX positions. gmUSD is available on Arbitrum.", "coingecko": "", "defillama": "", "twitter": ""}],
    "iUSD": [{"name": "Indigo Protocol iUSD", "homepage": "https://indigoprotocol.io/", "description": "iUSD is a synthetic USD stablecoin on the Cardano blockchain issued by Indigo Protocol. It is overcollateralised by ADA and uses a CDP model inspired by MakerDAO. iUSD is the primary stablecoin in the Cardano DeFi ecosystem.", "coingecko": "https://www.coingecko.com/en/coins/indigo-protocol-iusd", "defillama": "", "twitter": "https://x.com/IndigoProtocol1"}],
    "jEUR": [{"name": "Jarvis Synthetic Euro", "homepage": "https://www.jarvis.network/", "description": "jEUR is a synthetic Euro stablecoin issued by Jarvis Network. It is overcollateralised by USDC and uses Chainlink oracles for EUR/USD price feeds. jEUR enables on-chain Euro exposure across multiple chains.", "coingecko": "https://www.coingecko.com/en/coins/jarvis-synthetic-euro", "defillama": "", "twitter": "https://x.com/Jarvis_Network"}],
    "kUSD": [{"name": "Kolibri USD", "homepage": "https://kolibri.finance/", "description": "Kolibri USD (kUSD) is a decentralised stablecoin on the Tezos blockchain. It is overcollateralised by XTZ deposits using a CDP model. kUSD is the primary decentralised stablecoin in the Tezos ecosystem.", "coingecko": "https://www.coingecko.com/en/coins/kolibri-usd", "defillama": "", "twitter": "https://x.com/haboroverengineered"}],
    "lvlUSD": [{"name": "Level USD", "homepage": "https://www.level.money/", "description": "Level USD (lvlUSD) is a stablecoin by Level Money designed for yield generation. It is backed by diversified reserves and DeFi strategies. Level Money focuses on providing accessible stable yield to users.", "coingecko": "https://www.coingecko.com/en/coins/level-usd", "defillama": "", "twitter": "https://x.com/leveldotmoney"}],
    "mUSD": [{"name": "mStable USD", "homepage": "https://www.mstable.com/", "description": "mStable USD (mUSD) was a meta-stablecoin combining multiple USD stablecoins into one token. It aggregated USDC, DAI, and USDT to reduce individual stablecoin risk. The mStable protocol has been deprecated.", "coingecko": "https://www.coingecko.com/en/coins/musd", "defillama": "https://defillama.com/stablecoin/musd", "twitter": ""}],
    "meUSDT": [{"name": "Unknown", "homepage": "", "description": "", "coingecko": "", "defillama": "", "twitter": ""}],
    "msUSD": [{"name": "Main Street USD", "homepage": "https://mainstreet.finance/", "description": "Main Street USD (msUSD) is a stablecoin from Main Street Finance. Details about its backing mechanism and design are limited. It appears to be a smaller DeFi protocol stablecoin.", "coingecko": "", "defillama": "", "twitter": ""}],
    "plUSD": [{"name": "PolyLiquity USD", "homepage": "https://polyliquity.finance/", "description": "PolyLiquity USD (plUSD) is a stablecoin forked from the Liquity protocol deployed on Polygon. It uses the same overcollateralised ETH-backed model as Liquity. plUSD is specific to the Polygon ecosystem.", "coingecko": "", "defillama": "", "twitter": ""}],
    "reUSD": [{"name": "Resupply USD", "homepage": "https://resupply.fi/", "description": "Resupply USD (reUSD) is a stablecoin by Resupply, a protocol built by the teams behind Convex and Yearn Finance. It aggregates lending positions from Fraxlend and Curve to provide yield. reUSD is designed for capital-efficient stablecoin borrowing.", "coingecko": "https://www.coingecko.com/en/coins/resupply-reusd", "defillama": "", "twitter": "https://x.com/ResaborupplyFi"}],
    "sUSDC": [{"name": "Spark Savings USDC", "homepage": "https://spark.fi/", "description": "sUSDC is Spark Protocol's savings version of USDC that earns yield from the Sky (MakerDAO) ecosystem. It represents USDC deposited into the Spark lending market. Spark is the front end for the Sky Protocol's DeFi offerings.", "coingecko": "", "defillama": "", "twitter": "https://x.com/sparkdotfi"}],
    "satUSD": [
        {"name": "Satoshi Stablecoin", "homepage": "https://www.satoshiprotocol.org/", "description": "Satoshi Stablecoin (satUSD) is a BTC-backed stablecoin from Satoshi Protocol. It allows users to borrow satUSD against Bitcoin collateral. The protocol is designed for the Bitcoin DeFi ecosystem.", "coingecko": "https://www.coingecko.com/en/coins/satoshi-stablecoin", "defillama": "", "twitter": "https://x.com/SaboratoshiProtocol"},
        {"name": "River Protocol satUSD", "homepage": "https://river.money/", "description": "River Protocol's satUSD is a stablecoin designed for the Bitcoin ecosystem. It provides stable value within Bitcoin-native DeFi applications. Details about its specific mechanism are limited.", "coingecko": "", "defillama": "", "twitter": ""},
    ],
    "scUSD": [{"name": "Rings scUSD (Sonic)", "homepage": "https://rings.money/", "description": "scUSD is a stablecoin from Rings Protocol on the Sonic (formerly Fantom) network. It is designed for stable value transfer within the Sonic ecosystem. scUSD provides DeFi utility on the high-performance Sonic chain.", "coingecko": "", "defillama": "", "twitter": "https://x.com/RingsProtocol"}],
    "sosUSDT": [{"name": "Unknown", "homepage": "", "description": "", "coingecko": "", "defillama": "", "twitter": ""}],
    "vbUSDC": [{"name": "Vault Bridge Bridged USDC (Katana)", "homepage": "", "description": "vbUSDC is a bridged version of USDC on the Katana (Ronin) network via Vault Bridge. It represents USDC locked on Ethereum and bridged to Ronin. vbUSDC is used within the Ronin ecosystem for DeFi and gaming.", "coingecko": "", "defillama": "", "twitter": ""}],
    "vbUSDT": [{"name": "Vault Bridge Bridged USDT (Katana)", "homepage": "", "description": "vbUSDT is a bridged version of USDT on the Katana (Ronin) network via Vault Bridge. It represents USDT locked on Ethereum and bridged to Ronin. vbUSDT is used within the Ronin ecosystem for DeFi and gaming.", "coingecko": "", "defillama": "", "twitter": ""}],
    "wM": [{"name": "Wrapped M (M^0 Protocol)", "homepage": "https://www.m0.org/", "description": "Wrapped M (wM) is the wrapped version of the M token from M^0 Protocol. M^0 is a decentralised stablecoin infrastructure that allows approved minters to issue M against eligible collateral. wM enables M to be used across DeFi protocols.", "coingecko": "https://www.coingecko.com/en/coins/wrapped-m", "defillama": "https://defillama.com/stablecoin/wrapped-m", "twitter": "https://x.com/m0foundation"}],
    "xUSD": [
        {"name": "StraitsX USD", "homepage": "https://www.straitsx.com/", "description": "xUSD is a USD-backed stablecoin issued by StraitsX, a licensed payment institution in Singapore. Each xUSD is backed 1:1 by USD reserves held in regulated financial institutions. xUSD enables digital USD transactions in Southeast Asia.", "coingecko": "", "defillama": "", "twitter": "https://x.com/StraitsX"},
        {"name": "xDollar", "homepage": "https://xdollar.fi/", "description": "xDollar was a multi-chain stablecoin protocol inspired by MakerDAO. It allowed minting xUSD stablecoins against various collateral types. The project has limited current activity.", "coingecko": "", "defillama": "", "twitter": ""},
    ],
    "MTUSD": [{"name": "Unknown", "homepage": "", "description": "", "coingecko": "", "defillama": "", "twitter": ""}],
    "ysUSDC": [{"name": "Yearn V3 USDC Vault Token", "homepage": "https://yearn.fi/", "description": "ysUSDC is the vault token from Yearn Finance V3 representing USDC deposited in a Yearn yield strategy. It automatically earns yield from optimised DeFi lending and farming strategies. Yearn V3 uses a modular vault architecture.", "coingecko": "", "defillama": "", "twitter": "https://x.com/yearnfi"}],
    "mtUSDC": [{"name": "Mutuum Finance USDC", "homepage": "https://www.mutuum.finance/", "description": "mtUSDC represents USDC deposited in the Mutuum Finance lending protocol. It is a receipt token that accrues interest from borrowers. Mutuum Finance provides decentralised lending and borrowing.", "coingecko": "", "defillama": "", "twitter": "https://x.com/MutuumFinance"}],
    "mtUSDT": [{"name": "Mutuum Finance USDT", "homepage": "https://www.mutuum.finance/", "description": "mtUSDT represents USDT deposited in the Mutuum Finance lending protocol. It is a receipt token that accrues interest from borrowers. Mutuum Finance provides decentralised lending and borrowing.", "coingecko": "", "defillama": "", "twitter": "https://x.com/MutuumFinance"}],
    # YIELD_BEARING_STABLES members
    "sfrxUSD": [{"name": "Staked Frax USD", "homepage": "https://frax.finance/", "description": "sfrxUSD is the staked version of frxUSD that earns yield from Frax Finance's strategies. It represents frxUSD deposited in the Frax staking vault. sfrxUSD accrues yield from T-bill and DeFi revenue.", "coingecko": "https://www.coingecko.com/en/coins/staked-frax-usd", "defillama": "", "twitter": "https://x.com/fraboraxfinance"}],
    "sUSDe": [{"name": "Ethena Staked USDe", "homepage": "https://ethena.fi/", "description": "sUSDe is the staked version of Ethena's USDe that earns yield from funding rates and staking rewards. It represents USDe deposited in the Ethena staking contract. sUSDe is one of the highest-yielding stablecoin products in DeFi.", "coingecko": "https://www.coingecko.com/en/coins/ethena-staked-usde", "defillama": "", "twitter": "https://x.com/ethena_labs"}],
    "sUSDai": [{"name": "Staked USDai (USD.AI)", "homepage": "https://usd.ai/", "description": "sUSDai is the staked version of USDai from the USD.AI protocol. It earns yield through the protocol's AI-managed strategies. Details about its specific mechanism are limited.", "coingecko": "", "defillama": "", "twitter": "https://x.com/usd_ai"}],
    "sBOLD": [{"name": "sBOLD (K3 Capital / Liquity V2)", "homepage": "https://www.liquity.org/", "description": "sBOLD is a staked version of Liquity V2's BOLD stablecoin. It earns yield from Liquity V2's stability pool and protocol revenues. sBOLD is part of the Liquity V2 ecosystem.", "coingecko": "", "defillama": "", "twitter": "https://x.com/LiquityProtocol"}],
    "sAUSD": [{"name": "Unknown (possibly staked Acala USD)", "homepage": "https://acala.network/", "description": "sAUSD appears to be a staked version of Acala Dollar (aUSD) on the Polkadot ecosystem. Details about its specific implementation are limited. It may be a yield-bearing wrapper for aUSD.", "coingecko": "", "defillama": "", "twitter": "https://x.com/AcalaNetwork"}],
    "ynUSDx": [{"name": "YieldNest USD", "homepage": "https://app.yieldnest.finance/", "description": "ynUSDx is a yield-bearing USD token from YieldNest Finance. It aggregates stablecoin yield from diversified DeFi strategies. YieldNest provides optimised yield across multiple protocols.", "coingecko": "", "defillama": "", "twitter": "https://x.com/yaborieldnest"}],
    # WRAPPED_STABLECOIN_LIKE members
    "cUSDC": [{"name": "Compound USDC", "homepage": "https://compound.finance/", "description": "cUSDC is the Compound protocol's interest-bearing token representing USDC supplied to Compound V2. It accrues interest automatically as borrowers pay interest on USDC loans. cUSDC was one of the first yield-bearing stablecoin tokens in DeFi.", "coingecko": "https://www.coingecko.com/en/coins/compound-usd-coin", "defillama": "", "twitter": "https://x.com/compaboroundfinance"}],
    "cUSDT": [{"name": "Compound USDT", "homepage": "https://compound.finance/", "description": "cUSDT is the Compound protocol's interest-bearing token representing USDT supplied to Compound V2. It accrues interest automatically as borrowers pay interest on USDT loans. cUSDT enables passive yield on Tether holdings.", "coingecko": "https://www.coingecko.com/en/coins/compound-usdt", "defillama": "", "twitter": "https://x.com/compaboroundfinance"}],
    "sUSD": [{"name": "Synthetix sUSD", "homepage": "https://synthetix.io/", "description": "sUSD is the native stablecoin of the Synthetix protocol, pegged to the US Dollar. It is backed by SNX tokens staked by participants in the Synthetix system. sUSD serves as the base trading pair for all Synthetix synthetic assets.", "coingecko": "https://www.coingecko.com/en/coins/susd", "defillama": "https://defillama.com/stablecoin/susd", "twitter": "https://x.com/synthetix_io"}],
    "aDAI": [{"name": "Aave DAI", "homepage": "https://aave.com/", "description": "aDAI is the Aave protocol's interest-bearing token representing DAI supplied to Aave lending pools. It accrues interest in real-time as borrowers pay interest on DAI loans. aDAI was a foundational DeFi yield-bearing token.", "coingecko": "https://www.coingecko.com/en/coins/aave-dai", "defillama": "", "twitter": "https://x.com/aaborave"}],
    "cDAI": [{"name": "Compound DAI", "homepage": "https://compound.finance/", "description": "cDAI is the Compound protocol's interest-bearing token representing DAI supplied to Compound V2. It accrues interest automatically as borrowers pay interest on DAI loans. cDAI was one of the earliest DeFi yield-bearing tokens.", "coingecko": "https://www.coingecko.com/en/coins/cdai", "defillama": "", "twitter": "https://x.com/compaboroundfinance"}],
    "tfUSDC": [{"name": "TrueFi USDC", "homepage": "https://truefi.io/", "description": "tfUSDC represents USDC deposited into TrueFi lending pools. TrueFi provides uncollateralised lending to institutional borrowers. tfUSDC earns yield from interest paid by vetted borrowers.", "coingecko": "", "defillama": "", "twitter": "https://x.com/TrueFi_DAO"}],
    "alUSD": [{"name": "Alchemix USD", "homepage": "https://alchemix.fi/", "description": "alUSD is a synthetic stablecoin issued by the Alchemix protocol on Ethereum. It enables self-repaying loans backed by yield-bearing deposits. The Alchemix protocol automatically repays debt using generated yield.", "coingecko": "https://www.coingecko.com/en/coins/alchemix-usd", "defillama": "https://defillama.com/stablecoin/alchemix-usd", "twitter": "https://x.com/AlchemixFi"}],
    "agEUR": [{"name": "EURA (Angle Protocol, formerly agEUR)", "homepage": "https://www.angle.money/", "description": "agEUR (now EURA) is a Euro-pegged stablecoin issued by Angle Protocol. It is backed by overcollateralised loans and yield-bearing reserves. agEUR was rebranded to EURA as part of Angle Protocol's evolution.", "coingecko": "https://www.coingecko.com/en/coins/ageur", "defillama": "https://defillama.com/stablecoin/ageur", "twitter": "https://x.com/AngleProtocol"}],
    "gmdUSDC": [{"name": "GMD Protocol USDC Vault", "homepage": "https://gmdprotocol.com/", "description": "gmdUSDC represents USDC deposited in GMD Protocol's single-sided yield vaults. GMD aggregates yield from GMX's GLP token for stablecoin depositors. gmdUSDC provides delta-neutral exposure to GLP yield.", "coingecko": "", "defillama": "", "twitter": "https://x.com/gabormdprotocol"}],
    "gDAI": [{"name": "Gains Network DAI", "homepage": "https://gains.trade/", "description": "gDAI represents DAI deposited into the Gains Network (gTrade) vault on Arbitrum and Polygon. It earns yield from trading fees generated by the gTrade decentralised leverage trading platform. gDAI acts as the counterparty to traders on the platform.", "coingecko": "https://www.coingecko.com/en/coins/gains-network-dai", "defillama": "", "twitter": "https://x.com/GainsNetwork_io"}],
    "blUSD": [{"name": "Boosted LUSD (Chicken Bonds / Liquity)", "homepage": "https://www.chickenbonds.org/", "description": "bLUSD (Boosted LUSD) is a yield-amplified version of LUSD from the Chicken Bonds mechanism by Liquity. It earns enhanced yield from Liquity's stability pool and other DeFi strategies. Chicken Bonds uses a novel bonding mechanism for yield amplification.", "coingecko": "https://www.coingecko.com/en/coins/boosted-lusd", "defillama": "", "twitter": "https://x.com/chickaborenbonds"}],
}


#: Some test accounts with funded USDC for Anvil mainnet forking
#:
#: TBD: In theory we can find ERC-20 balance slots and write value there with Anvil, but
#: it is difficult to do reliably.
LARGE_USDC_HOLDERS = {
    # Arbitrum
    42161: "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
    # Ave
    43114: "0x9f8c163cBA728e99993ABe7495F06c0A3c8Ac8b9",
    # Base
    # Bybits hot wallet
    # https://basescan.org/token/0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913
    8453: "0xBaeD383EDE0e5d9d72430661f3285DAa77E9439F",
}


@dataclass(frozen=True, slots=True)
class DummyPickledContract:
    """Contract placeholder making contract references pickable"""

    address: str


@dataclass
class TokenDetails:
    """ERC-20 token Python presentation.

    - A helper class to work with ERC-20 tokens.

    - Read on-chain data, deal with token value decimal conversions.

    - Any field can be ``None`` for non-well-formed tokens.

    - Supports one-way pickling

    Example how to get USDC details on Polygon:

    .. code-block:: python

        usdc = fetch_erc20_details(web3, "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")  # USDC on Polygon
        formatted = f"Token {usdc.name} ({usdc.symbol}) at {usdc.address} on chain {usdc.chain_id}"
        assert formatted == "Token USD Coin (PoS) (USDC) at 0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174 on chain 137"
    """

    #: The underlying ERC-20 contract proxy class instance
    contract: Contract

    #: Token name e.g. ``USD Circle``
    name: Optional[str] = None

    #: Token symbol e.g. ``USDC``
    symbol: Optional[str] = None

    #: Token supply as raw units
    total_supply: Optional[int] = None

    #: Number of decimals
    decimals: Optional[int] = None

    #: Extra metadata, e.g. related to caching this result
    extra_data: dict[str, Any] = field(default_factory=dict)

    def __eq__(self, other):
        """Token is the same if it's on the same chain and has the same contract address."""
        assert isinstance(other, TokenDetails)
        return (self.contract.address == other.contract.address) and (self.chain_id == other.chain_id)

    def __hash__(self):
        """Token hash."""
        return hash((self.chain_id, self.contract.address))

    def __repr__(self):
        return f"<{self.name} ({self.symbol}) at {self.contract.address}, {self.decimals} decimals, on chain {self.chain_id}>"

    def __getstate__(self):
        """Contract cannot be pickled."""
        state = self.__dict__.copy()
        state["contract"] = DummyPickledContract(address=self.contract.address)
        return state

    def __setstate__(self, state):
        """Contract cannot be pickled."""
        self.__dict__.update(state)

    @cached_property
    def chain_id(self) -> int:
        """The EVM chain id where this token lives."""
        return self.contract.w3.eth.chain_id

    @cached_property
    def address(self) -> HexAddress:
        """The address of this token.

        See also :py:meth:`address_lower`.
        """
        return self.contract.address

    @cached_property
    def address_lower(self) -> HexAddress:
        """The address of this token.

        Always lowercase.
        """
        return self.contract.address.lower()

    @property
    def functions(self) -> ContractFunctions:
        """Alias for underlying Web3 contract method"""
        return self.contract.functions

    def convert_to_decimals(self, raw_amount: int) -> Decimal:
        """Convert raw token units to decimals.

        Example:

        .. code-block:: python

            details = fetch_erc20_details(web3, token_address)
            # Convert 1 wei units to edcimals
            assert details.convert_to_decimals(1) == Decimal("0.0000000000000001")

        """
        assert type(raw_amount) == int, f"Got {type(raw_amount)}, expected int: {raw_amount}"
        return Decimal(raw_amount) / Decimal(10**self.decimals)

    def convert_to_raw(self, decimal_amount: Decimal) -> int:
        """Convert decimalised token amount to raw uint256.

        Example:

        .. code-block:: python

            details = fetch_erc20_details(web3, token_address)
            # Convert 1.0 USDC to raw unit with 6 decimals
            assert details.convert_to_raw(1) == 1_000_000

        """
        return int(decimal_amount * 10**self.decimals)

    def fetch_balance_of(self, address: HexAddress | str, block_identifier="latest") -> Decimal:
        """Get an address token balance.

        :param block_identifier:
            A specific block to query if doing archive node historical queries

        :return:
            Converted to decimal using :py:meth:`convert_to_decimal`
        """
        address = Web3.to_checksum_address(address)
        raw_amount = self.contract.functions.balanceOf(address).call(block_identifier=block_identifier)
        return self.convert_to_decimals(raw_amount)

    def transfer(
        self,
        to: HexAddress | str,
        amount: Decimal,
    ) -> ContractFunction:
        """Prepare a ERC20.transfer() transaction with human-readable amount.

        Example:

        .. code-block:: python

            another_new_depositor = web3.eth.accounts[6]
            tx_hash = base_usdc.transfer(another_new_depositor, Decimal(500)).transact({"from": usdc_holder, "gas": 100_000})
            assert_transaction_success_with_explanation(web3, tx_hash)

        :return:
            Bound contract function you need to turn to a tx
        """
        assert isinstance(amount, Decimal), f"Give amounts in decimal, got {type(amount)}"
        to = Web3.to_checksum_address(to)
        raw_amount = self.convert_to_raw(amount)
        return self.contract.functions.transfer(to, raw_amount)

    def approve(
        self,
        to: HexAddress | str,
        amount: Decimal,
    ) -> ContractFunction:
        """Prepare a ERC20.approve() transaction with human-readable amount.

        Example:

        .. code-block:: python

            usdc_amount = Decimal(9.00)
            tx_hash = usdc.approve(vault.address, usdc_amount).transact({"from": depositor})
            assert_transaction_success_with_explanation(web3, tx_hash)

        :return:
            Bound contract function you need to turn to a tx
        """
        assert isinstance(amount, Decimal), f"Give amounts in decimal, got {type(amount)}"
        to = Web3.to_checksum_address(to)
        raw_amount = self.convert_to_raw(amount)
        return self.contract.functions.approve(to, raw_amount)

    def fetch_raw_balance_of(self, address: HexAddress | str, block_identifier="latest") -> Decimal:
        """Get an address token balance.

        :param block_identifier:
            A specific block to query if doing archive node historical queries

        :return:
            Raw token amount.
        """
        address = Web3.to_checksum_address(address)
        raw_amount = self.contract.functions.balanceOf(address).call(block_identifier=block_identifier)
        return raw_amount

    @staticmethod
    def generate_cache_key(chain_id: int, address: str) -> str:
        """Generate a cache key for this token.

        - Cached by (chain, address) as a string

        - Validate the inputs before generating the key

        - Address is always lowercase

        :return:
            Human reaadable {chain_id}-{address}
        """
        assert type(chain_id) == int, f"Bad chain id: {chain_id}"
        assert type(address) == str
        assert address.startswith("0x"), f"Bad token address: {address}"
        return f"{chain_id}-{address.lower()}"

    def export(self) -> dict:
        """Create a serialisable entry of this class.

        Removes web3 connection and such unserialisable data.

        :return:
            Python dict of exported data.
        """
        clone = dict(**self.__dict__)
        clone["address"] = self.address
        clone["chain"] = self.chain_id
        del clone["contract"]
        return clone

    def is_stablecoin_like(self) -> bool:
        """Smell test for stablecoins.

        - Symbol check for common stablecoins
        - Not immune to scams
        - For the list see :py:func:`is_stablecoin_like`

        :return:
            True if we think this could be a stablecoin.
        """
        return is_stablecoin_like(self.symbol)


class TokenDetailError(Exception):
    """Cannot extract token details for an ERC-20 token for some reason."""


def create_token(
    web3: Web3,
    deployer: str,
    name: str,
    symbol: str,
    supply: int,
    decimals: int = 18,
) -> Contract:
    """Deploys a new ERC-20 token on local dev, testnet or mainnet.

    - Uses `ERC20Mock <https://github.com/sushiswap/sushiswap/blob/canary/contracts/mocks/ERC20Mock.sol>`_ contract for the deployment.

    - Waits until the transaction has completed

    Example:

    .. code-block::

        # Deploys an ERC-20 token where 100,000 tokens are allocated ato the deployer address
        token = create_token(web3, deployer, "Hentai books token", "HENTAI", 100_000 * 10**18)
        print(f"Deployed token contract address is {token.address}")
        print(f"Deployer account {deployer} has {token.functions.balanceOf(user_1).call() / 10**18} tokens")

    Find more examples in :ref:`tutorials` and unit testing source code.

    :param web3:
        Web3 instance

    :param deployer:
        Deployer account as 0x address.

        Make sure this account has enough ETH or native token to cover the gas cost.

    :param name: Token name

    :param symbol: Token symbol

    :param supply: Token starting supply as raw units.

        E.g. ``500 * 10**18`` to have 500 tokens minted to the deployer
        at the start.

    :param decimals: How many decimals ERC-20 token values have

    :return:
        Instance to a deployed Web3 contract.
    """
    return deploy_contract(web3, "ERC20MockDecimals.json", deployer, name, symbol, supply, decimals)


def get_erc20_contract(
    web3: Web3,
    address: HexAddress,
    contract_name="ERC20MockDecimals.json",
) -> Contract:
    """Wrap address as ERC-20 standard interface."""
    return get_deployed_contract(web3, contract_name, address)


def fetch_erc20_details(
    web3: Web3,
    token_address: Union[HexAddress, str],
    max_str_length: int = 256,
    raise_on_error=True,
    contract_name="ERC20MockDecimals.json",
    cache: dict | None = DEFAULT_TOKEN_CACHE,
    chain_id: int = None,
    cause_diagnostics_message: str | None = None,
) -> TokenDetails:
    """Read token details from on-chain data.

    Connect to Web3 node and do RPC calls to extract the token info.
    We apply some sanitazation for incoming data, like length checks and removal of null bytes.

    The function should not raise an exception as long as the underlying node connection does not fail.

    .. note ::

        Always give ``chain_id`` when possible. Otherwise the caching of data is inefficient.

    Example:

    .. code-block:: python

        details = fetch_erc20_details(web3, token_address)
        assert details.name == "Hentai books token"
        assert details.decimals == 6

    :param web3:
        Web3 instance

    :param token_address:
        ERC-20 contract address:

    :param max_str_length:
        For input sanitisation

    :param raise_on_error:
        If set, raise `TokenDetailError` on any error instead of silently ignoring in and setting details to None.

    :param contract_name:
        Contract ABI file to use.

        The default is ``ERC20MockDecimals.json``. For USDC use ``centre/FiatToken.json``.

    :param cache:
        Use this cache for cache token detail calls.

        The main purpose is to easily reduce JSON-RPC API call count.

        By default, we use LRU cache of 1024 entries.

        Set to ``None`` to disable the cache.

        Instance of :py:class:`cachetools.Cache`.
        See `cachetools documentation for details <https://cachetools.readthedocs.io/en/latest/#cachetools.LRUCache>`__.

    :param chain_id:
        Chain id hint for the cache.

        If not given do ``eth_chainId`` RPC call to figure out.

    :param cause_diagnostics_message:
        Log in Python logging subsystem why this fetch was done to debug RPC overuse.

    :return:
        Sanitised token info
    """

    if not chain_id:
        chain_id_given = False
        chain_id = web3.eth.chain_id
    else:
        chain_id_given = True

    erc_20 = get_erc20_contract(web3, token_address, contract_name)

    key = TokenDetails.generate_cache_key(chain_id, token_address)

    if cache is not None:
        cached = cache.get(key)
        if cached is not None:
            return TokenDetails(
                erc_20,
                cached["name"],
                cached["symbol"],
                cached["supply"],
                cached["decimals"],
                extra_data={"cached": True},
            )

    logger.info(
        "Fetching uncached token, chain %s, address %s, chain id given: %s, reason: %s, token cache %s has %d entries",
        chain_id,
        token_address,
        chain_id_given,
        cause_diagnostics_message,
        cache.__class__.__name__,
        len(cache) if cache is not None else -1,
    )

    try:
        try:
            if chain_id == 42161 and token_address.lower() == "0xff970a61a04b1ca14834a43f5de4533ebddb5cc8":
                # Legacy USDC on Arbitrum.
                # The contract still returns the old symbol, but Arbiscan and others show USDC.e,
                # and because this is widespread we do this hack override here.
                symbol = "USDC.e"
            else:
                raw_resp = erc_20.functions.symbol().call()
                symbol = sanitise_string(raw_resp[0:max_str_length])
        except BadFunctionCallOutput as e:
            # ABI mismatch
            # MakerDAO f*** yeah
            # *** web3.exceptions.BadFunctionCallOutput: Could not decode contract function call to symbol() with return data: b'MKR\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00', output_types: ['string']
            msg = str(e)

            start = msg.find("b'") + 2
            end = msg.find("\\x00", start)
            if end != -1:
                value = msg[start:end]
                symbol = value
            else:
                raise

    except ReadTimeout as e:
        # Handle this specially because Anvil is piece of hanging shit
        # and we need to manually clean up these all the time
        provider_name = get_provider_name(web3.provider)
        raise TokenDetailError(f"Token {token_address} timeout reading on chain {chain_id}: {e}, provider {provider_name}") from e
    except _call_missing_exceptions as e:
        if raise_on_error:
            raise TokenDetailError(f"Token {token_address} missing symbol on chain {chain_id}: {e}") from e
        symbol = None
    except OverflowError:
        # OverflowError: Python int too large to convert to C ssize_t
        # Que?
        # Sai Stablecoin uses bytes32 instead of string for name and symbol information
        # https://etherscan.io/address/0x89d24a6b4ccb1b6faa2625fe562bdd9a23260359#readContract
        symbol = None

    try:
        name = sanitise_string(erc_20.functions.name().call()[0:max_str_length])
    except BadFunctionCallOutput as e:
        # ABI mismatch
        # MakerDAO f*** yeah
        # *** web3.exceptions.BadFunctionCallOutput: Could not decode contract function call to symbol() with return data: b'MKR\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00', output_types: ['string']
        msg = str(e)

        start = msg.find("b'") + 2
        end = msg.find("\\x00", start)
        if end != -1:
            value = msg[start:end]
            name = value
        else:
            if raise_on_error:
                raise
            else:
                name = None
    except _call_missing_exceptions as e:
        if raise_on_error:
            raise TokenDetailError(f"Token {token_address} missing name: {e}") from e
        name = None
    except OverflowError:
        # OverflowError: Python int too large to convert to C ssize_t
        # Que?
        # Sai Stablecoin uses bytes32 instead of string for name and symbol information
        # https://etherscan.io/address/0x89d24a6b4ccb1b6faa2625fe562bdd9a23260359#readContract
        name = None

    try:
        decimals = erc_20.functions.decimals().call()
    except _call_missing_exceptions as e:
        if raise_on_error:
            raise TokenDetailError(f"Token {token_address} missing decimals") from e
        decimals = 0

    try:
        supply = erc_20.functions.totalSupply().call()
    except _call_missing_exceptions as e:
        if raise_on_error:
            raise TokenDetailError(f"Token {token_address} missing totalSupply") from e
        supply = None

    token_details = TokenDetails(erc_20, name, symbol, supply, decimals, extra_data={"cached": False})
    if cache is not None:
        cache[key] = {
            "name": name,
            "symbol": symbol,
            "supply": supply,
            "decimals": decimals,
        }
    return token_details


def reset_default_token_cache():
    """Purge the cached token data.

    See :py:data:`DEFAULT_TOKEN_CACHE`
    """
    global DEFAULT_TOKEN_CACHE
    # Cache has a horrible API
    DEFAULT_TOKEN_CACHE.__dict__["_LRUCache__order"] = OrderedDict()
    DEFAULT_TOKEN_CACHE.__dict__["_Cache__currsize"] = 0
    DEFAULT_TOKEN_CACHE.__dict__["_Cache__data"] = dict()


def get_wrapped_native_token_address(chain_id: int):
    address = WRAPPED_NATIVE_TOKEN.get(chain_id)
    assert address, f"Chain id {chain_id} not found"
    return address


def get_chain_stablecoins(chain_id: int) -> set[TokenDetails]:
    """Get all good known stablecoins on a chain.

    :raise AssertionError:
        Chain has zero stablecoins configured
    """

    assert type(chain_id) is int

    tokens = set()
    usdc = USDC_NATIVE_TOKEN.get(chain_id)
    if usdc is not None:
        tokens.add(usdc)

    usdt = USDT_NATIVE_TOKEN.get(chain_id)
    if usdt is not None:
        tokens.add(usdt)

    susds = SUSDS_NATIVE_TOKEN.get(chain_id)
    if susds is not None:
        tokens.add(susds)

    honey = HONEY_NATIVE_TOKEN.get(chain_id)
    if honey is not None:
        tokens.add(honey)

    assert len(tokens) > 0, f"Zero known good stablecoins configured for chain {chain_id}"
    return tokens


def get_chain_known_quote_tokens(chain_id: int) -> set[TokenDetails]:
    """Get all good quote tokens on  chain."""
    pass


def is_stablecoin_like(token_symbol: str | None, symbol_list=ALL_STABLECOIN_LIKE) -> bool:
    """Check if specific token symbol is likely a stablecoin.

    Useful for quickly filtering stable/stable pairs in the pools.
    However, you should never rely on this check alone.

    Note that new stablecoins might be introduced, so this check
    is never going to be future proof.

    Example:

    .. code-block:: python

        assert is_stablecoin_like("USDC") == True
        assert is_stablecoin_like("USDT") == True
        assert is_stablecoin_like("GHO") == True
        assert is_stablecoin_like("crvUSD") == True
        assert is_stablecoin_like("WBTC") == False

    :param token_symbol:
        Token symbol as it is written on the contract.
        May contain lower and uppercase latter.

    :param symbol_list:
        Which filtering list we use.
    """

    if token_symbol is None:
        return False

    assert isinstance(token_symbol, str), f"We got {token_symbol}"
    return token_symbol in symbol_list


def normalise_token_symbol(token_symbol: str | None) -> str | None:
    """Normalise token symbol for stablecoin detection.

    - Uppercase
    - Remove bridge suffixes
    - Fix USDT variations

    :param token_symbol:
        Token symbol as it is written on the contract.

    :return:
        Normalised token symbol
    """

    if token_symbol is None:
        return None

    assert isinstance(token_symbol, str), f"We got {token_symbol}"

    token_symbol = token_symbol.upper()

    if token_symbol.endswith(".E"):
        token_symbol = token_symbol.removesuffix(".E")

    if token_symbol in {"USDT0", "USD₮0"}:
        token_symbol = "USDT"

    return token_symbol


def get_weth_contract(web3: Web3, name: str = "1delta/IWETH9.json") -> Contract:
    """Get WETH9 contract for the chain

    - `See WETH9 contract <https://www.contractreader.io/contract/mainnet/0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2>`__
    - WETH9 is different contract with different functions on different chain

    :param web3:
        Web3 instance

    :param name:
        Alternative implementation.

    :return:
        WETH token details
    """
    chain_id = web3.eth.chain_id
    weth_address = get_wrapped_native_token_address(chain_id)
    return get_deployed_contract(
        web3,
        name,
        weth_address,
    )


class TokenCacheWarmupResult(TypedDict):
    tokens_read: int
    multicalls_done: int


class TokenDiskCache(PersistentKeyValueStore):
    """Token cache that stores tokens in disk.

    - Use with :py:func:`fetch_erc20_details`
    - For loading hundreds of tokens once
    - Shared across chains
    - Enable fast cache warmup with :py:meth:`load_token_details_with_multicall`
    - Persistent: Make sure subsequent batch jobs do not refetch token data over RPC as it is expensive
    - Store as a SQLite database

    Example:

    .. code-block:: python

        addresses = [
            "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",  # USDC
            "0x4200000000000000000000000000000000000006",  # WETH
            "0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb",  # DAI
            "0x554a1283cecca5a46bc31c2b82d6702785fc72d9",  # UNI
        ]

        cache = TokenDiskCache(tmp_path / "disk_cache.sqlite")
        web3factory = MultiProviderWeb3Factory(JSON_RPC_BASE)
        web3 = web3factory()

        #
        # Do single token lookups against cache
        #
        token = fetch_erc20_details(
            web3,
            token_address="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
            chain_id=web3.eth.chain_id,
            cache=cache,
        )
        assert token.extra_data["cached"] == False
        assert len(cache) == 1
        # After one look up, we should have it cached
        token = fetch_erc20_details(
            web3,
            token_address="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
            chain_id=web3.eth.chain_id,
            cache=cache,
        )
        assert token.extra_data["cached"] == True
        cache.purge()

        #
        # Warm up multiple on dry cache
        #
        result = cache.load_token_details_with_multicall(
            chain_id=web3.eth.chain_id,
            web3factory=web3factory,
            addresses=addresses,
            max_workers=max_workers,
            display_progress=False,
        )
        assert result["tokens_read"] == 4
        assert "8453-0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913".lower() in cache
        assert "8453-0x4200000000000000000000000000000000000006".lower() in cache

        cache_data = cache["8453-0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913".lower()]
        assert cache_data["name"] == "USD Coin"
        assert cache_data["symbol"] == "USDC"
        assert cache_data["decimals"] == 6
        assert cache_data["supply"] > 1_000_000

        token = fetch_erc20_details(
            web3,
            token_address="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
            chain_id=web3.eth.chain_id,
            cache=cache,
        )
        assert token.extra_data["cached"] == True
    """

    DEFAULT_TOKEN_DISK_CACHE_PATH = Path("~/.cache/eth-defi-tokens.sqlite")

    def __init__(
        self,
        filename=DEFAULT_TOKEN_DISK_CACHE_PATH,
        max_str_length: int = 256,
    ):
        assert isinstance(filename, Path), f"We got {filename}"
        filename = filename.expanduser()
        dirname = filename.parent
        os.makedirs(dirname, exist_ok=True)
        self.max_str_length = max_str_length
        super().__init__(filename)

    def __repr__(self):
        return f"<TokenDiskCache file={self.filename} entries={len(self)}>"

    def encode_value(self, value: dict) -> Any:
        value["saved_at"] = native_datetime_utc_now().isoformat()
        return json.dumps(value)

    def decode_value(self, value: str) -> Any:
        return json.loads(value)

    def encode_multicalls(self, address: HexAddress) -> EncodedCall:
        """Generate multicalls for each token address"""

        symbol_call = EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="symbol()")[0:4],
            function="symbol",
            data=b"",
            extra_data=None,
        )
        yield symbol_call

        name_call = EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="name()")[0:4],
            function="name",
            data=b"",
            extra_data=None,
        )
        yield name_call

        decimals_call = EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="decimals()")[0:4],
            function="decimals",
            data=b"",
            extra_data=None,
        )
        yield decimals_call

        total_supply = EncodedCall.from_keccak_signature(
            address=address,
            signature=Web3.keccak(text="totalSupply()")[0:4],
            function="totalSupply",
            data=b"",
            extra_data=None,
        )
        yield total_supply

    def generate_calls(self, chain_id: int, addresses: list[HexAddress]) -> Iterable[EncodedCall]:
        for address in addresses:
            cache_key = TokenDetails.generate_cache_key(chain_id, address)
            if cache_key not in self:
                yield from self.encode_multicalls(address)
            else:
                logger.debug("Was already cached: %s", address)

    def create_cache_entry(self, call_results: dict[str, EncodedCallResult]) -> dict:
        """Map multicall results to token details data for one address"""
        entry = {}

        symbol_result = call_results["symbol"]
        entry["address"] = symbol_result.call.address
        if symbol_result.success and len(symbol_result.result) > 0:
            entry["symbol"] = convert_solidity_bytes_to_string(symbol_result.result, self.max_str_length)
        else:
            entry["symbol"] = None

        name_result = call_results["name"]
        if name_result.success and len(name_result.result) > 0:
            entry["name"] = convert_solidity_bytes_to_string(name_result.result, self.max_str_length)
        else:
            entry["name"] = None

        decimals_result = call_results["decimals"]
        if decimals_result.success:
            entry["decimals"] = convert_int256_bytes_to_int(decimals_result.result)
        else:
            entry["decimals"] = 0

        total_supply_result = call_results["totalSupply"]
        if total_supply_result.success:
            entry["supply"] = convert_int256_bytes_to_int(total_supply_result.result)
        else:
            entry["supply"] = None

        # A poisoned token that blows up stuff and
        # makes JSON serialisation impossible

        def _cap(x, _max=2**256):
            if type(x) == int:
                return min(x, _max)
            return x

        entry["decimals"] = _cap(entry["decimals"], _max=99)
        entry["supply"] = _cap(entry["supply"])

        return entry

    def load_token_details_with_multicall(
        self,
        chain_id: int,
        web3factory: Web3Factory,
        addresses: list[HexAddress],
        display_progress: str | bool = False,
        max_workers=8,
        block_identifier="latest",
        checkpoint: int = 32,
    ) -> TokenCacheWarmupResult:
        """Warm up cache and load token details for multiple"""

        assert type(chain_id) == int, "chain_id must be an integer"
        assert type(addresses) == list, "addresses must be a list of HexAddress"

        if type(display_progress) == str:
            progress_bar_desc = display_progress
        else:
            progress_bar_desc = f"Loading token metadata for {len(addresses)} addresses using {max_workers} workers"

        logger.info(f"Loading token metadata for {len(addresses)} addresses using {max_workers} workers")

        encoded_calls = list(self.generate_calls(chain_id, addresses))
        multicalls_done = len(encoded_calls)

        # Temporary work buffer were we count that all calls to the address have been made,
        # because results are dropping in one by one
        results_per_address: dict[HexAddress, dict] = defaultdict(dict)

        for call_result in read_multicall_chunked(
            chain_id,
            web3factory,
            encoded_calls,
            block_identifier=block_identifier,
            progress_bar_desc=progress_bar_desc,
            max_workers=max_workers,
        ):
            results_per_address[call_result.call.address][call_result.call.func_name] = call_result

        tokens_read = 0

        for address, result_per_address in results_per_address.items():
            cache_entry = self.create_cache_entry(result_per_address)
            key = TokenDetails.generate_cache_key(chain_id, address)
            cache_entry["chain_id"] = chain_id
            try:
                self[key] = cache_entry
            except ValueError as e:
                raise ValueError(f"Could not cache token {address} on chain {chain_id}: {e}, data: {cache_entry}") from e
            tokens_read += 1

            if tokens_read % checkpoint == 0:
                self.commit()

        logger.info(
            "Read %d tokens for chain %d with %d multicalls ",
            tokens_read,
            chain_id,
            multicalls_done,
        )

        self.commit()

        return TokenCacheWarmupResult(
            tokens_read=tokens_read,
            multicalls_done=multicalls_done,
        )
