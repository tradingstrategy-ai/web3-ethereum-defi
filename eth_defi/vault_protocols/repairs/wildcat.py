"""Reviewed Wildcat onboarding scope and helpers for numbered migrations.

Migration ``063-wildcat-market-leads.py`` seeds direct leads and scanner
metadata rows for the reviewed version 2 Ethereum markets. Migration
``064-wildcat-price-backfill.py`` then backfills their daily prices from
each market's registry block to the block where ordinary price scanning
takes over. Both run in one batch, with the looped scanner stopped.

The RPC-dependent steps are small module-level functions, so the numbered
migration batch test can replace them without provider access.
"""

import datetime
import logging
import pickle  # noqa: S403 - reads the trusted local reader-state pickle
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory

from eth_typing import HexAddress
from tqdm_loggable.auto import tqdm
from web3 import Web3

from eth_defi.abi import get_deployed_contract
from eth_defi.chain import get_block_time
from eth_defi.erc_4626.discovery_base import create_wildcat_market_detection, create_wildcat_potential_vault_match
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.erc_4626.vault_protocol.wildcat.constants import WILDCAT_DEPLOYMENTS, WILDCAT_ETHEREUM_CHAIN_ID, WILDCAT_READ_INTERVAL
from eth_defi.erc_4626.vault_protocol.wildcat.discovery import WildcatMarketCandidate
from eth_defi.erc_4626.vault_protocol.wildcat.offchain_metadata import redirect_metadata_cache
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDiskCache
from eth_defi.vault.base import VaultSpec
from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs import price_backfill
from eth_defi.vault_protocols.repairs.price_backfill import READER_STATE_FILE_NAME, PriceBackfillTarget

logger = logging.getLogger(__name__)

#: Block up to which the market inventory was reviewed, on 2026-11-10.
WILDCAT_INVENTORY_BLOCK = 26_386_736

#: Version 2 markets from ``MarketAdded`` events up to
#: :py:data:`WILDCAT_INVENTORY_BLOCK`: market address, registry event block
#: and its naive UTC timestamp. No market had been deregistered.
WILDCAT_V2_MARKETS: tuple[tuple[str, int, str], ...] = (
    ("0xfe7cf5680d2e59500f3938c2539fda4754876f94", 21866550, "2025-02-17T13:59:47"),
    ("0x0b6c6d557109f9e793297d54ee3fbde2e315b070", 21923723, "2025-02-25T13:44:59"),
    ("0xc9499006a149c553d18171747ed19aa7c6dd19e2", 21989438, "2025-03-06T17:47:59"),
    ("0x5ab22e9c360807f16dfa34ad5c589055d2871a5b", 21989547, "2025-03-06T18:10:23"),
    ("0xbad1b632e90ce02af868f07c572adb067eb98353", 21989700, "2025-03-06T18:41:11"),
    ("0xd18313c334bfbd366bc06991fc1e265bc7f388aa", 22016397, "2025-03-10T12:12:59"),
    ("0xe922db9bf086f1bff409bdd4d44facee371cc6e9", 22089305, "2025-03-20T16:34:59"),
    ("0xb3b823ec39f3edeb4354f76997031b3826b615c2", 22089954, "2025-03-20T18:45:23"),
    ("0x6af9c583016600dd8b509e919494b27c729c5dbd", 22152303, "2025-03-29T11:36:35"),
    ("0x2f3e0b070a80aaa17ff7650f777b944c0a1c2724", 22152328, "2025-03-29T11:41:35"),
    ("0x90772c109adc8d216967a2782eae8271b4e46c1e", 22181288, "2025-04-02T12:40:11"),
    ("0x6c70a6fd141be11b6f85ce50591d26a3d29aabf7", 22225107, "2025-04-08T15:33:35"),
    ("0xd1cf3dbb78d2bd541ea498d86565ed4c8a91ea8d", 22332630, "2025-04-23T15:34:35"),
    ("0x06fb293f7493f632db80ff0cf20bbcebbe8640ce", 22383208, "2025-04-30T16:59:59"),
    ("0x17fbc62241ba80d1709ef64ce69ae788c8eb03f3", 22430622, "2025-05-07T08:31:47"),
    ("0x6c2ce14293da8c6e19cad5a6b2a060426e2aeeb4", 22532288, "2025-05-21T15:54:11"),
    ("0x6231a192089fb636e704d2c7807d7a79c2457b07", 22536250, "2025-05-22T05:12:47"),
    ("0xc44acd62b6ab1b341798a803c5212a7d2fcc28df", 22536428, "2025-05-22T05:48:59"),
    ("0x255bb30ed2e861baf508c10fc3c682e9577a0c87", 22536455, "2025-05-22T05:54:47"),
    ("0x69e56f526637b430561e8293f3307765a046d750", 22536494, "2025-05-22T06:02:35"),
    ("0xf5d35b9e95f6842a2064a2dd24f8deede9d58f97", 22536567, "2025-05-22T06:17:11"),
    ("0x923237220cc79c6301c59851af3574d0a487c966", 22578888, "2025-05-28T04:28:23"),
    ("0xf21cd0fb0b27f966ee630e80d50d17dd090b9ebc", 22586073, "2025-05-29T04:37:59"),
    ("0x55a365f3997b2f015001c53cf6a73335e593ad8a", 22638973, "2025-06-05T14:21:59"),
    ("0xb3beb373d241378f5f1fd68ed865349257602476", 22679460, "2025-06-11T06:14:23"),
    ("0x5b833bf8c1b4d858f1d4f95768e8cfc2507eb513", 22723556, "2025-06-17T10:13:47"),
    ("0xf76a7887521a91b47c62060ba57549dec1dc88c7", 22723615, "2025-06-17T10:25:35"),
    ("0xc92b021ff09ae005cb3fccb66af8db01fc4cdf90", 22766942, "2025-06-23T11:49:59"),
    ("0x9aa7f58561812f01f1221e9718011aca78c28c17", 22773985, "2025-06-24T11:27:23"),
    ("0x14da929b9d44b74ce5937fb2527ba6abe5872b89", 22825844, "2025-07-01T17:21:23"),
    ("0xf7645afe817a1c6cdf985b1ff4750a00ec4fe505", 22876793, "2025-07-08T20:15:35"),
    ("0x5e342c3d8248937512f7cb8637bc18333b02fe18", 22884262, "2025-07-09T21:20:23"),
    ("0xc9c9ff8b00f4f6b934b5fcdbf3ff246cffe63d4a", 22884355, "2025-07-09T21:39:11"),
    ("0x4a8364a38cd1f98dbb69485d857221710bdb1dfc", 22915494, "2025-07-14T06:01:59"),
    ("0xe948fde1fe0a46f283e5dd9876de65de5aa2facf", 22915688, "2025-07-14T06:40:47"),
    ("0xdba32266e0c9315119c6f02808db8e0570d8ab83", 22918004, "2025-07-14T14:26:47"),
    ("0x262dd546703760adda0c06279508e04bd1f60dee", 22946907, "2025-07-18T15:13:23"),
    ("0x95ac07b3683727d916168cb6af516bd7446b19ad", 22979221, "2025-07-23T03:35:11"),
    ("0x593d3320fa2cb278fa24281611a58ba8db74a9ce", 22979268, "2025-07-23T03:44:35"),
    ("0x1ae65d2df9b39f7615ca2e6361eccf79500c325a", 22986090, "2025-07-24T02:39:35"),
    ("0xeafa12cc0905baabed8618584f0796f5d19da3c4", 22988595, "2025-07-24T11:03:47"),
    ("0x44b6795929986a8690039fcd9a3e7af1c9c9cc43", 23097875, "2025-08-08T17:37:11"),
    ("0x9e597911a484713f12f6efc29c49bebc64fa2144", 23098075, "2025-08-08T18:17:35"),
    ("0x8ec22d26562e72f442f32881a48c59c16d652fa0", 23098366, "2025-08-08T19:16:11"),
    ("0x50d7b234fded5535948b492d0510eae3cfa9738b", 23120561, "2025-08-11T21:43:11"),
    ("0x5a516928be2005e9852e342f71bfcc52ca5bdace", 23147144, "2025-08-15T14:50:47"),
    ("0x9fa4b300c474b86ffaa44288d2496ba1b735f9fd", 23218928, "2025-08-25T15:10:11"),
    ("0xdcd4dc1cff8bca76ebb83c08d8bf515f9abfe1be", 23239776, "2025-08-28T13:00:47"),
    ("0xf716fe4b887e052f35dffe0952d96782d4ccf7dd", 23332270, "2025-09-10T11:04:47"),
    ("0x12d64e3143fa6b8a7949982d8f80b658b1535651", 23333344, "2025-09-10T14:41:23"),
    ("0x603b29847659618813f039c73379746585b78875", 23336702, "2025-09-11T02:01:11"),
    ("0xbe8b1f70c5c20fe240cfa7e55b434545398279f3", 23378321, "2025-09-16T21:28:59"),
    ("0x7e496c5678b1722289d9c4a13c9aa53631ca7607", 23378343, "2025-09-16T21:33:23"),
    ("0x8c06d86596d671798a67e80dc44a281c8d822fd3", 23435305, "2025-09-24T20:33:11"),
    ("0x9e047d2e8970300f0461314e1b72abf7ef7792e9", 23520115, "2025-10-06T17:07:47"),
    ("0x2bc6f0402ca53aec65723dfb570d5f59bb771cfd", 23582160, "2025-10-15T09:20:59"),
    ("0x70ee31f6057034cfc89533aa1d44315fbf0828d1", 23582176, "2025-10-15T09:24:11"),
    ("0x2a1ffdd430ed6b7fcf4f8b9549154e6345bbebfb", 23631612, "2025-10-22T07:34:11"),
    ("0x7ce488a4e59bebf02eb2cd3e0c5297b85bfa54d9", 23637191, "2025-10-23T02:20:59"),
    ("0x68900c31d0a86848c5075c11e5e5e94b1dc0027c", 23637219, "2025-10-23T02:26:35"),
    ("0x2d958e4b289b188a110367208689bb6b560586a3", 23883785, "2025-11-26T15:23:35"),
    ("0x18b3794b9f0ffd999442815e537fe603b67fa029", 23924002, "2025-12-02T06:29:35"),
    ("0x42e68cfeeef97894bbc4fd17dd0fc97d6d71c24a", 24082208, "2025-12-24T11:34:11"),
    ("0x92bb70d7d8ff1618ba5a0e20fb2240ee1a133732", 24317750, "2026-01-26T08:16:23"),
    ("0xaa4677627d05775dafc410d7e3aff5702aae27f8", 24341269, "2026-01-29T15:00:35"),
    ("0x02e0415e828a5f97309f93f001885b5db8a87d71", 24488593, "2026-02-19T04:47:11"),
    ("0x0886dc1d5db7288e2818a80c308de8eb2f13790c", 24488621, "2026-02-19T04:52:47"),
    ("0xe79ca848ae0182fce1db61a968b89627c9a0c948", 24520188, "2026-02-23T14:28:35"),
    ("0x1a47a2c57ba169394c7d28118b8ce0baa6e22bef", 24534806, "2026-02-25T15:24:35"),
    ("0x3747a7d6c887348fe249fdff29145b25cf0311f9", 24589812, "2026-03-05T07:36:23"),
    ("0x33ac9b224c8a189d2332aecf021897f1535373ab", 24628116, "2026-03-10T15:59:35"),
    ("0x7244b84918dbfb50e62320fc0e6031a449deea28", 24729786, "2026-03-24T20:28:35"),
    ("0x6053e88b1ac0dce4ccac47122cbecd6598365c70", 24833847, "2026-04-08T08:39:47"),
    ("0x2393d8c586c1429cec33ca6c7e7c15dcaa06fb25", 25020151, "2026-05-04T07:30:23"),
    ("0x44239265e4c63573070048fecd83a6b1e324412c", 25041481, "2026-05-07T06:51:35"),
    ("0xf0b19c858e8ad544b555129ecb2d4d5e83abff6b", 25251018, "2026-06-05T11:56:59"),
    ("0x0dfc2d7230ee460ca0e0d77eab86ed44f9b254cd", 25272731, "2026-06-08T12:35:47"),
    ("0xa8139699360b255c5c02d0616e75f20b92adf1c3", 25272777, "2026-06-08T12:44:59"),
    ("0x5285766d5058d519bf7b37dce0a8f9a4ce008bed", 25336236, "2026-06-17T09:01:11"),
    ("0x20632bd54e16fcbcd35dcb2c8882a8a8802ad38d", 25895380, "2026-09-03T07:47:11"),
    ("0xac422731a936bb6c0b789191f97f2f60e75bc395", 26198409, "2026-10-15T14:07:35"),
    ("0x3e285f49c52888d38cc09682e32febfff2dd9996", 26241122, "2026-10-21T12:57:59"),
)

#: Registered version 1 markets, which are not supported: five are closed and
#: the seven held about $61 in total on 2026-11-10.
WILDCAT_V1_MARKETS: frozenset[str] = frozenset(
    {
        "0x25083923f2174c5ff00d26cd6bc497221713c5b9",
        "0x50ebdf73a0df61b782cea489e8102b3bfde0bda6",
        "0x5850afc80561932b0abb63dd13cdc129395323a3",
        "0x605309f21c1864bb0522781a2f97b91fe3a48601",
        "0x691e1aa048f995890caa47a5ac0b61a9a846bdc8",
        "0x90d0ff1a7b1abef69eaf3952641058673439e2e3",
        "0xd6440bd3c97e8bfbdc311cbbb50ada03ade4810a",
    }
)


def iter_wildcat_market_candidates(cursor_block: int) -> Iterator[WildcatMarketCandidate]:
    """Yield the reviewed version 2 markets registered by the lead cursor.

    Markets registered after the stored lead cursor are not deployed at the
    metadata block, and the ordinary lead scan discovers them.

    :param cursor_block:
        Stored Ethereum lead scan end block.
    :return:
        One candidate per market, with its ``MarketAdded`` block and time.
    """
    for address, block, timestamp in WILDCAT_V2_MARKETS:
        if block > cursor_block:
            continue
        yield WildcatMarketCandidate(
            chain=WILDCAT_ETHEREUM_CHAIN_ID,
            address=HexAddress(address),
            created_block=block,
            created_at=datetime.datetime.fromisoformat(timestamp),
        )


def create_ethereum_web3() -> Web3:
    """Connect to the configured Ethereum archive RPC.

    :return:
        Web3 connection from ``JSON_RPC_ETHEREUM``.
    """
    return create_multi_provider_web3(read_json_rpc_url(WILDCAT_ETHEREUM_CHAIN_ID))


def check_registry_coverage(web3: Web3, cursor_block: int) -> None:
    """Fail if the registry at the lead cursor has a market outside the inventory.

    Markets registered after the stored lead cursor are discovered by the
    ordinary lead scan. A market registered between
    :py:data:`WILDCAT_INVENTORY_BLOCK` and the cursor would be missed, so the
    inventory must be refrozen.

    :param web3:
        Ethereum archive connection.
    :param cursor_block:
        Stored Ethereum lead scan end block.
    :raise ValueError:
        If an unreviewed market is registered at the cursor.
    """
    deployment = WILDCAT_DEPLOYMENTS[WILDCAT_ETHEREUM_CHAIN_ID]
    registry = get_deployed_contract(web3, "wildcat/WildcatArchController.json", deployment.arch_controller)
    registered = {market.lower() for market in registry.functions.getRegisteredMarkets().call(block_identifier=cursor_block)}
    unreviewed = sorted(registered - {address for address, _, _ in WILDCAT_V2_MARKETS} - WILDCAT_V1_MARKETS)
    if unreviewed:
        message = f"Wildcat markets registered by lead cursor {cursor_block:,} are missing from the reviewed inventory: {unreviewed}"
        raise ValueError(message)


def build_market_rows(web3: Web3, candidates: list[WildcatMarketCandidate], block: int) -> dict[VaultSpec, dict]:
    """Build scanner metadata rows for markets through the ordinary scan record.

    Token metadata and Wildcat app responses go to temporary caches, so a
    preview writes no provider cache.

    :param web3:
        Ethereum archive connection.
    :param candidates:
        Markets without an existing metadata row.
    :param block:
        Metadata source block.
    :return:
        Rows keyed by vault.
    """
    rows = {}
    with TemporaryDirectory(prefix="migration-wildcat-") as directory:
        token_cache = TokenDiskCache(Path(directory) / "tokens.sqlite")
        try:
            with redirect_metadata_cache(Path(directory) / "wildcat"):
                for candidate in tqdm(candidates, desc="Building Wildcat market rows"):
                    rows[VaultSpec(candidate.chain, candidate.address)] = create_vault_scan_record(web3, create_wildcat_market_detection(candidate), block, token_cache)
        finally:
            token_cache.close()
    return rows


def read_price_cursor(data_dir: Path, chain_id: int) -> int:
    """Read the block at which ordinary price scanning resumes for a chain.

    The scanner starts every vault without its own reader state, such as a
    newly added market, at the chain's highest stored ``last_block`` plus
    one.

    :param data_dir:
        Pipeline data directory holding the hourly reader-state pickle.
    :param chain_id:
        EVM chain id.
    :return:
        Highest stored ``last_block`` of the chain.
    :raise ValueError:
        If the chain has no stored price scan.
    """
    with (data_dir / READER_STATE_FILE_NAME).open("rb") as inp:
        states = pickle.load(inp)  # noqa: S301 - trusted local production reader-state pickle.
    cursor = max(((state.get("last_block") or 0) for spec, state in states.items() if spec.chain_id == chain_id), default=0)
    if not cursor:
        message = f"Chain {chain_id} has no stored price scan to continue from"
        raise ValueError(message)
    return cursor


def read_lead_cursor(context: MigrationContext) -> int:
    """Read the stored Ethereum lead scan end block.

    :param context:
        Migration attempt holding the staged metadata.
    :return:
        Lead cursor block.
    :raise ValueError:
        If the Ethereum lead scan has never run.
    """
    cursor_block = context.metadata().last_scanned_block.get(WILDCAT_ETHEREUM_CHAIN_ID)
    if cursor_block is None:
        message = "Ethereum lead scan has not run; nothing to seed"
        raise ValueError(message)
    return cursor_block


def seed_market_leads(context: MigrationContext) -> None:
    """Insert reviewed Wildcat leads and metadata rows after checking registry coverage.

    An existing lead keeps its counters and only gains the Wildcat candidate.
    Existing metadata rows are kept. New rows are read at the lead cursor.
    Every new row must be a complete Wildcat row before anything is committed,
    so a transient read failure fails the migration and a retry rebuilds the
    rows, instead of persisting a broken placeholder.

    :param context:
        Guarded migration attempt and shared metadata database.
    :raise ValueError:
        If the registry has an unreviewed market or a row could not be read.
    """
    database = context.metadata()
    cursor_block = read_lead_cursor(context)
    web3 = create_ethereum_web3()
    check_registry_coverage(web3, cursor_block)
    candidates = list(iter_wildcat_market_candidates(cursor_block))

    missing = [candidate for candidate in candidates if VaultSpec(candidate.chain, candidate.address) not in database.rows]
    rows = build_market_rows(web3, missing, cursor_block)
    broken = sorted(spec.as_string_id() for spec, row in rows.items() if row.get("Protocol") != "Wildcat" or row.get("_rpc_failure_category"))
    if broken:
        message = f"Could not read Wildcat metadata for {broken}; retry the migration"
        raise ValueError(message)

    inserted = updated = 0
    for candidate in candidates:
        spec = VaultSpec(candidate.chain, candidate.address)
        lead = database.leads.get(spec)
        if lead is None:
            database.leads[spec] = create_wildcat_potential_vault_match(candidate)
            inserted += 1
        elif getattr(lead, "wildcat_market_candidate", None) is None:
            lead.wildcat_market_candidate = candidate
            updated += 1

    database.rows.update(rows)
    context.report(cursor_block=cursor_block, inventory_block=WILDCAT_INVENTORY_BLOCK, inserted_leads=inserted, updated_leads=updated, inserted_rows=len(missing))
    context.commit_metadata()


def backfill_market_prices(context: MigrationContext) -> None:
    """Backfill reviewed markets' daily prices up to the price scan cursor.

    New markets have no price rows yet, and the scanner starts them at the
    chain's price cursor. Each range therefore runs from the market's
    ``MarketAdded`` block to the cursor block plus one, exclusive, so the
    backfill and ordinary scanning meet without overlap. Markets registered
    after the cursor are left to ordinary scanning.

    :param context:
        Guarded migration attempt and shared metadata database.
    :raise ValueError:
        If a reviewed market has no Wildcat metadata row.
    """
    database = context.metadata()
    end_block = read_price_cursor(context.data_dir, WILDCAT_ETHEREUM_CHAIN_ID) + 1
    targets = [PriceBackfillTarget(candidate.chain, candidate.address, candidate.created_block, end_block) for candidate in iter_wildcat_market_candidates(read_lead_cursor(context)) if candidate.created_block < end_block]
    for target in targets:
        row = database.rows.get(target.as_spec())
        if row is None or row.get("Protocol") != "Wildcat":
            message = f"Wildcat backfill target {target.as_spec().as_string_id()} is missing or not a Wildcat market; run 063-wildcat-market-leads.py first"
            raise ValueError(message)
    # Lending markets do not move fast, so daily reads avoid unnecessary churn
    step = WILDCAT_READ_INTERVAL // datetime.timedelta(seconds=get_block_time(WILDCAT_ETHEREUM_CHAIN_ID))
    context.report(price_cursor_end_block=end_block, backfill_targets=len(targets), backfill_step_blocks=step)
    if not targets:
        # Only when no reviewed market is registered by the lead cursor, as in isolated test data
        logger.info("No Wildcat market is registered by the lead cursor; nothing to backfill")
        return
    # Module attribute access lets the numbered batch test replace the helper.
    price_backfill.run_price_backfill(context, targets, step=step)
