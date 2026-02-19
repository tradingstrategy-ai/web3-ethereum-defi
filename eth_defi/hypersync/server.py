"""Hypersync server list.

- `Find source page here <https://docs.envio.dev/docs/HyperSync/hypersync-supported-networks>`__
"""

from web3 import Web3

#: HyperSync server URLs by chain id.
#: Updated 2026-02-19.
HYPERSYNC_SERVES = {
    1: "https://eth.hypersync.xyz",  # Ethereum Mainnet
    10: "https://optimism.hypersync.xyz",  # Optimism
    14: "https://flare.hypersync.xyz",  # Flare
    30: "https://rootstock.hypersync.xyz",  # Rootstock
    42: "https://lukso.hypersync.xyz",  # Lukso
    50: "https://xdc.hypersync.xyz",  # XDC
    51: "https://xdc-testnet.hypersync.xyz",  # XDC Testnet
    56: "https://bsc.hypersync.xyz",  # BSC
    97: "https://bsc-testnet.hypersync.xyz",  # BSC Testnet
    100: "https://gnosis.hypersync.xyz",  # Gnosis
    130: "https://unichain.hypersync.xyz",  # Unichain
    137: "https://polygon.hypersync.xyz",  # Polygon
    143: "https://monad.hypersync.xyz",  # Monad
    146: "https://sonic.hypersync.xyz",  # Sonic
    148: "https://shimmer-evm.hypersync.xyz",  # Shimmer EVM
    169: "https://manta.hypersync.xyz",  # Manta
    204: "https://opbnb.hypersync.xyz",  # opBNB
    250: "https://fantom.hypersync.xyz",  # Fantom
    252: "https://fraxtal.hypersync.xyz",  # Fraxtal
    255: "https://kroma.hypersync.xyz",  # Kroma
    288: "https://boba.hypersync.xyz",  # Boba
    324: "https://zksync.hypersync.xyz",  # zkSync
    480: "https://worldchain.hypersync.xyz",  # Worldchain
    841: "https://taraxa.hypersync.xyz",  # Taraxa
    999: "https://hyperliquid.hypersync.xyz",  # Hyperliquid
    1101: "https://polygon-zkevm.hypersync.xyz",  # Polygon zkEVM
    1135: "https://lisk.hypersync.xyz",  # Lisk
    1284: "https://moonbeam.hypersync.xyz",  # Moonbeam
    1328: "https://sei-testnet.hypersync.xyz",  # Sei Testnet
    1329: "https://sei.hypersync.xyz",  # Sei
    1750: "https://metall2.hypersync.xyz",  # Metall2
    1868: "https://soneium.hypersync.xyz",  # Soneium
    1923: "https://swell.hypersync.xyz",  # Swell
    2741: "https://abstract.hypersync.xyz",  # Abstract
    2818: "https://morph.hypersync.xyz",  # Morph
    4114: "https://citrea.hypersync.xyz",  # Citrea
    4200: "https://merlin.hypersync.xyz",  # Merlin
    4201: "https://lukso-testnet.hypersync.xyz",  # Lukso Testnet
    4326: "https://megaeth.hypersync.xyz",  # MegaETH
    5000: "https://mantle.hypersync.xyz",  # Mantle
    5115: "https://citrea-testnet.hypersync.xyz",  # Citrea Testnet
    5330: "https://superseed.hypersync.xyz",  # Superseed
    6342: "https://megaeth-testnet.hypersync.xyz",  # MegaETH Testnet
    6343: "https://megaeth-testnet2.hypersync.xyz",  # MegaETH Testnet2
    6767: "https://sentient.hypersync.xyz",  # Sentient
    7000: "https://zeta.hypersync.xyz",  # Zeta
    7560: "https://cyber.hypersync.xyz",  # Cyber
    8453: "https://base.hypersync.xyz",  # Base
    9745: "https://plasma.hypersync.xyz",  # Plasma
    10143: "https://monad-testnet.hypersync.xyz",  # Monad Testnet
    10200: "https://gnosis-chiado.hypersync.xyz",  # Gnosis Chiado
    14601: "https://sonic-testnet.hypersync.xyz",  # Sonic Testnet
    17000: "https://holesky.hypersync.xyz",  # Holesky
    33111: "https://curtis.hypersync.xyz",  # Curtis
    34443: "https://mode.hypersync.xyz",  # Mode
    36888: "https://ab.hypersync.xyz",  # Ab
    42161: "https://arbitrum.hypersync.xyz",  # Arbitrum
    42170: "https://arbitrum-nova.hypersync.xyz",  # Arbitrum Nova
    42220: "https://celo.hypersync.xyz",  # Celo
    43113: "https://fuji.hypersync.xyz",  # Fuji
    43114: "https://avalanche.hypersync.xyz",  # Avalanche
    48900: "https://zircuit.hypersync.xyz",  # Zircuit
    50104: "https://sophon.hypersync.xyz",  # Sophon
    57073: "https://ink.hypersync.xyz",  # Ink
    59144: "https://linea.hypersync.xyz",  # Linea
    80002: "https://polygon-amoy.hypersync.xyz",  # Polygon Amoy
    80094: "https://berachain.hypersync.xyz",  # Berachain
    81457: "https://blast.hypersync.xyz",  # Blast
    84532: "https://base-sepolia.hypersync.xyz",  # Base Sepolia
    88888: "https://chiliz.hypersync.xyz",  # Chiliz
    98866: "https://plume.hypersync.xyz",  # Plume
    421614: "https://arbitrum-sepolia.hypersync.xyz",  # Arbitrum Sepolia
    534352: "https://scroll.hypersync.xyz",  # Scroll
    560048: "https://hoodi.hypersync.xyz",  # Hoodi
    168587773: "https://blast-sepolia.hypersync.xyz",  # Blast Sepolia
    531050104: "https://sophon-testnet.hypersync.xyz",  # Sophon Testnet
    5042002: "https://arc-testnet.hypersync.xyz",  # Arc Testnet
    7225878: "https://saakuru.hypersync.xyz",  # Saakuru
    7777777: "https://zora.hypersync.xyz",  # Zora
    11155111: "https://sepolia.hypersync.xyz",  # Sepolia
    11155420: "https://optimism-sepolia.hypersync.xyz",  # Optimism Sepolia
    1184075182: "https://sentient-testnet.hypersync.xyz",  # Sentient Testnet
    1313161554: "https://aurora.hypersync.xyz",  # Aurora
    1660990954: "https://status-sepolia.hypersync.xyz",  # Status Sepolia
    1666600000: "https://harmony-shard-0.hypersync.xyz",  # Harmony Shard 0
}


def get_hypersync_server(web3: Web3 | int, allow_missing=False) -> str | None:
    """Get HyperSync server for Web3 instance or by chain id"""

    if type(web3) == int:
        chain_id = web3
    else:
        chain_id = web3.eth.chain_id
    server = HYPERSYNC_SERVES.get(chain_id)

    if not allow_missing:
        assert server, f"Does not know HyperSync server for chain: {chain_id}"
    else:
        if not server:
            return None

    return server


def is_hypersync_supported_chain(web3: Web3 | int) -> bool:
    """Is the chain supported by HyperSync?

    Based on our internal server mapping.
    """

    if type(web3) == int:
        chain_id = web3
    else:
        chain_id = web3.eth.chain_id
    return chain_id in HYPERSYNC_SERVES
