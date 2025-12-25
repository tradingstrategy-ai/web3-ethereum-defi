"""Hypersync server list.

- `Find source page here <https://docs.envio.dev/docs/HyperSync/hypersync-supported-networks>`__
"""

from web3 import Web3

#: Converted with Grok.
#: Mess. Partially cleaned.
HYPERSYNC_SERVES = {
    2741: {"Network Name": "Abstract", "URL": "https://abstract.hypersync.xyz or https://2741.hypersync.xyz", "Tier": "🪨", "Supports Traces": False},
    42161: {"Network Name": "Arbitrum", "URL": "https://arbitrum.hypersync.xyz or https://42161.hypersync.xyz", "Tier": "🏅", "Supports Traces": False},
    42170: {"Network Name": "Arbitrum Nova", "URL": "https://arbitrum-nova.hypersync.xyz or https://42170.hypersync.xyz", "Tier": "🥉", "Supports Traces": False},
    421614: {"Network Name": "Arbitrum Sepolia", "URL": "https://arbitrum-sepolia.hypersync.xyz or https://421614.hypersync.xyz", "Tier": "🎒", "Supports Traces": False},
    1313161554: {"Network Name": "Aurora", "URL": "https://aurora.hypersync.xyz or https://1313161554.hypersync.xyz", "Tier": "🪨", "Supports Traces": False},
    43114: {"Network Name": "Avalanche", "URL": "https://avalanche.hypersync.xyz or https://43114.hypersync.xyz", "Tier": "🥉", "Supports Traces": False},
    8453: {"Network Name": "Base", "URL": "https://base.hypersync.xyz or https://8453.hypersync.xyz", "Tier": "🏅", "Supports Traces": False},
    84532: {"Network Name": "Base Sepolia", "URL": "https://base-sepolia.hypersync.xyz or https://84532.hypersync.xyz", "Tier": "🎒", "Supports Traces": False},
    80094: {"Network Name": "Berachain", "URL": "https://berachain.hypersync.xyz or https://80094.hypersync.xyz", "Tier": "🪨", "Supports Traces": False},
    80084: {"Network Name": "Berachain Bartio", "URL": "https://berachain-bartio.hypersync.xyz or https://80084.hypersync.xyz", "Tier": "🎒", "Supports Traces": False},
    81457: {"Network Name": "Blast", "URL": "https://blast.hypersync.xyz or https://81457.hypersync.xyz", "Tier": "🥉", "Supports Traces": False},
    168587773: {"Network Name": "Blast Sepolia", "URL": "https://blast-sepolia.hypersync.xyz or https://168587773.hypersync.xyz", "Tier": "🎒", "Supports Traces": False},
    288: {"Network Name": "Boba", "URL": "https://boba.hypersync.xyz or https://288.hypersync.xyz", "Tier": "🪨", "Supports Traces": False},
    56: {"Network Name": "Bsc", "URL": "https://bsc.hypersync.xyz or https://56.hypersync.xyz", "Tier": "🥉", "Supports Traces": False},
    97: {"Network Name": "Bsc Testnet", "URL": "https://bsc-testnet.hypersync.xyz or https://97.hypersync.xyz", "Tier": "🎒", "Supports Traces": False},
    42220: {"Network Name": "Celo", "URL": "https://celo.hypersync.xyz or https://42220.hypersync.xyz", "Tier": "🪨", "Supports Traces": False},
    8888: {"Network Name": "Chiliz", "URL": "https://chiliz.hypersync.xyz or https://8888.hypersync.xyz", "Tier": "🥉", "Supports Traces": False},
    5115: {"Network Name": "Citrea Testnet", "URL": "https://citrea-testnet.hypersync.xyz or https://5115.hypersync.xyz", "Tier": "🪨", "Supports Traces": False},
    7560: {"Network Name": "Cyber", "URL": "https://cyber.hypersync.xyz or https://7560.hypersync.xyz", "Tier": "🪨", "Supports Traces": False},
    46: {"Network Name": "Darwinia", "URL": "https://darwinia.hypersync.xyz or https://46.hypersync.xyz", "Tier": "🪨", "Supports Traces": True},
    1: {"Network Name": "Ethereum Mainnet", "URL": "https://eth.hypersync.xyz or https://1.hypersync.xyz", "Tier": "🏅", "Supports Traces": True},
    283027429: {"Network Name": "Extrabud", "URL": "https://extrabud.hypersync.xyz or https://283027429.hypersync.xyz", "Tier": "🏗️", "Supports Traces": False},
    250: {"Network Name": "Fantom", "URL": "https://fantom.hypersync.xyz or https://250.hypersync.xyz", "Tier": "🥉", "Supports Traces": False},
    14: {"Network Name": "Flare", "URL": "https://flare.hypersync.xyz or https://14.hypersync.xyz", "Tier": "🪨", "Supports Traces": False},
    252: {"Network Name": "Fraxtal", "URL": "https://fraxtal.hypersync.xyz or https://252.hypersync.xyz", "Tier": "🪨", "Supports Traces": False},
    43113: {"Network Name": "Fuji", "URL": "https://fuji.hypersync.xyz or https://43113.hypersync.xyz", "Tier": "🎒", "Supports Traces": False},
    696969: {"Network Name": "Galadriel Devnet", "URL": "https://galadriel-devnet.hypersync.xyz or https://696969.hypersync.xyz", "Tier": "🎒", "Supports Traces": False},
    100: {"Network Name": "Gnosis", "URL": "https://gnosis.hypersync.xyz or https://100.hypersync.xyz", "Tier": "🏅", "Supports Traces": False},  # Note: Gnosis appears twice with different URLs/Tiers
    10200: {"Network Name": "Gnosis Chiado", "URL": "https://gnosis-chiado.hypersync.xyz or https://10200.hypersync.xyz", "Tier": "🎒", "Supports Traces": False},
    # 100: {"Network Name": "Gnosis Traces", "URL": "https://gnosis-traces.hypersync.xyz or https://100.hypersync.xyz", "Tier": "🥉", "Supports Traces": False},  # Duplicate Network ID
    1666600000: {"Network Name": "Harmony Shard 0", "URL": "https://harmony-shard-0.hypersync.xyz or https://1666600000.hypersync.xyz", "Tier": "🪨", "Supports Traces": False},
    17000: {"Network Name": "Holesky", "URL": "https://holesky.hypersync.xyz or https://17000.hypersync.xyz", "Tier": "🎒", "Supports Traces": False},  # Note: Holesky appears twice with different URLs
    # 17000: {"Network Name": "Holesky Token Test", "URL": "https://holesky-token-test.hypersync.xyz or https://17000.hypersync.xyz", "Tier": "🔒", "Supports Traces": False},  # Duplicate Network ID
    999: {"Network Name": "Hyperliquid", "URL": "https://hyperliquid.hypersync.xyz or https://645749.hypersync.xyz", "Tier": "🥉", "Supports Traces": False},
    645748: {"Network Name": "Hyperliquid Temp", "URL": "https://hyperliquid-temp.hypersync.xyz or https://645748.hypersync.xyz", "Tier": "🥉", "Supports Traces": False},
    57073: {"Network Name": "Ink", "URL": "https://ink.hypersync.xyz or https://57073.hypersync.xyz", "Tier": "🪨", "Supports Traces": False},
    16858666: {"Network Name": "Internal Test Chain", "URL": "https://internal-test-chain.hypersync.xyz or https://16858666.hypersync.xyz", "Tier": "🔒", "Supports Traces": False},
    255: {"Network Name": "Kroma", "URL": "https://kroma.hypersync.xyz or https://255.hypersync.xyz", "Tier": "🪨", "Supports Traces": False},
    59144: {"Network Name": "Linea", "URL": "https://linea.hypersync.xyz or https://59144.hypersync.xyz", "Tier": "🥉", "Supports Traces": False},
    1135: {"Network Name": "Lisk", "URL": "https://lisk.hypersync.xyz or https://1135.hypersync.xyz", "Tier": "🥉", "Supports Traces": False},
    42: {"Network Name": "Lukso", "URL": "https://lukso.hypersync.xyz or https://42.hypersync.xyz", "Tier": "🪨", "Supports Traces": False},
    4201: {"Network Name": "Lukso Testnet", "URL": "https://lukso-testnet.hypersync.xyz or https://4201.hypersync.xyz", "Tier": "🎒", "Supports Traces": False},
    169: {"Network Name": "Manta", "URL": "https://manta.hypersync.xyz or https://169.hypersync.xyz", "Tier": "🪨", "Supports Traces": False},
    5000: {"Network Name": "Mantle", "URL": "https://mantle.hypersync.xyz or https://5000.hypersync.xyz", "Tier": "🪨", "Supports Traces": False},
    6342: {"Network Name": "Megaeth Testnet", "URL": "https://megaeth-testnet.hypersync.xyz or https://6342.hypersync.xyz", "Tier": "🪨", "Supports Traces": False},
    4200: {"Network Name": "Merlin", "URL": "https://merlin.hypersync.xyz or https://4200.hypersync.xyz", "Tier": "🪨", "Supports Traces": False},
    1750: {"Network Name": "Metall2", "URL": "https://metall2.hypersync.xyz or https://1750.hypersync.xyz", "Tier": "🪨", "Supports Traces": False},
    1088: {"Network Name": "Metis", "URL": "https://metis.hypersync.xyz or https://1088.hypersync.xyz", "Tier": "🪨", "Supports Traces": False},
    17864: {"Network Name": "Mev Commit", "URL": "https://mev-commit.hypersync.xyz or https://17864.hypersync.xyz", "Tier": "🪨", "Supports Traces": False},
    34443: {"Network Name": "Mode", "URL": "https://mode.hypersync.xyz or https://34443.hypersync.xyz", "Tier": "🪨", "Supports Traces": False},
    10143: {"Network Name": "Monad Testnet", "URL": "https://monad-testnet.hypersync.xyz or https://10143.hypersync.xyz", "Tier": "🏅", "Supports Traces": False},
    1287: {"Network Name": "Moonbase Alpha", "URL": "https://moonbase-alpha.hypersync.xyz or https://1287.hypersync.xyz", "Tier": "🪨", "Supports Traces": False},
    1284: {"Network Name": "Moonbeam", "URL": "https://moonbeam.hypersync.xyz or https://1284.hypersync.xyz", "Tier": "🥈", "Supports Traces": False},
    2818: {"Network Name": "Morph", "URL": "https://morph.hypersync.xyz or https://2818.hypersync.xyz", "Tier": "🪨", "Supports Traces": False},
    2810: {"Network Name": "Morph Holesky", "URL": "https://morph-holesky.hypersync.xyz or https://2810.hypersync.xyz", "Tier": "🎒", "Supports Traces": False},
    204: {"Network Name": "Opbnb", "URL": "https://opbnb.hypersync.xyz or https://204.hypersync.xyz", "Tier": "🪨", "Supports Traces": False},
    10: {"Network Name": "Optimism", "URL": "https://optimism.hypersync.xyz or https://10.hypersync.xyz", "Tier": "🏅", "Supports Traces": False},
    11155420: {"Network Name": "Optimism Sepolia", "URL": "https://optimism-sepolia.hypersync.xyz or https://11155420.hypersync.xyz", "Tier": "🎒", "Supports Traces": False},
    50002: {"Network Name": "Pharos Devnet", "URL": "https://pharos-devnet.hypersync.xyz"},
    137: {"Network Name": "Polygon", "URL": "https://polygon.hypersync.xyz or https://137.hypersync.xyz", "Tier": "🏅", "Supports Traces": False},
    80002: {"Network Name": "Polygon Amoy", "URL": "https://polygon-amoy.hypersync.xyz or https://80002.hypersync.xyz", "Tier": "🥉", "Supports Traces": False},
    1101: {"Network Name": "Polygon zkEVM", "URL": "https://polygon-zkevm.hypersync.xyz or https://1101.hypersync.xyz", "Tier": "🪨", "Supports Traces": False},
    30: {"Network Name": "Rootstock", "URL": "https://rootstock.hypersync.xyz or https://30.hypersync.xyz", "Tier": "🥉", "Supports Traces": False},
    7225878: {"Network Name": "Saakuru", "URL": "https://saakuru.hypersync.xyz or https://7225878.hypersync.xyz", "Tier": "🪨", "Supports Traces": False},
    534352: {"Network Name": "Scroll", "URL": "https://scroll.hypersync.xyz or https://534352.hypersync.xyz", "Tier": "🥉", "Supports Traces": False},
    11155111: {"Network Name": "Sepolia", "URL": "https://sepolia.hypersync.xyz or https://11155111.hypersync.xyz", "Tier": "🥉", "Supports Traces": False},
    148: {"Network Name": "Shimmer Evm", "URL": "https://shimmer-evm.hypersync.xyz or https://148.hypersync.xyz", "Tier": "🪨", "Supports Traces": False},
    1868: {"Network Name": "Soneium", "URL": "https://soneium.hypersync.xyz or https://1868.hypersync.xyz", "Tier": "🪨", "Supports Traces": False},
    50104: {"Network Name": "Sophon", "URL": "https://sophon.hypersync.xyz or https://50104.hypersync.xyz", "Tier": "🥉", "Supports Traces": False},
    531050104: {"Network Name": "Sophon Testnet", "URL": "https://sophon-testnet.hypersync.xyz or https://531050104.hypersync.xyz", "Tier": "🎒", "Supports Traces": False},
    130: {"Network Name": "Unichain", "URL": "https://unichain.hypersync.xyz or https://130.hypersync.xyz", "Tier": "🪨", "Supports Traces": False},
    1301: {"Network Name": "Unichain Sepolia", "URL": "https://unichain-sepolia.hypersync.xyz or https://1301.hypersync.xyz", "Tier": "🎒", "Supports Traces": False},
    50: {"Network Name": "Xdc", "URL": "https://xdc.hypersync.xyz or https://50.hypersync.xyz", "Tier": "🥈", "Supports Traces": False},
    51: {"Network Name": "Xdc Testnet", "URL": "https://xdc-testnet.hypersync.xyz or https://51.hypersync.xyz", "Tier": "🎒", "Supports Traces": False},
    7000: {"Network Name": "Zeta", "URL": "https://zeta.hypersync.xyz or https://7000.hypersync.xyz", "Tier": "🪨", "Supports Traces": False},
    48900: {"Network Name": "Zircuit", "URL": "https://zircuit.hypersync.xyz or https://48900.hypersync.xyz", "Tier": "🪨", "Supports Traces": False},
    324: {"Network Name": "ZKsync", "URL": "https://zksync.hypersync.xyz or https://324.hypersync.xyz", "Tier": "🥉", "Supports Traces": False},
    7777777: {"Network Name": "Zora", "URL": "https://zora.hypersync.xyz or https://7777777.hypersync.xyz", "Tier": "🪨", "Supports Traces": False},
    146: {"Network Name": "Sonic", "URL": "https://sonic.hypersync.xyz or https://146.hypersync.xyz", "Tier": "🪨", "Supports Traces": False},
    143: {"Network Name": "Monad", "URL": "https://monad.hypersync.xyz or https://143.hypersync.xyz", "Tier": "🪨", "Supports Traces": False},
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

    urls = server["URL"]
    return urls.split(" ")[0]


def is_hypersync_supported_chain(web3: Web3 | int) -> bool:
    """Is the chain supported by HyperSync?

    Based on our internal server mapping.
    """

    if type(web3) == int:
        chain_id = web3
    else:
        chain_id = web3.eth.chain_id
    return chain_id in HYPERSYNC_SERVES
