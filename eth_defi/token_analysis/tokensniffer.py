"""TokenSniffer API.

- Python wrapper for TokenSniffer API

- Allows to fetch  ERC-20 risk score and other automatically analysed metadata to determine if a token is some sort of a scam or not

- For usage see :py:class:`CachedTokenSniffer` class

- TokenSniffer API is $99/month, 500 requests a day

- `Read TokeSniffer REST API documentation <https://tokensniffer.readme.io/reference/introduction>`__

- For more examples see `Getting started repo <https://github.com/tradingstrategy-ai/getting-started>`__
"""

import datetime
import logging
import json
from pathlib import Path
from statistics import mean
from typing import TypedDict

import requests
from eth_typing import HexAddress
from requests import Session

from eth_defi.sqlite_cache import PersistentKeyValueStore
from eth_defi.compat import native_datetime_utc_now

from .trusted_tokens import KNOWN_GOOD_TOKENS


logger = logging.getLogger(__name__)


class TokenSnifferError(Exception):
    """Wrap bad API replies from TokenSniffer.

    - Has attribute `status_code`
    """

    def __init__(self, msg: str, status_code: int, address: str):
        """
        :param status_code:
            to reflect the HTTP code (e.g. 404 if TokenSniffer does not have data)
        """
        super().__init__(msg)
        self.status_code = status_code
        self.address = address


class TokenSnifferReply(TypedDict):
    """TokenSniffer JSON payload.

    - Some of the fields annotated (not all)

    - Described here https://tokensniffer.readme.io/reference/response

    - Token is low risk if :py:attr:`score` > 80

    Example data:

    .. code-block:: javascript

            {'address': '0x873259322be8e50d80a4b868d186cc5ab148543a',
             'balances': {'burn_balance': 0.002441962189654333,
                          'deployer_balance': 0,
                          'lock_balance': 0,
                          'owner_balance': 0,
                          'top_holders': [{'address': '0x15ef07c7ec863081b757f34c497452dbb65f16f7',
                                           'balance': 9332.365029778688,
                                           'is_contract': False},
                                          {'address': '0x0453bef3490d4e4cbb01ec94737b75bbc051c750',
                                           'balance': 7251.968154354711,
                                           'is_contract': False},
                                          {'address': '0x90ba15d4ad2c6ed1aa6296d4c06b3a7ad1599750',
                                           'balance': 3711.3650926786295,
                                           'is_contract': False},
                                          {'address': '0x788b293db0068b17c1147d289aebcd1c7cc11229',
                                           'balance': 1918.5942148506324,
                                           'is_contract': False},
                                          {'address': '0x08c2d690340998bf3f74e6a6496fc2868ced75d5',
                                           'balance': 1764.577012673702,
                                           'is_contract': False},
                                          {'address': '0xe8c97650aa7e4525cc45851af5b2f5f81403432a',
                                           'balance': 1759.1752683280474,
                                           'is_contract': False},
                                          {'address': '0xd4913c03ba8b00a85634c170a404b99ef01fe4f6',
                                           'balance': 1499.323423358631,
                                           'is_contract': False},
                                          {'address': '0x8e54b18ea37a97914149e4bec2b4146503ba14ed',
                                           'balance': 1285.0936630338015,
                                           'is_contract': False},
                                          {'address': '0xcd9f53208390399de0e2ba5914b7bd53afc62835',
                                           'balance': 1243.9131637279036,
                                           'is_contract': False},
                                          {'address': '0x2e43eac73fabe2b207d014726d7c157054beccde',
                                           'balance': 1177.2629555707488,
                                           'is_contract': False},
                                          {'address': '0x396e7c0cdd9dcec52f2b40948f8f703f8d750e10',
                                           'balance': 1101.9120660748333,
                                           'is_contract': False},
                                          {'address': '0x4a63eef3060ad8eabd67c4cd4b9f908c37f2e1c1',
                                           'balance': 1068.880217996999,
                                           'is_contract': False},
                                          {'address': '0x4eeee62a0c41fd39285af411fc9be030dc40a691',
                                           'balance': 1028.24351924691,
                                           'is_contract': False},
                                          {'address': '0xe1ef21cd83316467823b7cd33b43cd87b9ed645a',
                                           'balance': 948.0084401939899,
                                           'is_contract': False},
                                          {'address': '0xe8ea1eab72af70471e3cfa999f4c0eff173473ed',
                                           'balance': 904.7122650717333,
                                           'is_contract': False},
                                          {'address': '0x457d90dc48ba7549c1c04922dc0f3dea23c3a9f2',
                                           'balance': 897.8435736232242,
                                           'is_contract': False},
                                          {'address': '0x3f747d527666d752706fd5d96d5c857a8de4a517',
                                           'balance': 868.5356019052704,
                                           'is_contract': False},
                                          {'address': '0xc731022481a88f40541346fff53eaaf38a5d86ba',
                                           'balance': 811.2400720078341,
                                           'is_contract': False},
                                          {'address': '0xafc17077adcd32cf9110f8f9f271e250b7680fd1',
                                           'balance': 804.5682720521471,
                                           'is_contract': False},
                                          {'address': '0xa735df3b21a6f665e9cb54d7a29918f4047b638d',
                                           'balance': 778.4391820978005,
                                           'is_contract': False}]},
             'cached': True,
             'chainId': '1',
             'contract': {'has_blocklist': True,
                          'has_fee_modifier': True,
                          'has_max_transaction_amount': False,
                          'has_mint': False,
                          'has_pausable': False,
                          'has_proxy': False,
                          'is_source_verified': True},
             'created_at': 1718627231000,
             'decimals': 18,
             'deployer_addr': '0x5a0e7c0f651dfbb45cbc130a3e7422d3e2c8dc57',
             'exploits': [],
             'is_flagged': True,
             'message': 'OK',
             'name': 'Ponzio The Cat',
             'permissions': {'is_ownership_renounced': True,
                             'owner_address': '0x0000000000000000000000000000000000000000'},
             'pools': [{'address': '0x90908e414d3525e33733d320798b5681508255ea',
                        'base_address': '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2',
                        'base_reserve': 320.06561592285965,
                        'base_symbol': 'ETH',
                        'burn_balance': 3.24037034920393e+22,
                        'decimals': 18,
                        'deployer_balance': 0,
                        'initial_base_reserve': 0.25798805413538606,
                        'lock_balance': 0,
                        'locks': [],
                        'name': 'Uniswap v2',
                        'owner_balance': 1e-15,
                        'top_holders': [{'address': '0x46030f5e33afa7d0b7c0c54a3a8017e10140a979',
                                         'balance': 135224.0254206525},
                                        {'address': '0x000000000000000000000000000000000000dead',
                                         'balance': 32403.7034920393},
                                        {'address': '0xf76a09d5930285456162fb3c5317d4d79498990a',
                                         'balance': 234.8025868953592},
                                        {'address': '0x1fd1e7bc0e6a5255f94047470bfff8dcafdf2bfa',
                                         'balance': 45.550801246839946},
                                        {'address': '0xa147ebe368a411b2e757f36cef91c592e52adeb2',
                                         'balance': 36.74201290998386},
                                        {'address': '0xf5af46bc5f3a9d412c27ba53c5e57f0ccf9b8ab5',
                                         'balance': 19.0041259704864},
                                        {'address': '0x538f8a3181e6b192629591c06116c882b6be2b7c',
                                         'balance': 16.154725552250454},
                                        {'address': '0x5cc71b76c0ea69c27362e9a595969512933c94c7',
                                         'balance': 15.743890726031212},
                                        {'address': '0xab25362ca38b11975885ffd66b4e7d928159cb56',
                                         'balance': 15.599079045130523},
                                        {'address': '0xeafbfc76e54fbad22e3314008cc1b0d4fa8c1691',
                                         'balance': 11.270345823990574},
                                        {'address': '0xc38798d5444f4b8af98e4dd890bde225f2e2da59',
                                         'balance': 11.108066605155567},
                                        {'address': '0xc425591420ecc0ae301d7c2e223ec6d34ce56902',
                                         'balance': 8.060042964227087},
                                        {'address': '0x16a0ce3e805dd11c7074e9851ab33bfac0cc5bb5',
                                         'balance': 7.429134425519216},
                                        {'address': '0x02cd35dd57d37b97da3df69a526e458f2e8beaa3',
                                         'balance': 6.554675101704869},
                                        {'address': '0x6b96559df5bce0d46487efa92ef41fe68f901f5c',
                                         'balance': 5.054292341485139},
                                        {'address': '0x3286e7eca9da5f6fd9b4f9aad2d13cd0d625e16f',
                                         'balance': 4.972721217335674},
                                        {'address': '0xbfba29a3ca51ad0a4265bfbd223d3da9b0955cd9',
                                         'balance': 4.660561980388217},
                                        {'address': '0xe1908233a1c3b9b22389535e479eb1272f2e9d15',
                                         'balance': 4.639894642263144},
                                        {'address': '0x3aa3419475eca32efde41560de0135cf87c040ab',
                                         'balance': 4.496294877137668},
                                        {'address': '0x3ee117f85f58aae2d9e12ab30e3754e8921bb733',
                                         'balance': 4.460782725068053}],
                        'total_supply': 1.681493252109614e+23,
                        'version': '2'}],
             'refreshed_at': 1725437810442,
             'riskLevel': 'high',
             'score': 0,
             'similar': [{'address': '0xbe80849ef400b2dfb616c8c268e4e4fa04fb8b8e',
                          'chainId': 'ETH',
                          'stcore': 93},
                         {'address': '0x31e81092412bf5eb329ac7bf3ccaf0971f84e2c2',
                          'chainId': 'ETH',
                          'stcore': 91}],
             'status': 'ready',
             'swap_simulation': {'buy_fee': 1.525060573608289e-14,
                                 'is_sellable': True,
                                 'sell_fee': 0},
             'symbol': 'Ponzio',
             'tests': [{'description': 'Verified contract source',
                        'id': 'testForMissingSource',
                        'result': False},
                       {'description': 'Source does not contain a proxy contract',
                        'id': 'testForProxy',
                        'result': False},
                       {'description': 'Source does not contain a pausable contract',
                        'id': 'testForPausable',
                        'result': False},
                       {'description': 'Source does not contain a mint function',
                        'id': 'testForMint',
                        'result': False},
                       {'description': 'Source does not contain a function to restore '
                                       'ownership',
                        'id': 'testForRestoreOwnership',
                        'result': False},
                       {'description': 'Source does not contain a function to set maximum '
                                       'transaction amount',
                        'id': 'testForMaxTransactionAmount',
                        'result': False},
                       {'description': 'Source does not contain a function to modify the '
                                       'fee',
                        'id': 'testForModifiableFee',
                        'result': True},
                       {'description': 'Source does not contain a function to blacklist '
                                       'holders',
                        'id': 'testForBlacklist',
                        'result': True},
                       {'description': 'Ownership renounced or source does not contain an '
                                       'owner contract',
                        'id': 'testForOwnershipNotRenounced',
                        'result': False},
                       {'description': 'Creator not authorized for special permission',
                        'id': 'testForAuthorization',
                        'result': False},
                       {'description': 'Tokens locked/burned',
                        'id': 'testForTokensLockedOrBurned',
                        'result': True,
                        'value': 0.002441962189654333,
                        'valuePct': 9.353727652891257e-05},
                       {'description': 'Creator wallet contains less than 5% of token '
                                       'supply',
                        'id': 'testForHighCreatorTokenBalance',
                        'result': False,
                        'value': 0,
                        'valuePct': 0},
                       {'description': 'Owner wallet contains less than 5% of token supply',
                        'id': 'testForHighOwnerTokenBalance',
                        'result': False,
                        'value': 0,
                        'valuePct': 0},
                       {'data': [{'address': '0x15ef07c7ec863081b757f34c497452dbb65f16f7',
                                  'balance': 9332.365029778688,
                                  'is_contract': False},
                                 {'address': '0x0453bef3490d4e4cbb01ec94737b75bbc051c750',
                                  'balance': 7251.968154354711,
                                  'is_contract': False},
                                 {'address': '0x90ba15d4ad2c6ed1aa6296d4c06b3a7ad1599750',
                                  'balance': 3711.3650926786295,
                                  'is_contract': False},
                                 {'address': '0x788b293db0068b17c1147d289aebcd1c7cc11229',
                                  'balance': 1918.5942148506324,
                                  'is_contract': False},
                                 {'address': '0x08c2d690340998bf3f74e6a6496fc2868ced75d5',
                                  'balance': 1764.577012673702,
                                  'is_contract': False},
                                 {'address': '0xe8c97650aa7e4525cc45851af5b2f5f81403432a',
                                  'balance': 1759.1752683280474,
                                  'is_contract': False},
                                 {'address': '0xd4913c03ba8b00a85634c170a404b99ef01fe4f6',
                                  'balance': 1499.323423358631,
                                  'is_contract': False},
                                 {'address': '0x8e54b18ea37a97914149e4bec2b4146503ba14ed',
                                  'balance': 1285.0936630338015,
                                  'is_contract': False},
                                 {'address': '0xcd9f53208390399de0e2ba5914b7bd53afc62835',
                                  'balance': 1243.9131637279036,
                                  'is_contract': False},
                                 {'address': '0x2e43eac73fabe2b207d014726d7c157054beccde',
                                  'balance': 1177.2629555707488,
                                  'is_contract': False},
                                 {'address': '0x396e7c0cdd9dcec52f2b40948f8f703f8d750e10',
                                  'balance': 1101.9120660748333,
                                  'is_contract': False},
                                 {'address': '0x4a63eef3060ad8eabd67c4cd4b9f908c37f2e1c1',
                                  'balance': 1068.880217996999,
                                  'is_contract': False},
                                 {'address': '0x4eeee62a0c41fd39285af411fc9be030dc40a691',
                                  'balance': 1028.24351924691,
                                  'is_contract': False},
                                 {'address': '0xe1ef21cd83316467823b7cd33b43cd87b9ed645a',
                                  'balance': 948.0084401939899,
                                  'is_contract': False},
                                 {'address': '0xe8ea1eab72af70471e3cfa999f4c0eff173473ed',
                                  'balance': 904.7122650717333,
                                  'is_contract': False},
                                 {'address': '0x457d90dc48ba7549c1c04922dc0f3dea23c3a9f2',
                                  'balance': 897.8435736232242,
                                  'is_contract': False},
                                 {'address': '0x3f747d527666d752706fd5d96d5c857a8de4a517',
                                  'balance': 868.5356019052704,
                                  'is_contract': False},
                                 {'address': '0xc731022481a88f40541346fff53eaaf38a5d86ba',
                                  'balance': 811.2400720078341,
                                  'is_contract': False},
                                 {'address': '0xafc17077adcd32cf9110f8f9f271e250b7680fd1',
                                  'balance': 804.5682720521471,
                                  'is_contract': False},
                                 {'address': '0xa735df3b21a6f665e9cb54d7a29918f4047b638d',
                                  'balance': 778.4391820978005,
                                  'is_contract': False}],
                        'description': 'All other wallets contain less than 5% of token '
                                       'supply',
                        'id': 'testForHighWalletTokenBalance',
                        'result': True},
                       {'description': 'Burned amount exceeds total token supply',
                        'id': 'testForBurnedBalanceExceedsSupply',
                        'result': False},
                       {'description': 'All wallets combined contain less than 100% of '
                                       'token supply',
                        'id': 'testForCombinedWalletsExceedSupply',
                        'result': True},
                       {'description': 'All wallets contain less than 100% of token supply',
                        'id': 'testForImpossibleWalletTokenBalance',
                        'result': True},
                       {'currency': 'ETH',
                        'description': 'Adequate current liquidity',
                        'id': 'testForInadequateLiquidity',
                        'result': False,
                        'value': 320.06561592285965,
                        'valuePct': 320.06561592285965},
                       {'description': 'Adequate initial liquidity',
                        'id': 'testForInadequateInitialLiquidity',
                        'result': True,
                        'value': 0.25798805413538606,
                        'valuePct': 0.6449701353384651},
                       {'description': 'At least 95% of liquidity locked/burned',
                        'id': 'testForInadeqateLiquidityLockedOrBurned',
                        'result': True,
                        'value': 3.24037034920393e+22,
                        'valuePct': 0.19270790085767736},
                       {'description': 'Creator wallet contains less than 5% of liquidity',
                        'id': 'testForHighCreatorLPBalance',
                        'result': False,
                        'value': 0,
                        'valuePct': 0},
                       {'description': 'Owner wallet contains less than 5% of liquidity',
                        'id': 'testForHighOwnerLPBalance',
                        'result': False,
                        'value': 1e-15,
                        'valuePct': 5.947094933300462e-39},
                       {'description': 'Token is sellable',
                        'id': 'testForUnableToSell',
                        'result': False},
                       {'description': 'Buy fee is less than 5%',
                        'id': 'testForHighBuyFee',
                        'result': False,
                        'valuePct': 0},
                       {'description': 'Sell fee is less than 5%',
                        'id': 'testForHighSellFee',
                        'result': False,
                        'valuePct': 0},
                       {'description': 'Buy/sell fee is less than 30%',
                        'id': 'testForExtremeFee',
                        'result': False}],
             'total_supply': 21000000}

    """

    #: Added to the response if it was locally cached
    cached: bool

    #: OK if success
    message: str

    #:
    status: str

    #: 0-100 d
    score: int

    #: Trading pool data
    pools: list[dict]


class TokenSniffer:
    """TokenSniffer API."""

    def __init__(self, api_key: str, session: Session = None):
        assert api_key
        self.api_key = api_key

        assert self.api_key.strip() == self.api_key, f"Got: {self.api_key}"

        if session is None:
            session = requests.Session()

        self.session = session

    def fetch_token_info(self, chain_id: int, address: str | HexAddress) -> TokenSnifferReply:
        """Get TokenSniffer token data.

        This is a synchronous method and may block long time if TokenSniffer does not have cached results.

        https://tokensniffer.com/api/v2/tokens/{chain_id}/{address}

        :param chain_id:
            Integer. Example for Ethereum mainnet is `1`.

        :param address:
            ERC-20 smart contract address.

        :return:
            Raw TokenSniffer JSON reply.

        """
        assert type(chain_id) == int
        assert address.startswith("0x")

        parameters = {
            "apikey": self.api_key,
            "include_metrics": True,
            "include_tests": True,
            "include_similar": True,
            "block_until_ready": True,
        }

        logger.info("Fetching TokenSniffer data %d: %s", chain_id, address)

        url = f"https://tokensniffer.com/api/v2/tokens/{chain_id}/{address}"
        resp = self.session.get(url, params=parameters)
        if resp.status_code != 200:
            raise TokenSnifferError(
                msg=f"TokeSniffer replied on address {address}: {resp}: {resp.text} API key is: {self.api_key[0:12]}...{self.api_key[-2:]}",
                status_code=resp.status_code,
                address=address,
            )

        data = resp.json()

        if data["message"] != "OK":
            raise TokenSnifferError(f"Bad TokenSniffer address: {address} reply: {data}", status_code=resp.status_code, address=address)

        # Add timestamp when this was recorded,
        # so cache can have this also as a content value
        data["data_fetched_at"] = native_datetime_utc_now().isoformat()

        return data


class CachedTokenSniffer(TokenSniffer):
    """Add file-system based cache for TokenSniffer API.

    - See :py:class:`TokenSniffer` class for details

    - Use SQLite DB as a key-value cache backend, or your custom cache interface

    - No support for multithreading/etc. fancy stuff

    Example usage:

    .. code-block:: python

        from eth_defi.token_analysis.tokensniffer import CachedTokenSniffer, is_tradeable_token

        #
        # Setup TokenSniffer
        #

        db_file = Path(cache_path) / "tokensniffer.sqlite"

        tokensniffer_threshold = 24  # Quite low threshold, 0 = total scam
        sniffer = CachedTokenSniffer(
            db_file,
            TOKENSNIFFER_API_KEY,
        )

        ticker = make_full_ticker(pair_metadata[pair_id])
        address = pair_metadata[pair_id]["base_token_address"]
        sniffed_data = sniffer.fetch_token_info(chain_id.value, address)
        if not is_tradeable_token(sniffed_data, risk_score_threshold=tokensniffer_threshold):
            score = sniffed_data["score"]
            print(f"WARN: Skipping pair {ticker} as the TokenSniffer score {score} is below our risk threshold")
            continue

    You can also use your own cache interface instead of SQLite. Here is an example SQLALchemy implementation:

    .. code-block:: python

        class TokenInternalCache(UserDict):
            def __init__(self, dbsession: Session):
                self.dbsession = dbsession

            def match_token(self, token_spec: str) -> Token:
                # Sniffer interface gives us tokens as {chain}-{address} strings
                chain, address = token_spec.split("-")
                chain_id = int(chain)
                address = HexBytes(address)
                return self.dbsession.query(Token).filter(Token.chain_id == chain_id, Token.address == address).one_or_none()

            def __getitem__(self, name) -> None | str:
                token = self.match_token(name)
                if token is not None:
                    if token.etherscan_data is not None:
                        return token.etherscan_data.get("tokensniffer_data")

                return None

            def __setitem__(self, name, value):
                token = self.match_token(name)
                if token.etherscan_data is None:
                    token.etherscan_data = {}
                token.etherscan_data["tokensniffer_data"] = value

            def __contains__(self, key):
                return self.get(key) is not None


        # And then usage:

        weth = dbsession.query(Token).filter_by(symbol="WETH", chain_id=1).one()

        sniffer = CachedTokenSniffer(
            cache_file=None,
            api_key=TOKENSNIFFER_API_KEY,
            cache=cast(dict, TokenInternalCache(dbsession)),
        )

        data = sniffer.fetch_token_info(weth.chain_id, weth.address.hex())
        assert data["cached"] is False

        data = sniffer.fetch_token_info(weth.chain_id, weth.address.hex())
        assert data["cached"] is True

    """

    def __init__(
        self,
        cache_file: Path | None,
        api_key: str,
        session: Session = None,
        cache: dict | None = None,
    ):
        """

        :param api_key:
            TokenSniffer API key.

        :param session:
            requests.Session for persistent HTTP connections

        :param cache_file:
            Path to a local file system SQLite file used as a cached.

            For simple local use cases.

        :param cache:
            Direct custom cache interface as a Python dict interface.

            For your own database caching.

            Cache keys are format: `cache_key = f"{chain_id}-{address}"`.
            Cache values are JSON blobs as string.

        """
        super().__init__(api_key, session)

        if cache is not None:
            assert cache_file is None, "Cannot give both cache interface and cache_path"
            self.cache = cache
        else:
            assert isinstance(cache_file, Path), f"Got {cache_file.__class__}"
            self.cache = PersistentKeyValueStore(cache_file)

    def fetch_token_info(self, chain_id: int, address: str | HexAddress) -> TokenSnifferReply:
        """Get TokenSniffer info.

        Use local file cache if available.

        :return:
            Data passed through TokenSniffer.

            A special member `cached` is set depending on whether the reply was cached or not.
        """
        cache_key = f"{chain_id}-{address}"
        cached = self.cache.get(cache_key)
        if not cached:
            decoded = super().fetch_token_info(chain_id, address)
            self.cache[cache_key] = json.dumps(decoded)
            decoded["cached"] = False
        else:
            decoded = json.loads(cached)
            decoded["cached"] = True

        return decoded

    def get_diagnostics(self) -> str:
        """Get a diagnostics message.

        - Use for logging what kind of data we have collected

        Example output:

        .. code-block:: text

            Token sniffer info is:

                    TokenSniffer cache database /Users/moo/.cache/tradingstrategy/tokensniffer.sqlite summary:

                    Entries: 195
                    Max score: 100
                    Min score: 0
                    Avg score: 56.6

        :return:
            Multi-line human readable string
        """

        scores = []
        path = self.cache.filename

        for key in self.cache.keys():
            data = json.loads(self.cache[key])
            scores.append(data["score"])

        text = f"""
        TokenSniffer cache database {path} summary:
        
        Entries: {len(scores)}
        Max score: {max(scores)}
        Min score: {min(scores)}
        Avg score: {mean(scores)}        
        """
        return text


def is_tradeable_token(
    data: TokenSnifferReply,
    symbol: str | None = None,
    risk_score_threshold=65,
    whitelist=KNOWN_GOOD_TOKENS,
) -> bool:
    """Risk assessment for open-ended trade universe.

    - Based on TokenSniffer reply, determine if we want to trade this token or not

    .. note::

        This will alert for USDT/USDC, etc. so be careful.

    Some example thresholds:

    .. code-block:: text

        WARN: Skipping pair USDT-USDC-uniswap-v2-30bps, address 0xdac17f958d2ee523a2206206994597c13d831ec7 as the TokenSniffer score 45 is below our risk threshold, liquidity is 2,447,736.44 USD
        WARN: Skipping pair MKR-DAI-uniswap-v2-30bps as the TokenSniffer score 70 is below our risk threshold, liquidity is 76,978,850.37
        WARN: Skipping pair PEPE-WETH-uniswap-v2-30bps as the TokenSniffer score 70 is below our risk threshold, liquidity is 19,104,516.38
        WARN: Skipping pair XXi-WETH-uniswap-v2-30bps as the TokenSniffer score 50 is below our risk threshold, liquidity is 10,234,803.81
        WARN: Skipping pair PAXG-WETH-uniswap-v2-30bps as the TokenSniffer score 20 is below our risk threshold, liquidity is 9,197,796.28
        WARN: Skipping pair FLOKI-WETH-uniswap-v2-30bps as the TokenSniffer score 69 is below our risk threshold, liquidity is 8,786,378.77
        WARN: Skipping pair BEAM-WETH-uniswap-v2-30bps as the TokenSniffer score 70 is below our risk threshold, liquidity is 5,192,385.34

    :param symbol:
        For manual whitelist check.

    :param whitelist:
        Always whitelist these if the token symbol matches.

        E.g. WBTC needs to be whitelisted, as its risk score is 45.

    :return:
        True if we want to trade
    """

    if symbol is not None:
        if symbol in whitelist:
            return True

    # Trust on TokenSniffer heurestics
    return data["score"] >= risk_score_threshold
