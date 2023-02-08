"""Aave v3 constants."""
import json
import os
from typing import NamedTuple

# Sources:
# https://docs.aave.com/developers/deployed-contracts/v3-mainnet/polygon
# https://docs.aave.com/developers/deployed-contracts/v3-mainnet/optimism
# https://docs.aave.com/developers/deployed-contracts/v3-mainnet/arbitrum
# https://docs.aave.com/developers/deployed-contracts/v3-mainnet/fantom
# https://docs.aave.com/developers/deployed-contracts/v3-mainnet/avalanche
# https://docs.aave.com/developers/deployed-contracts/v3-mainnet/harmony


class AaveNetwork(NamedTuple):
    # Network name
    name: str

    # Aave v3 pool address
    pool_address: str

    # Aave v3 pool configurator address
    pool_configurator_address: str

    # Block number when the pool was created
    pool_created_at_block: int

    # Token contract information
    token_contracts: dict[str, "AaveToken"]


class AaveToken(NamedTuple):
    # Address of the token contract
    token_address: str

    # Address of the AToken (deposit) contract
    deposit_address: str

    # Address of the VariableDebtToken (variable borrow rate) contract
    variable_borrow_address: str

    # Address of the StableDebtToken (stable borrow rate) contract
    stable_borrow_address: str

    # Block number when the token was created
    token_created_at_block: int


# Map chain identifiers to Aave network parameters - autodetect parameters based on the Web3 provider's chain id
# Note that the pool addresses are proxy addresses, not the actual contract addresses (but you can use them with the contract ABI)

AAVE_V3_NETWORK_CHAINS: dict[int, str] = {
    137: "polygon",
    10: "optimism",
    42161: "arbitrum",
    250: "fantom",
    43114: "avalanche",
    1666600000: "harmony",
}

# Note that in Polygon, Aave v2 sends identical ReserveDataUpdated events from contract 0x8dff5e27ea6b7ac08ebfdf9eb090f32ee9a30fcf,
# while we are watching v3 events from contract 0x794a61358D6845594F94dc1DB02A252b5b4814aD only. So we need to filter events by
# the pool_address configured here.
# Read more at: https://docs.aave.com/developers/deployed-contracts/v3-mainnet

AAVE_V3_NETWORKS: dict[str, AaveNetwork] = {
    # Polygon Mainnet
    "polygon": AaveNetwork(
        name="Polygon",
        pool_address="0x794a61358D6845594F94dc1DB02A252b5b4814aD",
        pool_configurator_address="0x8145eddDf43f50276641b55bd3AD95944510021E",
        pool_created_at_block=25826028,
        token_contracts={
            # Aave token contracts defined in the Polygon network
            "AAVE": AaveToken(token_address="0xD6DF932A45C0f255f85145f286eA0b292B21C90B", deposit_address="0xf329e36C7bF6E5E86ce2150875a84Ce77f477375", variable_borrow_address="0xE80761Ea617F66F96274eA5e8c37f03960ecC679", stable_borrow_address="0xfAeF6A702D15428E588d4C0614AEFb4348D83D48", token_created_at_block=11666003),  # AAVE
            "DAI": AaveToken(token_address="0x8f3Cf7ad23Cd3CaDbD9735AFf958023239c6A063", deposit_address="0x82E64f49Ed5EC1bC6e43DAD4FC8Af9bb3A2312EE", variable_borrow_address="0x8619d80FB0141ba7F184CbF22fd724116D9f7ffC", stable_borrow_address="0xd94112B5B62d53C9402e7A60289c6810dEF1dC9B", token_created_at_block=4362007),  # DAI
            "USDT": AaveToken(token_address="0xc2132D05D31c914a87C6611C10748AEb04B58e8F", deposit_address="0x6ab707Aca953eDAeFBc4fD23bA73294241490620", variable_borrow_address="0xfb00AC187a8Eb5AFAE4eACE434F493Eb62672df7", stable_borrow_address="0x70eFfc565DB6EEf7B927610155602d31b670e802", token_created_at_block=4196335),  # USDT
            "LINK": AaveToken(token_address="0x53E0bca35eC356BD5ddDFebbD1Fc0fD03FaBad39", deposit_address="0x191c10Aa4AF7C30e871E70C95dB0E4eb77237530", variable_borrow_address="0x953A573793604aF8d41F306FEb8274190dB4aE0e", stable_borrow_address="0x89D976629b7055ff1ca02b927BA3e020F22A44e4", token_created_at_block=3835428),  # LINK
            "WMATIC": AaveToken(token_address="0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270", deposit_address="0x6d80113e533a2C0fe82EaBD35f1875DcEA89Ea97", variable_borrow_address="0x4a1c3aD6Ed28a636ee1751C69071f6be75DEb8B8", stable_borrow_address="0xF15F26710c827DDe8ACBA678682F3Ce24f2Fb56E", token_created_at_block=4931456),  # WMATIC
            "USDC": AaveToken(token_address="0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174", deposit_address="0x625E7708f30cA75bfd92586e17077590C60eb4cD", variable_borrow_address="0xFCCf3cAbbe80101232d343252614b6A3eE81C989", stable_borrow_address="0x307ffe186F84a3bc2613D1eA417A5737D69A7007", token_created_at_block=5013591),  # USDC
            "AGEUR": AaveToken(token_address="0xE0B52e49357Fd4DAf2c15e02058DCE6BC0057db4", deposit_address="0x8437d7c167dfb82ed4cb79cd44b7a32a1dd95c77", variable_borrow_address="0x3ca5fa07689f266e907439afd1fbb59c44fe12f6", stable_borrow_address="0x40b4baecc69b882e8804f9286b12228c27f8c9bf", token_created_at_block=21711550),  # AGEUR
            "EURS": AaveToken(token_address="0xE111178A87A3BFf0c8d18DECBa5798827539Ae99", deposit_address="0x38d693ce1df5aadf7bc62595a37d667ad57922e5", variable_borrow_address="0x5d557b07776d12967914379c71a1310e917c7555", stable_borrow_address="0x8a9fde6925a839f6b1932d16b36ac026f8d3fbdb", token_created_at_block=12153223),  # EURS
            "WBTC": AaveToken(token_address="0x1BFD67037B42Cf73acF2047067bd4F2C47D9BfD6", deposit_address="0x078f358208685046a11C85e8ad32895DED33A249", variable_borrow_address="0x92b42c66840C7AD907b4BF74879FF3eF7c529473", stable_borrow_address="0x633b207Dd676331c413D4C013a6294B0FE47cD0e", token_created_at_block=4196820),  # WBTC
            "WETH": AaveToken(token_address="0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619", deposit_address="0xe50fA9b3c56FfB159cB0FCA61F5c9D750e8128c8", variable_borrow_address="0x0c84331e39d6658Cd6e6b9ba04736cC4c4734351", stable_borrow_address="0xD8Ad37849950903571df17049516a5CD4cbE55F6", token_created_at_block=3678215),  # WETH
            "CRV": AaveToken(token_address="0x172370d5Cd63279eFa6d502DAB29171933a610AF", deposit_address="0x513c7e3a9c69ca3e22550ef58ac1c0088e918fff", variable_borrow_address="0x77ca01483f379e58174739308945f044e1a764dc", stable_borrow_address="0x08cb71192985e936c7cd166a8b268035e400c3c3", token_created_at_block=11828257),  # CRV
            "SUSHI": AaveToken(token_address="0x0b3F868E0BE5597D5DB7fEB59E1CADBb0fdDa50a", deposit_address="0xc45a479877e1e9dfe9fcd4056c699575a1045daa", variable_borrow_address="0x34e2ed44ef7466d5f9e0b782b5c08b57475e7907", stable_borrow_address="0x78246294a4c6fbf614ed73ccc9f8b875ca8ee841", token_created_at_block=10461221),  # SUSHI
            "GHST": AaveToken(token_address="0x385Eeac5cB85A38A9a07A70c73e0a3271CfB54A7", deposit_address="0x8eb270e296023e9d92081fdf967ddd7878724424", variable_borrow_address="0xce186f6cccb0c955445bb9d10c59cae488fea559", stable_borrow_address="0x3ef10dff4928279c004308ebadc4db8b7620d6fc", token_created_at_block=9249989),  # GHST
            "JEUR": AaveToken(token_address="0x4e3Decbb3645551B8A19f0eA1678079FCB33fB4c", deposit_address="0x6533afac2e7bccb20dca161449a13a32d391fb00", variable_borrow_address="0x44705f578135cc5d703b4c9c122528c73eb87145", stable_borrow_address="0x6b4b37618d85db2a7b469983c888040f7f05ea3d", token_created_at_block=17934387),  # JEUR
            "DPI": AaveToken(token_address="0x85955046DF4668e1DD369D2DE9f3AEB98DD2A369", deposit_address="0x724dc807b04555b71ed48a6896b6f41593b8c637", variable_borrow_address="0xf611aeb5013fd2c0511c9cd55c7dc5c1140741a6", stable_borrow_address="0xdc1fad70953bb3918592b6fcc374fe05f5811b6a", token_created_at_block=11252515),  # DPI
            "BAL": AaveToken(token_address="0x9a71012B13CA4d3D0Cdc72A177DF3ef03b0E76A3", deposit_address="0x8ffdf2de812095b1d19cb146e4c004587c0a0692", variable_borrow_address="0xa8669021776bc142dfca87c21b4a52595bcbb40a", stable_borrow_address="0xa5e408678469d23efdb7694b1b0a85bb0669e8bd", token_created_at_block=14390111),  # BAL
        },
    ),
    # Optimism Mainnet (XXX TODO - add more tokens)
    "optimism": AaveNetwork(
        name="Optimism",
        pool_address="0x794a61358D6845594F94dc1DB02A252b5b4814aD",
        pool_configurator_address="0x8145eddDf43f50276641b55bd3AD95944510021E",
        pool_created_at_block=4365693,  # https://optimistic.etherscan.io/tx/0x0cbeb42d5aca9f716a88107327ccf30d543c2d89d2d8a1071ae590430d360503
        token_contracts={
            # Aave token contracts defined in the Optimism network
            "AAVE": AaveToken(token_address="0x76FB31fb4af56892A25e32cFC43De717950c9278", deposit_address="0xf329e36C7bF6E5E86ce2150875a84Ce77f477375", variable_borrow_address="0xE80761Ea617F66F96274eA5e8c37f03960ecC679", stable_borrow_address="0xfAeF6A702D15428E588d4C0614AEFb4348D83D48", token_created_at_block=4130073),  # https://optimistic.etherscan.io/address/0x76fb31fb4af56892a25e32cfc43de717950c9278
            "WETH": AaveToken(token_address="0x4200000000000000000000000000000000000006", deposit_address="0xe50fA9b3c56FfB159cB0FCA61F5c9D750e8128c8", variable_borrow_address="0x0c84331e39d6658Cd6e6b9ba04736cC4c4734351", stable_borrow_address="0xD8Ad37849950903571df17049516a5CD4cbE55F6", token_created_at_block=0),  # https://optimistic.etherscan.io/address/0x4200000000000000000000000000000000000006
        },
    ),
    # Arbitrum Mainnet (XXX TODO - add more tokens)
    "arbitrum": AaveNetwork(
        name="Arbitrum",
        pool_address="0x794a61358D6845594F94dc1DB02A252b5b4814aD",
        pool_configurator_address="0x8145eddDf43f50276641b55bd3AD95944510021E",
        pool_created_at_block=7742429,  # https://arbiscan.io/tx/0xf73ad5eb856faaf2eaf6e8a0823d2964e80ca4ad7cc2031f0606b158d236b5a9
        token_contracts={
            # Aave token contracts defined in the Arbitrum network
            "AAVE": AaveToken(token_address="0xba5DdD1f9d7F570dc94a51479a000E3BCE967196", deposit_address="0xf329e36C7bF6E5E86ce2150875a84Ce77f477375", variable_borrow_address="0xE80761Ea617F66F96274eA5e8c37f03960ecC679", stable_borrow_address="0xfAeF6A702D15428E588d4C0614AEFb4348D83D48", token_created_at_block=7410775),  # https://arbiscan.io/address/0xba5ddd1f9d7f570dc94a51479a000e3bce967196
            "WETH": AaveToken(token_address="0x82aF49447D8a07e3bd95BD0d56f35241523fBab1", deposit_address="0xe50fA9b3c56FfB159cB0FCA61F5c9D750e8128c8", variable_borrow_address="0x0c84331e39d6658Cd6e6b9ba04736cC4c4734351", stable_borrow_address="0xD8Ad37849950903571df17049516a5CD4cbE55F6", token_created_at_block=55),  # https://arbiscan.io/address/0x82aF49447D8a07e3bd95BD0d56f35241523fBab1
        },
    ),
    # Fantom Mainnet (XXX TODO - add more tokens)
    "fantom": AaveNetwork(
        name="Fantom",
        pool_address="0x794a61358D6845594F94dc1DB02A252b5b4814aD",
        pool_configurator_address="0x8145eddDf43f50276641b55bd3AD95944510021E",
        pool_created_at_block=33142113,  # https://ftmscan.com/tx/0xd2bf0aa79d1ccb312939bd584921cb24e111d01b672029931e7b657600146ab6
        token_contracts={
            # Aave token contracts defined in the Fantom network
            "AAVE": AaveToken(token_address="0x6a07A792ab2965C72a5B8088d3a069A7aC3a993B", deposit_address="0xf329e36C7bF6E5E86ce2150875a84Ce77f477375", variable_borrow_address="0xE80761Ea617F66F96274eA5e8c37f03960ecC679", stable_borrow_address="0xfAeF6A702D15428E588d4C0614AEFb4348D83D48", token_created_at_block=2301053),  # https://ftmscan.com/address/0x6a07a792ab2965c72a5b8088d3a069a7ac3a993b
            "WETH": AaveToken(token_address="0x74b23882a30290451A17c44f4F05243b6b58C76d", deposit_address="0xe50fA9b3c56FfB159cB0FCA61F5c9D750e8128c8", variable_borrow_address="0x0c84331e39d6658Cd6e6b9ba04736cC4c4734351", stable_borrow_address="0xD8Ad37849950903571df17049516a5CD4cbE55F6", token_created_at_block=2300940),  # https://ftmscan.com/address/0x74b23882a30290451a17c44f4f05243b6b58c76d
        },
    ),
    # Avalanche Mainnet (XXX TODO - add more tokens)
    "avalanche": AaveNetwork(
        name="Avalanche",
        pool_address="0x794a61358D6845594F94dc1DB02A252b5b4814aD",
        pool_configurator_address="0x8145eddDf43f50276641b55bd3AD95944510021E",
        pool_created_at_block=11970506,  # https://snowtrace.io/tx/0x300a7d036cddf30aa239f75afa5770efa557fdaf7fc67b79d1f0a183df619377
        token_contracts={
            # Aave token contracts defined in the Avalanche network
            "AAVE": AaveToken(token_address="0x63a72806098Bd3D9520cC43356dD78afe5D386D9", deposit_address="0xf329e36C7bF6E5E86ce2150875a84Ce77f477375", variable_borrow_address="0xE80761Ea617F66F96274eA5e8c37f03960ecC679", stable_borrow_address="0xfAeF6A702D15428E588d4C0614AEFb4348D83D48", token_created_at_block=2749886),  # https://snowtrace.io/address/0x63a72806098bd3d9520cc43356dd78afe5d386d9
            "WETH": AaveToken(token_address="0x49D5c2BdFfac6CE2BFdB6640F4F80f226bc10bAB", deposit_address="0xe50fA9b3c56FfB159cB0FCA61F5c9D750e8128c8", variable_borrow_address="0x0c84331e39d6658Cd6e6b9ba04736cC4c4734351", stable_borrow_address="0xD8Ad37849950903571df17049516a5CD4cbE55F6", token_created_at_block=2749895),  # https://snowtrace.io/address/0x49D5c2BdFfac6CE2BFdB6640F4F80f226bc10bAB
        },
    ),
    # Harmony Mainnet Shard 0 (XXX TODO - add more tokens)
    "harmony": AaveNetwork(
        name="Harmony",
        pool_address="0x794a61358D6845594F94dc1DB02A252b5b4814aD",
        pool_configurator_address="0x8145eddDf43f50276641b55bd3AD95944510021E",
        pool_created_at_block=23930374,  # https://explorer.harmony.one/tx/0xd89653b18dfe7558efd23569acc3cd6c8f51ee6b4005fc4bd65f084cad9caae9
        token_contracts={
            # Aave token contracts defined in the Harmony network
            "AAVE": AaveToken(token_address="0xcF323Aad9E522B93F11c352CaA519Ad0E14eB40F", deposit_address="0xf329e36C7bF6E5E86ce2150875a84Ce77f477375", variable_borrow_address="0xE80761Ea617F66F96274eA5e8c37f03960ecC679", stable_borrow_address="0xfAeF6A702D15428E588d4C0614AEFb4348D83D48", token_created_at_block=5419567),  # https://explorer.harmony.one/address/0xcF323Aad9E522B93F11c352CaA519Ad0E14eB40F
            "WETH": AaveToken(token_address="0x6983D1E6DEf3690C4d616b13597A09e6193EA013", deposit_address="0xe50fA9b3c56FfB159cB0FCA61F5c9D750e8128c8", variable_borrow_address="0x0c84331e39d6658Cd6e6b9ba04736cC4c4734351", stable_borrow_address="0xD8Ad37849950903571df17049516a5CD4cbE55F6", token_created_at_block=7907933),  # https://explorer.harmony.one/address/0x6983D1E6DEf3690C4d616b13597A09e6193EA013
        },
    ),
    # Ethereum Mainnet
    # https://etherscan.io/tx/0xf2089a3c2a24f512214691c5a51dd57d1a5545758486360f3a1e24547723525d
    "ethereum": AaveNetwork(
        name="Ethereum",
        pool_address="0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",
        pool_configurator_address="0x64b761D848206f447Fe2dd461b0c635Ec39EbB27",
        pool_created_at_block=16291127,
        token_contracts={},
    ),
}

AAVE_V3_DEPOSIT_ADDRESS_TOKENS: dict[str, str] = {}  # autofill later


# Helper functions for reading JSON-RPC URLs and account addresses from an optional aave.json file.
# If you use it (instead of entering the values interactively), it should look like this:
# {
#   "json_rpc_url": "https://address-to-your-json-rpc-server,
#   "account_address": "address-of-your-account"
# }


def aave_v3_get_json_rpc_url() -> str | None:
    # Allow configuring the JSON-RPC URL via aave.json in current directory
    # If not present, user will be asked to input the URL
    if os.path.exists("./aave.json"):
        aave_config = json.load(open("./aave.json"))
        return aave_config["json_rpc_url"]


def aave_v3_get_account_address() -> str | None:
    # Allow configuring an account address via aave.json in current directory
    # If not present, user will be asked to input the account
    if os.path.exists("./aave.json"):
        aave_config = json.load(open("./aave.json"))
        return aave_config["account_address"]


def aave_v3_get_network_by_chain_id(chain_id: int) -> AaveNetwork:
    # Auto-detect the network based on the chain id
    if chain_id not in AAVE_V3_NETWORK_CHAINS:
        raise ValueError(f"Unsupported chain id: {chain_id}")
    network_slug = AAVE_V3_NETWORK_CHAINS[chain_id]
    aave_network = AAVE_V3_NETWORKS[network_slug]
    return aave_network


def aave_v3_get_token_name_by_deposit_address(deposit_address: str) -> str | None:
    # Get a token name by the AToken deposit contract address
    return AAVE_V3_DEPOSIT_ADDRESS_TOKENS.get(deposit_address, None)


def _autofill_token_addresses():
    for network in AAVE_V3_NETWORKS.values():
        for token_name, token in network.token_contracts.items():
            AAVE_V3_DEPOSIT_ADDRESS_TOKENS[token.token_address] = token_name


# Initialization
_autofill_token_addresses()
