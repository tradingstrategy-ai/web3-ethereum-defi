"""Token holding and portfolio for addresses."""

import logging
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, Optional, Collection, Iterable, Hashable

import cachetools
import requests.exceptions
from eth_typing import BlockNumber, HexAddress

from web3 import Web3
from web3.contract import Contract
from web3.exceptions import BadFunctionCallOutput, InvalidAddress
from web3.types import BlockIdentifier

from eth_defi.abi import get_contract
from eth_defi.event import fetch_all_events
from eth_defi.event_reader.conversion import convert_address_to_bytes32, convert_int256_bytes_to_int
from eth_defi.event_reader.multicall_batcher import EncodedCall, read_multicall_chunked
from eth_defi.event_reader.multicall_batcher import get_multicall_contract, MulticallWrapper, call_multicall, call_multicall_batched_single_thread
from eth_defi.provider.anvil import is_anvil, is_mainnet_fork
from eth_defi.provider.broken_provider import get_almost_latest_block_number
from eth_defi.provider.mev_blocker import MEVBlockerProvider
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory
from eth_defi.provider.named import get_provider_name
from eth_defi.token import fetch_erc20_details, DEFAULT_TOKEN_CACHE
from eth_defi.vault.lower_case_dict import LowercaseDict
from eth_defi.compat import WEB3_PY_V7

logger = logging.getLogger(__name__)


@dataclass
class DecimalisedHolding:
    """A helper class to represent token holdings.

    Exposes the underlying decimals the ERC-20 wants to express.
    """

    value: Decimal
    decimals: int
    contract: Contract


class BalanceFetchFailed(Exception):
    """Could not read balances for an address.

    Usually this means that you tried to read balances for an address with too many transactions
    and the underlying GoEthereun node craps out.
    """


@dataclass(slots=True, frozen=True)
class ERC20BalanceCall(MulticallWrapper):
    """Multicall wrapper for ERC-20 balanceOf calls."""

    call: object  # The contract function call object (required)
    debug: bool = False  # Debug flag (optional)
    token_address: HexAddress = None  # Token contract address
    holder_address: HexAddress = None  # Address whose balance we're checking

    def get_key(self) -> Hashable:
        return self.token_address

    def handle(self, succeed: bool, raw_return_value: bytes) -> int | None:
        if not succeed or raw_return_value is None:
            return None
        try:
            if len(raw_return_value) != 32:
                return None
            return int.from_bytes(raw_return_value, byteorder="big")
        except Exception:
            return None

    def __repr__(self):
        return f"ERC20BalanceCall(token={self.token_address}, holder={self.holder_address})"


def fetch_erc20_balances_by_transfer_event(
    web3: Web3,
    owner: HexAddress,
    from_block: Optional[int] = 1,
    last_block_num: Optional[BlockNumber] = None,
) -> Dict[HexAddress, int]:
    """Get all current holdings of an account.

    We attempt to build a list of token holdings by analysing incoming ERC-20 Transfer events to a wallet.

    The blockchain native currency like `ETH` or `MATIC` is not included in the analysis, because native
    currency transfers do not generate events.

    We are not doing any throttling: If you ask for too many events once this function and your
    Ethereum node are likely to blow up.

    .. note ::

        Because the limitations of GoEthereum, this method is likely to fail on public
        JSON-RPC nodes for blockchains like Binance Smart Chain, Polygon and others.
        E.g. BSC nodes will fail with `{'code': -32000, 'message': 'exceed maximum block range: 5000'}`.
        Even if the nodes don't directly fail, their JSON-RPC APIs are likely to timeout.

    Example:

    .. code-block:: python

        # Load up the user with some tokens
        usdc.functions.transfer(user_1, 500).transact({"from": deployer})
        aave.functions.transfer(user_1, 200).transact({"from": deployer})
        balances = fetch_erc20_balances(web3, user_1)
        assert balances[usdc.address] == 500
        assert balances[aave.address] == 200

    :param web3: Web3 instance
    :param owner: The address we are analysis
    :param from_block: As passed to eth_getLogs
    :param last_block_num: Set to the last block, inclusive, if you want to have an analysis of in a point of history.
    :return: Map of (token address, amount)
    """

    IERC20 = get_contract(web3, "sushi/IERC20.json")
    Transfer = IERC20.events.Transfer

    balances = Counter()

    #
    # TODO: We are not iterating over the Transfer() events in historical order -
    # though this should not cause difference in the end balances
    #

    try:
        # Iterate over all ERC-20 transfer events to the address
        for transfer in fetch_all_events(web3, Transfer, argument_filters={"to": owner}, to_block=last_block_num, from_block=from_block):
            # transfer is AttributeDict({'args': AttributeDict({'from': '0x7E5F4552091A69125d5DfCb7b8C2659029395Bdf', 'to': '0x2B5AD5c4795c026514f8317c7a215E218DcCD6cF', 'value': 200}), 'event': 'Transfer', 'logIndex': 0, 'transactionIndex': 0, 'transactionHash': HexBytes('0xd3fef67dbded34f1f7b2ec5217e5dfd5e4d9ad0fda66a8da925722f1e62518c8'), 'address': '0x2946259E0334f33A064106302415aD3391BeD384', 'blockHash': HexBytes('0x55618d13d644f35a8639671561c2f9a93958eae055c754531b124735f92b429b'), 'blockNumber': 4})
            erc20_smart_contract = transfer["address"]
            value = transfer["args"]["value"]
            balances[erc20_smart_contract] += value

        for transfer in fetch_all_events(web3, Transfer, argument_filters={"from": owner}, to_block=last_block_num, from_block=from_block):
            erc20_smart_contract = transfer["address"]
            value = transfer["args"]["value"]
            balances[erc20_smart_contract] -= value

        return balances
    except requests.exceptions.ReadTimeout as e:
        raise BalanceFetchFailed(f"Could not read Transfer() events for an address {owner} - fetch_erc20_balances() only works with addresses with small amount of transfers") from e


def fetch_erc20_balances_by_token_list(
    web3: Web3,
    owner: HexAddress | str,
    tokens: Collection[HexAddress | str],
    block_identifier: BlockIdentifier = None,
    decimalise=False,
) -> Dict[HexAddress | str, int | Decimal]:
    """Get all current holdings of an account for a limited set of ERC-20 tokens.

    If you know what tokens you are interested in, this method is much more efficient
    way than :py:func:`fetch_erc20_balances_by_transfer_event` to query token balances.

    Example:

    .. code-block:: python

        def test_portfolio_token_list(web3: Web3, deployer: str, user_1: str, usdc: Contract, aave: Contract):
            # Create a set of tokens
            tokens = {aave.address, usdc.address}
            # Load up the user with some tokens
            usdc.functions.transfer(user_1, 500).transact({"from": deployer})
            aave.functions.transfer(user_1, 200).transact({"from": deployer})
            balances = fetch_erc20_balances_by_token_list(web3, user_1, tokens)
            assert balances[usdc.address] == 500
            assert balances[aave.address] == 200

    :param tokens:
        ERC-20 list

    :param block_identifier:
        Fetch at specific height

    :param decimalise:
        If ``True``, convert output amounts to humanised format in Python :py:class:`Decimal`.

         Use cached :py:class:`TokenDetails` data.

    :raise BalanceFetchFailed:
        When you give a non-ERC-20 contract as a token.
    """

    assert len(tokens) > 0, "Queried token set is empty"

    assert owner.startswith("0x")
    owner = Web3.to_checksum_address(owner)

    if block_identifier is None:
        block_identifier = get_almost_latest_block_number(web3)
        last_block = web3.eth.block_number
    else:
        last_block = None

    chain_id = web3.eth.chain_id

    logger.info(f"Reading the latest token balances for {len(tokens)} tokens at block identifier {block_identifier}, last block is {last_block}, address is {owner}")

    balances = LowercaseDict()
    for address in tokens:
        # Uses cached token ABI
        token = fetch_erc20_details(web3, address, chain_id=chain_id)
        try:
            if decimalise:
                balances[address] = token.fetch_balance_of(owner, block_identifier)
            else:
                raw_amount = token.contract.functions.balanceOf(owner).call(block_identifier=block_identifier)
                balances[address] = raw_amount

        except BadFunctionCallOutput as e:
            raise BalanceFetchFailed(f"Could not get ERC-20 {address} balance for {owner}") from e

    return balances


def convert_balances_to_decimal(
    web3,
    raw_balances: Dict[HexAddress | str, int],
    require_decimals=True,
) -> Dict[HexAddress, DecimalisedHolding]:
    """Convert mapping of ERC-20 holdings to decimals.

    Issues a JSON-RPC call to fetch token data for each ERC-20 in the input dictionary.

    Example:

    .. code-block:: python

        raw_balances = fetch_erc20_balances_by_token_list(web3, address, tokens)
        return convert_balances_to_decimal(web3, raw_balances)

    :param raw_balances:
        Token address -> uint256 mappings

    :param require_decimals:
        Safety check to ensure ERC-20 tokens have valid decimals set.
        Prevents some wrong addresses and broken tokens.

    :return: Token address -> `DecimalisedHolding` mappings
    """

    # decimals() is not part of core ERC-20 interface,
    # use OpenZeppein contract here
    ERC20 = get_contract(web3, "ERC20MockDecimals.json")

    res = {}

    for address, raw_balance in raw_balances.items():
        address = web3.to_checksum_address(address)
        try:
            contract = ERC20(address)
        except InvalidAddress as e:
            raise RuntimeError(f"Could not handle address: {address}") from e
        decimals = contract.functions.decimals().call()

        if require_decimals:
            assert decimals > 0, f"ERC20.decimals() did not return a good value: {address}"

        res[address] = DecimalisedHolding(Decimal(raw_balance) / Decimal(10**decimals), decimals, contract)

    return res


def fetch_erc20_balances_multicall_v6(
    web3: Web3,
    address: HexAddress | str,
    tokens: list[HexAddress | str] | set[HexAddress | str],
    block_identifier,
    decimalise=True,
    chunk_size=50,
    token_cache: cachetools.Cache | None = DEFAULT_TOKEN_CACHE,
    gas_limit=10_000_000,
    raise_on_error=True,
    max_workers=1,
) -> dict[HexAddress | str, Decimal]:
    """Read balance of multiple ERC-20 tokens on an address once using multicall.

    - Fast, batches multiple calls on one JSON-RPC request

    - Using internal multicall module :py:mod:`eth_defi.event_reader.multicall_batcher`

    Example:

    .. code-block:: python

        def test_fetch_erc20_balances_multicall(web3):
            tokens = {
                "0x6921B130D297cc43754afba22e5EAc0FBf8Db75b",  # DogInMe
                "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",  # USDC on Base
            }

            # Velvet vault
            address = "0x9d247fbc63e4d50b257be939a264d68758b43d04"

            block_number = get_almost_latest_block_number(web3)

            balances = fetch_erc20_balances_multicall(
                web3,
                address,
                tokens,
                block_identifier=block_number,
            )

            existing_dogmein_balance = balances["0x6921B130D297cc43754afba22e5EAc0FBf8Db75b"]
            assert existing_dogmein_balance > 0

            existing_usdc_balance = balances["0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"]
            assert existing_usdc_balance > Decimal(1.0)

    :param address:
        Our wallet address of which balances we query

    :param tokens:
        List of ERC-20 addresses.

    :param block_identifier:
        Fetch at specific height.

        Must be given for a multicall.

    :param chunk_size:
        How many ERC-20 addresses feed to multicall once

    :param gas_limit:
        Gas limit of the multicall request

    :param decimalise:
        If ``True``, convert output amounts to humanised format in Python :py:class:`Decimal`.

         Use cached :py:class:`TokenDetails` data.

    :param token_cache:
        Cache ERC-20 decimal data.

    :param max_workers:
        Use this many worker processes

    :param raise_on_error:
        See `BalanceFetchFailed`.

    :raise BalanceFetchFailed:
        balanceOf() call failed.

        When you give a non-ERC-20 contract as a token.

    :return:
        Map of token address -> balance.

        If ERC-20 call failed, balance is set to `None` if `raise_on_error` is `False`.
    """

    assert address.startswith("0x")
    assert block_identifier

    # TODO: Convert web3 arg to web3factory arg to be aligned with the rest of the code
    # Handle our special MEV + Fallback (read) providers
    provider = web3.provider
    if isinstance(provider, MEVBlockerProvider):
        logger.info("Skipping MEV RPC provider, using %s for multicall", provider.call_provider)
        web3 = Web3(provider.call_provider)

    chain_id = web3.eth.chain_id

    rpc_name = get_provider_name(web3.provider)

    logger.info(
        "Looking up token balances for %d addresses, chunk size %d, gas limit %d, using provider %s",
        len(tokens),
        chunk_size,
        gas_limit,
        rpc_name,
    )

    out_address = address
    balance_of_signature = Web3.keccak(text="balanceOf(address)")[0:4]

    # Generated packed multicall for each token contract we want to query
    def _gen_calls(addresses: Iterable[str]) -> Iterable[EncodedCall]:
        for _token_address in addresses:
            yield EncodedCall.from_keccak_signature(
                address=_token_address.lower(),
                signature=balance_of_signature,
                data=convert_address_to_bytes32(out_address),
                extra_data={},
                ignore_errors=True,
                function="balanceOf",
            )

    logger.info("Fetching balances for %d tokens in chunks of %d", len(tokens), chunk_size)

    web3factory = MultiProviderWeb3Factory(web3.provider.endpoint_uri, hint="fetch_erc20_balances_multicall")

    # Execute calls for all token balance reads at a specific block.
    # read_multicall_chunked() will automatically split calls to multiple chunks
    # if we are querying too many.
    results = read_multicall_chunked(
        chain_id=chain_id,
        web3factory=web3factory,
        calls=list(_gen_calls(tokens)),
        block_identifier=block_identifier,
        max_workers=max_workers,
        timestamped_results=False,
    )

    results = list(results)

    addr_to_balance = LowercaseDict()

    for result in results:
        token_address = result.call.address

        if not result.result:
            if raise_on_error:
                raise BalanceFetchFailed(f"Could not read token balance for ERC-20: {token_address} for address {out_address}")
            value = None
        else:
            raw_value = convert_int256_bytes_to_int(result.result)
            if decimalise:
                token = fetch_erc20_details(web3, token_address, cache=token_cache, chain_id=chain_id)
                value = token.convert_to_decimals(raw_value)
            else:
                value = raw_value

        addr_to_balance[token_address] = value

    return addr_to_balance


def create_erc20_balance_call(web3: Web3, token_address: HexAddress | str, holder_address: HexAddress | str, debug: bool = False) -> ERC20BalanceCall:
    """Create a multicall wrapper for ERC-20 balanceOf call."""

    # Get ERC-20 contract instance using simple ABI
    erc20_abi = [{"constant": True, "inputs": [{"name": "_owner", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"}]

    contract = web3.eth.contract(address=Web3.to_checksum_address(token_address), abi=erc20_abi)

    # Create bound function call
    bound_function = contract.functions.balanceOf(Web3.to_checksum_address(holder_address))

    # MIGRATED: Fix missing function_identifier attribute for multicall compatibility
    if not hasattr(bound_function, "function_identifier"):
        # Try different attribute names that might contain the function identifier
        if hasattr(bound_function, "fn_name"):
            bound_function.function_identifier = bound_function.fn_name
        elif hasattr(bound_function, "name"):
            bound_function.function_identifier = bound_function.name
        else:
            # Fallback to the function name from ABI
            bound_function.function_identifier = "balanceOf"

    # Also ensure other required attributes exist
    if not hasattr(bound_function, "fn_name"):
        bound_function.fn_name = "balanceOf"

    # Debug: print available attributes
    if debug:
        print(f"bound_function attributes: {[attr for attr in dir(bound_function) if not attr.startswith('_')]}")
        print(f"function_identifier: {getattr(bound_function, 'function_identifier', 'NOT_FOUND')}")

    return ERC20BalanceCall(
        call=bound_function,
        debug=debug,
        token_address=token_address,
        holder_address=holder_address,
    )


def fetch_erc20_balances_multicall_v7(
    web3: Web3,
    address: HexAddress | str,
    tokens: list[HexAddress | str] | set[HexAddress | str],
    block_identifier: BlockIdentifier,
    decimalise=True,
    chunk_size=50,
    token_cache: cachetools.Cache | None = DEFAULT_TOKEN_CACHE,
    gas_limit=10_000_000,
    raise_on_error=True,
) -> dict[HexAddress | str, Decimal]:
    """Read balance of multiple ERC-20 tokens on an address using internal multicall implementation.

    - Fast, batches multiple calls on one JSON-RPC request
    - Uses internal Multicall3 implementation without external dependencies

    Example:

    .. code-block:: python

        def test_fetch_erc20_balances_multicall(web3):
            tokens = {
                "0x6921B130D297cc43754afba22e5EAc0FBf8Db75b",  # DogInMe
                "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",  # USDC on Base
            }

            # Velvet vault
            address = "0x9d247fbc63e4d50b257be939a264d68758b43d04"

            block_number = get_almost_latest_block_number(web3)

            balances = fetch_erc20_balances_multicall(
                web3,
                address,
                tokens,
                block_identifier=block_number,
            )

            existing_dogmein_balance = balances["0x6921B130D297cc43754afba22e5EAc0FBf8Db75b"]
            assert existing_dogmein_balance > 0

            existing_usdc_balance = balances["0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"]
            assert existing_usdc_balance > Decimal(1.0)

    :param address:
        Address of which balances we query

    :param tokens:
        List of ERC-20 addresses.

    :param block_identifier:
        Fetch at specific height.

        Must be given for a multicall.

    :param chunk_size:
        How many ERC-20 addresses feed to multicall once

    :param gas_limit:
        Gas limit of the multicall request

    :param decimalise:
        If ``True``, convert output amounts to humanised format in Python :py:class:`Decimal`.

         Use cached :py:class:`TokenDetails` data.

    :param token_cache:
        Cache ERC-20 decimal data.

    :param raise_on_error:
        See `BalanceFetchFailed`.

    :raise BalanceFetchFailed:
        balanceOf() call failed.

        When you give a non-ERC-20 contract as a token.

    :return:
        Map of token address -> balance.

        If ERC-20 call failed, balance is set to `None` if `raise_on_error` is `False`.
    """

    assert address.startswith("0x"), f"Address must start with 0x, got: {address}"
    assert block_identifier, "block_identifier must be provided"

    # Handle our special MEV + Fallback (read) providers
    provider = web3.provider
    if isinstance(provider, MEVBlockerProvider):
        logger.info("Skipping MEV RPC provider, using %s for multicall", provider.call_provider)
        web3 = Web3(provider.call_provider)

    chain_id = web3.eth.chain_id
    rpc_name = get_provider_name(web3.provider)

    logger.info(
        "Looking up token balances for %d addresses, chunk size %d, gas limit %d, using provider %s",
        len(tokens),
        chunk_size,
        gas_limit,
        rpc_name,
    )

    tokens = list(tokens)

    # Get multicall contract
    multicall_contract = get_multicall_contract(web3, block_identifier=block_identifier)

    all_calls = {}

    # Process tokens in chunks
    for i in range(0, len(tokens), chunk_size):
        token_address_chunk = tokens[i : i + chunk_size]

        # Create multicall wrappers for this chunk
        calls = [create_erc20_balance_call(web3=web3, token_address=token_addr, holder_address=address, debug=False) for token_addr in token_address_chunk]

        # Execute multicall for this chunk
        try:
            if chunk_size <= 15:
                # Use direct multicall for small batches
                batched_result = call_multicall(
                    multicall_contract=multicall_contract,
                    calls=calls,
                    block_identifier=block_identifier,
                )
            else:
                # Use batched multicall for larger chunks
                batched_result = call_multicall_batched_single_thread(
                    multicall_contract=multicall_contract,
                    calls=calls,
                    block_identifier=block_identifier,
                    batch_size=15,
                )

            all_calls.update(batched_result)

        except Exception as e:
            if raise_on_error:
                raise BalanceFetchFailed(f"Multicall failed for chunk starting at {token_address_chunk[0]}: {e}") from e
            else:
                # Set all balances in this chunk to None
                for token_addr in token_address_chunk:
                    all_calls[token_addr] = None

    # Check for failed calls if raise_on_error is True
    if raise_on_error:
        for token_address, raw_balance in all_calls.items():
            if raw_balance is None:
                raise BalanceFetchFailed(f"Could not read token balance for ERC-20: {token_address} for address {address}")

    # Convert to decimal format if requested
    if decimalise:
        result = LowercaseDict()
        for token_address, raw_balance in all_calls.items():
            if raw_balance is not None:
                try:
                    token = fetch_erc20_details(web3, token_address, cache=token_cache, chain_id=chain_id)
                    result[token_address] = token.convert_to_decimals(raw_balance)
                except Exception as e:
                    if raise_on_error:
                        raise BalanceFetchFailed(f"Could not fetch token details for {token_address}: {e}") from e
                    result[token_address] = None
            else:
                result[token_address] = None
    else:
        result = all_calls

    return result


if WEB3_PY_V7:
    fetch_erc20_balances_multicall = fetch_erc20_balances_multicall_v7
else:
    fetch_erc20_balances_multicall = fetch_erc20_balances_multicall_v6


def fetch_erc20_balances_fallback(
    web3: Web3,
    address: HexAddress | str,
    tokens: list[HexAddress | str] | set[HexAddress | str],
    block_identifier: BlockIdentifier,
    decimalise=True,
    chunk_size=50,
    token_cache: cachetools.Cache | None = DEFAULT_TOKEN_CACHE,
    gas_limit=10_000_000,
    raise_on_error=True,
    disable_multicall: bool = None,
) -> dict[HexAddress | str, Decimal]:
    """Get all onchain balances of the token.

    - Safe variant

    - Try multicall approach first

    - If it fails for some reason, fall to individual JSON-RPC API approach

    - A reason for the failure would be crappy RPC providers

    See :py:func:`fetch_erc20_balances_multicall` for usage and argument descriptions.

    :param disable_multicall:
        Disable multicall behaviour.

        If set to `None` autodetect local dev/test chain and disable based on the presence of Anvil:
        assume no multicall contract deployed there. On the mainnet fork, assume the presence of multicall contract.
    """

    # Multicall not deployed on local test chains
    if disable_multicall is None:
        disable_multicall = is_anvil(web3) and not is_mainnet_fork(web3)

    if disable_multicall:
        balances = fetch_erc20_balances_by_token_list(
            web3,
            address,
            tokens,
            block_identifier=block_identifier,
            decimalise=True,
        )
    else:
        try:
            balances = fetch_erc20_balances_multicall(
                web3,
                address,
                tokens,
                block_identifier=block_identifier,
                decimalise=decimalise,
                chunk_size=chunk_size,
                token_cache=token_cache,
                gas_limit=gas_limit,
                raise_on_error=raise_on_error,
            )
        except Exception as e:
            # RPC failure - multicall error cannot be handled gracefully.
            # Try something better.
            # https://github.com/banteg/multicall.py/issues/103
            logger.error("fetch_erc20_balances_multicall() failed with %s, falling back to single call processing of balance fetches", e, exc_info=e)
            balances = fetch_erc20_balances_by_token_list(
                web3,
                address,
                tokens,
                block_identifier=block_identifier,
                decimalise=decimalise,
            )

    return balances
