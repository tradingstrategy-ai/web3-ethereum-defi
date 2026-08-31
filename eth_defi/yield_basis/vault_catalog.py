"""Enumerate and validate the reviewed YieldBasis Factory markets."""

from dataclasses import dataclass

from eth_typing import BlockIdentifier, HexAddress
from eth_utils import to_checksum_address
from web3 import Web3
from web3.exceptions import BadFunctionCallOutput, ContractLogicError, Web3Exception

from eth_defi.yield_basis.addresses import YIELD_BASIS_ACTIVE_MARKETS, YIELD_BASIS_FACTORY, YIELD_BASIS_STABLECOIN, YieldBasisMarketReview
from eth_defi.yield_basis.contracts import fetch_yield_basis_amm, fetch_yield_basis_curve_pool, fetch_yield_basis_factory, fetch_yield_basis_lt

#: Address fields returned by ``Factory.markets()`` in canonical order.
YIELD_BASIS_FACTORY_MARKET_FIELDS: tuple[str, ...] = ("asset_token", "cryptopool", "amm", "lt", "price_oracle", "virtual_pool", "staker")


@dataclass(frozen=True, slots=True)
class YieldBasisMarket:
    """Runtime Factory tuple for a reviewed market."""

    #: Address-level product identity reviewed in source control.
    review: YieldBasisMarketReview
    #: Curve Cryptoswap pool used by the LT.
    cryptopool: HexAddress
    #: YieldBasis AMM managing the leveraged position.
    amm: HexAddress
    #: Current AMM kill-switch state.
    killed: bool

    @property
    def market_id(self) -> int:
        """Return the reviewed market ID."""

        return self.review.market_id

    @property
    def lt_address(self) -> HexAddress:
        """Return the LT share address."""

        return self.review.lt_address

    @property
    def asset_address(self) -> HexAddress:
        """Return the volatile asset address."""

        return self.review.asset_address


@dataclass(frozen=True, slots=True)
class YieldBasisScanPreparation:
    """Validated product set returned by the pre-scan."""

    #: EVM chain ID inspected by the pre-scan.
    chain_id: int
    #: Fixed block shared by all validation calls.
    block_number: int
    #: Whether the Factory identity and stablecoin were accepted.
    factory_valid: bool
    #: Stablecoin returned by the Factory, when available.
    stablecoin: HexAddress | None
    #: Individually validated reviewed products safe to publish.
    products: tuple[YieldBasisMarket, ...]
    #: Operator-visible reasons why other products were withheld.
    review_required: tuple[str, ...]


def _address(value: object) -> HexAddress:
    """Normalise a tuple or mapping address to checksum form."""

    return HexAddress(to_checksum_address(value))


def _market_tuple(value: object) -> tuple[HexAddress, ...]:
    """Extract the seven Factory Market fields from Web3 return variants.

    :param value:
        Sequence or named mapping returned by Web3.py.
    :return:
        Seven checksum addresses in canonical Factory order.
    """

    if isinstance(value, dict):
        result = tuple(_address(value[field]) for field in YIELD_BASIS_FACTORY_MARKET_FIELDS)
    else:
        result = tuple(_address(item) for item in value)
    if len(result) != len(YIELD_BASIS_FACTORY_MARKET_FIELDS):
        raise ValueError(f"YieldBasis Factory returned {len(result)} market fields, expected {len(YIELD_BASIS_FACTORY_MARKET_FIELDS)}")
    return result


def _fetch_component_validation_failure(web3: Web3, market: YieldBasisMarket, block_number: BlockIdentifier) -> str | None:
    """Validate every component used by the valuation path.

    The pre-scan checks only LT, Curve and AMM relationships that determine
    product identity, quote direction, valuation or deposit availability.

    :param web3:
        Ethereum connection used for component contract reads.
    :param market:
        Reviewed Factory product whose runtime links are checked.
    :param block_number:
        Fixed validation block shared by every component read.
    :return:
        Concise review reason, or ``None`` when the links are safe.
    """

    lt = fetch_yield_basis_lt(web3, market.lt_address)
    if _address(lt.functions.ASSET_TOKEN().call(block_identifier=block_number)) != market.asset_address:
        return "LT ASSET_TOKEN differs from Factory asset_token"
    if _address(lt.functions.STABLECOIN().call(block_identifier=block_number)) != YIELD_BASIS_STABLECOIN:
        return "LT STABLECOIN differs from reviewed crvUSD"
    if _address(lt.functions.CRYPTOPOOL().call(block_identifier=block_number)) != market.cryptopool:
        return "LT CRYPTOPOOL differs from Factory cryptopool"
    if _address(lt.functions.amm().call(block_identifier=block_number)) != market.amm:
        return "LT amm differs from Factory AMM"
    curve = fetch_yield_basis_curve_pool(web3, market.cryptopool)
    coin0 = _address(curve.functions.coins(0).call(block_identifier=block_number))
    coin1 = _address(curve.functions.coins(1).call(block_identifier=block_number))
    if coin0.lower() != YIELD_BASIS_STABLECOIN.lower() or coin1.lower() != market.asset_address.lower():
        return f"unsafe Curve coin order ({coin0}, {coin1})"
    if int(curve.functions.price_oracle().call(block_identifier=block_number)) <= 0:
        return "Curve price_oracle is zero"
    amm = fetch_yield_basis_amm(web3, market.amm)
    if _address(amm.functions.STABLECOIN().call(block_identifier=block_number)).lower() != YIELD_BASIS_STABLECOIN.lower():
        return "AMM STABLECOIN differs from reviewed crvUSD"
    if _address(amm.functions.COLLATERAL().call(block_identifier=block_number)).lower() != market.cryptopool.lower():
        return "AMM COLLATERAL differs from Curve pool"
    if _address(amm.functions.LT_CONTRACT().call(block_identifier=block_number)).lower() != market.lt_address.lower():
        return "AMM LT_CONTRACT differs from LT"
    return None


def fetch_yield_basis_scan_preparation(web3: Web3, block_number: int | None = None) -> YieldBasisScanPreparation:  # noqa: PLR0914
    """Run the narrow Ethereum pre-scan before generic lead discovery.

    A validation failure is represented in the result so callers can isolate
    YieldBasis from unrelated chain scanning. Callers should not reconcile
    products when ``factory_valid`` is false; when ``review_required`` is
    non-empty, only the individually validated reviewed products are returned.

    :param web3:
        Ethereum connection used for Factory and component reads.
    :param block_number:
        Fixed validation block; defaults to the current head.
    :return:
        Factory status, safe products and review messages.
    """

    chain_id = int(web3.eth.chain_id)
    block_number = int(block_number if block_number is not None else web3.eth.block_number)
    if chain_id != 1:
        return YieldBasisScanPreparation(chain_id, block_number, False, None, (), (f"unsupported chain {chain_id}",))
    factory = fetch_yield_basis_factory(web3)
    code = web3.eth.get_code(YIELD_BASIS_FACTORY, block_identifier=block_number)
    if not code:
        return YieldBasisScanPreparation(chain_id, block_number, False, None, (), ("Factory has no deployed bytecode",))
    stablecoin = _address(factory.functions.STABLECOIN().call(block_identifier=block_number))
    if stablecoin.lower() != YIELD_BASIS_STABLECOIN.lower():
        return YieldBasisScanPreparation(chain_id, block_number, False, stablecoin, (), (f"unexpected Factory.STABLECOIN {stablecoin}",))
    count = int(factory.functions.market_count().call(block_identifier=block_number))
    review_required = []
    products = []
    highest_reviewed_market = max(YIELD_BASIS_ACTIVE_MARKETS)
    if count > highest_reviewed_market + 1:
        review_required.append(f"unreviewed YieldBasis market IDs present: {highest_reviewed_market + 1}-{count - 1}")
    for market_id, review in YIELD_BASIS_ACTIVE_MARKETS.items():
        if market_id >= count:
            review_required.append(f"reviewed market {market_id} is absent from market_count={count}")
            continue
        values = _market_tuple(factory.functions.markets(market_id).call(block_identifier=block_number))
        asset, cryptopool, amm, lt, _price_oracle, _virtual_pool, _staker = values
        if asset.lower() != review.asset_address.lower() or lt.lower() != review.lt_address.lower():
            review_required.append(f"market {market_id} changed asset/LT tuple")
            continue
        try:
            amm_contract = fetch_yield_basis_amm(web3, amm)
            killed = bool(amm_contract.functions.is_killed().call(block_identifier=block_number))
            market = YieldBasisMarket(review, cryptopool, amm, killed)
            failure = _fetch_component_validation_failure(web3, market, block_number)
        except (BadFunctionCallOutput, ContractLogicError, Web3Exception, ValueError, TypeError, ArithmeticError) as error:
            failure = f"component validation failed: {error.__class__.__name__}"
            market = None
        if failure:
            review_required.append(f"market {market_id}: {failure}")
        elif market is not None:
            products.append(market)
    return YieldBasisScanPreparation(chain_id, block_number, True, stablecoin, tuple(products), tuple(review_required))
