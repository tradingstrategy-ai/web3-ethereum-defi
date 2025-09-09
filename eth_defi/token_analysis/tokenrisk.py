"""Glider Token Risk API.

- Python wrapper for `Token Risk API by Hexens <https://hexens.io/solutions/token-risks-api>`__, based on `Glider <https://glide.gitbook.io/main>`__

- Allows to fetch ERC-20 risk flags and other automatically analysed metadata to determine if a token is some sort of a scam or not

- For usage see :py:class:`CachedTokenRisk` class

- `Read Token Risk REST API documentation <https://glide.gitbook.io/main/glider-api/api-documentation>`__

"""

import datetime
import logging
import json
from pathlib import Path
from statistics import mean
from typing import TypedDict, Collection

import requests
from eth_typing import HexAddress
from requests import Session
from requests.sessions import HTTPAdapter

from eth_defi.sqlite_cache import PersistentKeyValueStore
from eth_defi.velvet.logging_retry import LoggingRetry

from .trusted_tokens import KNOWN_GOOD_TOKENS

logger = logging.getLogger(__name__)


#: The default location of SQLite cache of Token Risk replies
DEFAULT_CACHE_PATH = Path.home() / ".cache" / "tradingstrategy" / "glide-token-risk.sqlite"


#: List of Token Risk flags we do not want to trade by default
DEFAULT_AVOID_RISKS = [
    # Hidden fee functionality included in transfers
    # "risk_hidden_fees",
    "risk_balance_manipulation_in_non_standard_functions",
]

DEFAULT_RETRIES = 15


class TokenRiskError(Exception):
    """Wrap bad API replies from Token Risk.

    - Has attribute `status_code`
    """

    def __init__(self, msg: str, status_code: int, address: str):
        """
        :param status_code:
            to reflect the HTTP code (e.g. 404 if Token Risk does not have data)
        """
        super().__init__(msg)
        self.status_code = status_code
        self.address = address


class TokenRiskSmartContractInfo(TypedDict):
    """Token Risk info about if a smart contract is proxy and verified"""

    implementation_address: str | None
    is_proxy: bool
    is_verified: bool
    proxy_address: str | None


class TokenRiskFlags(TypedDict):
    """All evaluated flags are returned, value being true or false.

    Example:

    .. code-block:: json

        {
        'description': "The token contract's transfer or transferFrom "
                                     'functions have a hidden fee functionality that '
                                     'can be turned on. This may mean that the '
                                     'receiver address can get fewer or a different '
                                     'amount of tokens than passed within the transfer '
                                     'functions.',
                      'key': 'risk_hidden_fees',
                      'severity': 'high',
                      'sub_title': 'Hidden fee functionality included in transfers',
                      'title': 'Hidden fees',
                      'value': 'false'}
    """

    description: str
    key: str
    sub_title: str
    title: str
    value: bool


class TokenRiskReply(TypedDict):
    """Token Risk JSON payload.

    Example:

    .. code-block:: none

        {'address': '0x7aaaa5b10f97321345acd76945083141be1c5631',
         'cached': False,
         'cached_at': '2025-08-07T09:00:36.460660',
         'chain_id': '56',
         'data_fetched_at': '2025-08-07T09:00:36.460616',
         'execution_time': 0.001073565,
         'info': {'implementation_address': None,
                  'is_proxy': False,
                  'is_verified': True,
                  'proxy_address': None},
         'market_endorsed': False,
         'results': [{'description': "The token contract's transfer or transferFrom "
                                     'functions have a hidden fee functionality that '
                                     'can be turned on. This may mean that the '
                                     'receiver address can get fewer or a different '
                                     'amount of tokens than passed within the transfer '
                                     'functions.',
                      'key': 'risk_hidden_fees',
                      'severity': 'high',
                      'sub_title': 'Hidden fee functionality included in transfers',
                      'title': 'Hidden fees',
                      'value': 'false'},

         ...

         'score': 0}

    """

    #: Added to the response if it was locally cached
    cached: bool

    #: ISO format of the orignal reply caching timestamp
    data_fetched_at: str

    #: 0 - 100, but prefer flag checks in :py:attr:`results` yourself
    score: int

    info: TokenRiskSmartContractInfo

    chain_id: int

    address: str

    market_endorsed: bool

    results: list[TokenRiskFlags]


class TokenRisk:
    """Token Risk API.

    - `Glider Token Risk API by Hexens <https://glide.gitbook.io/main/glider-api/risks-breakdown>`__.
    - Intelligent support for API throttling, etc.
    """

    def __init__(
        self,
        api_key: str,
        session: Session = None,
        retries: int | None = DEFAULT_RETRIES,
    ):
        """

        :param api_key:
            From Glider

        :param session:
            Custom request session object

        :param retries:
            Set up retry policy.

            Handle API throttling.

            Set None to disable retries.
        """
        assert api_key
        self.api_key = api_key

        assert self.api_key.strip() == self.api_key, f"Got: {self.api_key}"
        assert self.api_key

        if session is None:
            session = requests.Session()

            retry_policy = LoggingRetry(
                total=retries,
                backoff_factor=0.9,
                status_forcelist=[429, 500, 502, 503, 504],
            )
            session.mount("http://", HTTPAdapter(max_retries=retry_policy))
            session.mount("https://", HTTPAdapter(max_retries=retry_policy))

        else:
            assert retries is None, f"Cannot set retries with custom session: {session}"

        self.session = session

        self.api_url = f"https://data1.hexens.io/api/v1/contract/analyze-risk"

    def fetch_token_info(self, chain_id: int, address: str | HexAddress) -> TokenRiskReply:
        """Get Token Risk token data.

        This is a synchronous method and may block long time if Token Risk does not have cached results.

        https://Token Risk.com/api/v2/tokens/{chain_id}/{address}

        :param chain_id:
            Integer. Example for Ethereum mainnet is `1`.

        :param address:
            ERC-20 smart contract address.

        :return:
            Raw Token Risk JSON reply.

        """
        assert type(chain_id) == int
        assert address.startswith("0x")

        parameters = {
            "token": self.api_key,
            "chain_id": chain_id,
            "address": address,
        }

        logger.info("Fetching Token Risk data %d: %s", chain_id, address)

        resp = self.session.get(self.api_url, params=parameters)

        if resp.status_code != 200:
            raise TokenRiskError(
                msg=f"Token Risk replied on address {address}: {resp}: {resp.text}\nFull URL is {resp.url}",
                status_code=resp.status_code,
                address=address,
            )

        data = resp.json()

        # error_message {'cached': True,
        #  'cached_at': '2025-08-18T10:26:16.911559',
        #  'data_fetched_at': '2025-08-18T10:26:16.911525',
        #  'error_message': 'The address could not be parsed due to an invalid or '
        #                   'unrecognized format'}
        if "error_message" in data:
            raise TokenRiskError(
                msg=f"Token Risk replied on address {address}: {data['error_message']}",
                status_code=resp.status_code,
                address=address,
            )

        # Add timestamp when this was recorded,
        # so cache can have this also as a content value
        data["data_fetched_at"] = datetime.datetime.utcnow().isoformat()

        return data


class CachedTokenRisk(TokenRisk):
    """Add file-system based cache for Token Risk API.

    Example:

    .. code-block:: python

        TOKEN_RISK_API_KEY = os.environ.get("TOKEN_RISK_API_KEY")

        token_risk = CachedTokenRisk(
            TOKEN_RISK_API_KEY,
        )

        # COW on BNB Chain
        data = token_risk.fetch_token_info(56, "0x7aaaa5b10f97321345acd76945083141be1c5631")

        assert data["score"] == 0
        assert not is_tradeable_token(data)

    You can also pass your custom SQLite file for caching:

    .. code-block:: python

        path = Path("./cache/token_risk.sqlite")
        token_risk = CachedTokenRisk(
            api_key=os.environ["TOKEN_RISK_API_KEY"],
            cache_file=path,
        )
    """

    def __init__(
        self,
        api_key: str,
        cache_file: Path | None = DEFAULT_CACHE_PATH,
        session: Session = None,
        cache: dict | None = None,
        retries: int | None = DEFAULT_RETRIES,
    ):
        """

        :param api_key:
            Token Risk API key.

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
        super().__init__(api_key, session=session, retries=retries)

        assert isinstance(api_key, str), f"Got {api_key.__class__}"
        if cache_file:
            assert isinstance(cache_file, Path), f"Got {cache_file.__class__}: {cache_file}"
            cache_file.parent.mkdir(parents=True, exist_ok=True)

        if cache is not None:
            assert cache_file is None, "Cannot give both cache interface and cache_path"
            self.cache = cache
        else:
            assert isinstance(cache_file, Path), f"Got {cache_file.__class__}"
            self.cache = PersistentKeyValueStore(cache_file)

        logger.info("Starting with Token Risk cache at %s, we have %d entries cached", cache_file, len(self.cache))

    def fetch_token_info(self, chain_id: int, address: str | HexAddress) -> TokenRiskReply:
        """Get Token Risk info.

        Use local file cache if available.

        :return:
            Data passed through Token Risk.

            A special member `cached` is set depending on whether the reply was cached or not.
        """
        cache_key = f"{chain_id}-{address}"
        cached = self.cache.get(cache_key)
        if not cached:
            decoded = super().fetch_token_info(chain_id, address)
            decoded["cached_at"] = datetime.datetime.utcnow().isoformat()
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

                    Token Risk cache database /Users/moo/.cache/tradingstrategy/Token Risk.sqlite summary:

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
        Token Risk cache database {path} summary:
        
        Entries: {len(scores)}
        Max score: {max(scores)}
        Min score: {min(scores)}
        Avg score: {mean(scores)}        
        """
        return text


def has_risk_flags(
    data: TokenRiskReply,
    avoid_risks: Collection[str] = DEFAULT_AVOID_RISKS,
) -> bool:
    """Check if any of the risk flags are set in Token Risk reply.

    :param data:
        Token Risk reply data

    :param avoid_risks:
        List of risk flags to avoid, by their "key" value

    :return:
        True if any of the risks is set
    """

    for flag in data["results"]:
        # Hexen API is broken returning "true" string instead of true value
        if flag["value"] in (True, "true") and flag["key"] in avoid_risks:
            return True

    return False


def is_tradeable_token(
    data: TokenRiskReply,
    symbol: str | None = None,
    whitelist=KNOWN_GOOD_TOKENS,
    risk_score_threshold=5,
    avoid_risks: Collection[str] = DEFAULT_AVOID_RISKS,
) -> bool:
    """Risk assessment for open-ended trade universe.

    - Based on Token Risk reply, determine if we want to trade this token or not

    .. note::

        This will alert for USDT/USDC, etc. so be careful.

    :param symbol:
        For manual whitelist check.

    :param whitelist:
        Always whitelist these if the token symbol matches.

        E.g. WBTC needs to be whitelisted, as its risk score is 45.

    :param avoid_risks:
        If any of these risk flags is set, short circuit to zero

    :param risk_score_threshold:
        If the risk score is below this, we do not want to trade.

        Default is zero, so if the token has any risk flags set, we do not want to trade.

        Between 0-100.

    :return:
        True if we want to trade
    """

    if symbol is not None:
        if symbol in whitelist:
            return True

    if has_risk_flags(data, avoid_risks):
        # If any of the risk flags are set, we do not want to trade
        return False

    # Trust on Token Risk heurestics
    return data["score"] >= risk_score_threshold
