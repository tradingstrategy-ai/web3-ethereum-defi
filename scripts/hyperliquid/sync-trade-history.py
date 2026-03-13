"""Sync trade history for whitelisted Hyperliquid accounts.

Fetches fills, funding payments, and ledger events for whitelisted accounts
and stores them in a DuckDB database. Supports incremental sync that
accumulates data beyond the 10K fill API limit.

Usage:

.. code-block:: shell

    # Sync specific addresses
    ADDRESSES=0x1e37a337ed460039d1b15bd3bc489de789768d5e,0x3df9769bbbb335340872f01d8157c779d73c6ed0 \
      poetry run python scripts/hyperliquid/sync-trade-history.py

    # Auto-discover vaults with peak TVL >= $100k
    MIN_VAULT_PEAK_TVL=100000 \
      poetry run python scripts/hyperliquid/sync-trade-history.py

    # Non-interactive mode (skip confirmation prompt)
    MIN_VAULT_PEAK_TVL=100000 INTERACTIVE=false \
      poetry run python scripts/hyperliquid/sync-trade-history.py

    # Sync curated top traders (by trade count, account value > $25k)
    SCAN=top_traders INTERACTIVE=false \
      poetry run python scripts/hyperliquid/sync-trade-history.py

    # With debug logging
    LOG_LEVEL=info ADDRESSES=0x1e37a337ed460039d1b15bd3bc489de789768d5e \
      poetry run python scripts/hyperliquid/sync-trade-history.py

Environment variables:

- ``LOG_LEVEL``: Logging level (debug, info, warning, error). Default: warning
- ``TRADE_HISTORY_DB_PATH``: DuckDB path. Default: ~/.tradingstrategy/vaults/hyperliquid/trade-history.duckdb
- ``ADDRESSES``: Comma-separated addresses to add to whitelist and sync.
  If not set, syncs all existing whitelisted accounts.
- ``LABELS``: Comma-separated labels matching ADDRESSES (optional).
- ``MIN_VAULT_PEAK_TVL``: Auto-discover vaults with peak TVL >= this value (USD).
  Reads from cleaned vault prices Parquet. Mutually exclusive with ADDRESSES.
- ``PARQUET_PATH``: Path to cleaned vault prices Parquet (for MIN_VAULT_PEAK_TVL).
- ``INTERACTIVE``: Set to ``false`` to skip confirmation prompts (for CI/cron). Default: true.
- ``MAX_WORKERS``: Parallel threads (default: 1, DuckDB single-writer).
- ``WEBSHARE_API_KEY``: Webshare proxy API token. When set, each worker gets
  its own proxy for API requests, with automatic rotation on failure.
- ``SCAN``: Address source mode. Currently supports ``top_traders`` which uses
  the curated ``TRADERS_TO_WATCH`` set (top traders by trade count with
  account value > $25k). Mutually exclusive with ADDRESSES.
- ``WEBSHARE_PROXY_MODE``: Proxy mode — "backbone" (residential/server, default) or "direct" (datacenter).
"""

import logging
import os
import sys
from pathlib import Path

from tabulate import tabulate

from eth_defi.event_reader.webshare import load_proxy_rotator, print_proxy_dashboard
from eth_defi.hyperliquid.constants import HYPERLIQUID_SYSTEM_VAULT_ADDRESSES
from eth_defi.hyperliquid.session import create_hyperliquid_session
from eth_defi.hyperliquid.vault_filter import fetch_vaults_by_peak_tvl
from eth_defi.hyperliquid.trade_history_db import (
    DEFAULT_TRADE_HISTORY_DB_PATH,
    HyperliquidTradeHistoryDatabase,
)
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)

#: Curated set of top Hyperliquid traders by trade count with account value > $25k.
#: Generated from ``top-traders-by-trade-count.py`` with ``TOP_N=500``.
#: Last updated: 2026-03-13.
TRADERS_TO_WATCH: set[str] = {
    "0x023a3d058020fb76cca98f01b3c48c8938a22355",
    "0x03488d119ff425c17521e1fb0fafaf97c69902f3",
    "0x034d4f0b00827e4ac77d910071377f707ddd23d3",
    "0x03b9a189e2480d1e4c3007080b29f362282130fa",
    "0x05e6ffff89d05b7ed820dfedb665e430252c8132",
    "0x06c51f1fc98ff7cdee2b076ec5c8999af829b221",
    "0x07fd993f0fa3a185f7207adccd29f7a87404689d",
    "0x0898b54fbba3296d7c4ac58db87d09fa4cda0896",
    "0x0b35427aa2f8193fe426b2332878acce6f34ae61",
    "0x14a4914df70e599bdc5baf74b7b51773499dbe21",
    "0x15be61aef0ea4e4dc93c79b668f26b3f1be75a66",
    "0x162cc7c861ebd0c06b3d72319201150482518185",
    "0x17c5b01bc17593bd9647e98fe7f292b32f6ed4f0",
    "0x185611e0343c14f53a4406891cb0fdc9e81808ae",
    "0x18ae3ec77652efd09597a577c100d97c1169fbb1",
    "0x18cde66120c9195fb6e50a4b1e13bce4c85d1300",
    "0x193c6bcb33eab0b35989f2b6d1acc922b66409f2",
    "0x1d2a3b568e82678f22df040485b8cc006f3d0ea6",
    "0x1dd4af3383fce2ee4a40d9fb6766cbfcce2ddbce",
    "0x1e37a337ed460039d1b15bd3bc489de789768d5e",
    "0x1e8ea5bc8b3e3eddd35c203807a8f9cf158d266d",
    "0x2025137a136bea7446deba681cbfc7cf1970840e",
    "0x21a2b0fcdea50c75092cb69539940ea17942a192",
    "0x223537ac9a856c31f4043e86ced86bb29f06653e",
    "0x23e43318cb56c1efca72bc8f2c4ba5ce94d73db1",
    "0x24a81ade94952eff40bdb081a230f8fdd4a4313c",
    "0x258a4b8a8447c61aabf1bef9bb14ee4d69efff03",
    "0x25bdd0a95d95319033360a2989219c697fc8a7d9",
    "0x279dbeaae93533a5c4607712b027b04aff62d625",
    "0x27c9fa86c91b84ddfa15de58c482ff662498d65d",
    "0x298e21ddd7320c393afdbf0c59dc4e4034a4b68d",
    "0x2a1d4a8adc73ed606a5376b525e58792c64f8ca0",
    "0x2f55331b4fa24efa8fbfeafb896669c0733f827a",
    "0x31b1ac74a39fa92604c3bccac20453f2e3eebfaa",
    "0x31dea2516beee92135b96f464eeec3cf292a13f2",
    "0x33ad48ed92e3e91a98278f0f7456eae6eafa516e",
    "0x34827044cbd4b808fc1b189fce9f50e6dafae7c9",
    "0x348e5365acfa48a26ada7da840ca611e29c950ef",
    "0x34fb5ec7d4e939161946340ea2a1f29254b893de",
    "0x35f13dc71426d4a429cf8bad69c4b39abbbf9896",
    "0x365e0c115f1ca1adcb42fd21142873493df7f880",
    "0x369daedbefcb30b82154338c8fd613a70c60d251",
    "0x3895d155b005191686476a39580d43258a518e46",
    "0x39475d17bcd20adc540e647dae6781b153fbf3b1",
    "0x39bac00809cae627f52e4a82f95f4f67d5b269bd",
    "0x3a03c75df10d3f18a212649ce0d28b03b108b812",
    "0x3bcae23e8c380dab4732e9a159c0456f12d866f3",
    "0x3ca146d2692f61746a28fa926dc9d18086ce37c6",
    "0x3e051c89cd06e6867ce98c758fcc665d2148e1bb",
    "0x3e781befd071c59d820725f70ce109298f750706",
    "0x3f3e7878cbde6da00629f6f6115f367f8ef316d2",
    "0x3faf35a220e0d6a3bc3cce4ca54454aba68cfd6a",
    "0x4061eada41227256d5c7d501b562824a1b717a36",
    "0x417e99d72cfef3a31d9a525c41236912752f2c1e",
    "0x4264b5a132e4f263d6de2e0d01512a99ea21ec6e",
    "0x4268c9e3c4476f91baabd87cff42feb378dc03bd",
    "0x42be8a7a39db60abe34d878f92ff22243cfde2b3",
    "0x4334ff1dd9fa46667b718a6f8a489c3d0079b44b",
    "0x44a3e107c375c1abfa28c1ac5e92bc85f0b435dd",
    "0x47649d96f78ea3a70db88c745913484cc558c7ae",
    "0x483e1d004247248d516865b9b9e893d48359ce10",
    "0x48b5c751c2d63ac9134ce31f8d3e08b6836f6ee0",
    "0x48ea62a2cc8391fbbe210e8ee89db573a8ec145f",
    "0x493364de7f4a39ed24f9c68ece229973f324d369",
    "0x4a19c9768629266caad38bd6857c6a4161532c19",
    "0x4d0976dabd56385d1b54867724c64159aef12bc4",
    "0x4d3e7f2c578b5edc58eb352da6e73b1184db7a4a",
    "0x4e7cfe8e3e20284ba246978abaf301c5fb0ccc9c",
    "0x4ee1d18e68743d9e3e90e43363342283acc9a36f",
    "0x4f65e90a62eae8cc8c85afd6c856874c99970934",
    "0x50e8db5da4198e49197743f8006b318cb6a08b49",
    "0x51f63885534cb494f4592a0311551f369bff2b39",
    "0x52e842bd8299095eae43110dac619302f545456a",
    "0x53ac3da992efb3785a615f281380ed7e591fbe79",
    "0x54f9524155c82f1f3f2c5e086ba513df442a18ef",
    "0x56667d7d8e0e5c529d96f6d92ff2ce2414c5910d",
    "0x57dd78cd36e76e2011e8f6dc25cabbaba994494b",
    "0x587d923fe6ab80db02845bf9ab30587db6594690",
    "0x597077efeb1bca71deaf556bac4d8e28a4595bef",
    "0x59fd165b93300f7da94b38296acaada620b877d5",
    "0x5b31a472f8156568bad291072dcce4faa43fb05b",
    "0x5f3f530aae8cd83c2893ddf786f188fe6785c42f",
    "0x5fffee2555a15899ad656c1a80f1b35cd0b2c0c1",
    "0x621c5551678189b9a6c94d929924c225ff1d63ab",
    "0x63fea639f62c51888e41434640f7707d638901f5",
    "0x6628f829994f1756257e6c2468dabd675a530a1d",
    "0x66701345620a1d2d81e37f9fd8c74491e8c7d525",
    "0x6859da14835424957a1e6b397d8026b1d9ff7e1e",
    "0x68d017f8c75d22af6a48aa6f2f697c658ec15599",
    "0x6ba889db7f923622d3548f621ecc2054b80c1817",
    "0x6ce8a609b7e76d22b3cf477c16453ef9ec9a188a",
    "0x6da22bdd4ed69704c4912ced8ba988e93e6b0a83",
    "0x6dcade414ac7baeeae0ab8a0298728e521c7ddf5",
    "0x6de62240b8fec4bc7201b828934efafe9253ef7b",
    "0x6ecbb68c07bd4be7adb7f0365e9263e382aeca2d",
    "0x6f0b45a68d3c32826bdb7140cb4c5ecf11385e2f",
    "0x727956612a8700627451204a3ae26268bd1a1525",
    "0x73e5c11a8a9820afb1a25aa89787ba025b84e60e",
    "0x75f3665323cce024368870bfd89db0c21e1ce76b",
    "0x7717a7a245d9f950e586822b8c9b46863ed7bd7e",
    "0x7a9d45682babd8a2d79181623c75a7f615990b27",
    "0x7af0b7a51c6bbf3970a5d0c79fc958f3d5ec58b7",
    "0x7c930969fcf3e5a5c78bcf2e1cefda3f53e3c8fd",
    "0x8050706eb79e2e1128a3a96b50ece6e74e1428de",
    "0x80f80b9cbad775b4fb9d699d34f5c5acc4615bdb",
    "0x8184405076b61ab78013f16393322eaf0d0a01a1",
    "0x83027df4f3f749a1f9dd8bbc976eee49653c1d72",
    "0x84d5c9e6a6944356a01ffc9728610227bd1a670e",
    "0x856c35038594767646266bc7fd68dc26480e910d",
    "0x8587051491522599dfcc23de01ae59b622b498b3",
    "0x880ac484a1743862989a441d6d867238c7aa311c",
    "0x89a4e7b411cebfcf66347935bb84f406eab4be29",
    "0x8b91cec98fa4097e224fa08e9225e8720fa27e21",
    "0x8cc94dc843e1ea7a19805e0cca43001123512b6a",
    "0x8dbc2ef3943cdac4df2d18f31099121d5f250282",
    "0x8e80c4b533dd977cf716b5c24fd9223129272804",
    "0x8f8ce8014e0e41aab563627beede5b6861869fa2",
    "0x9266865bb6afb4c4f618544dd3b8c970f17aa664",
    "0x92c7d53dc3bac14722b281e74a3feba8f7ba040e",
    "0x9690bcfc1639abff887c8fb62b48ab787b2dba3f",
    "0x984e16e759ac515784f90dd88443260e482c63c1",
    "0x9878b73eba4475a8ca8bb9be119c3f85df988e9d",
    "0x9970d443386312f7839f3adbe2d607b024ac20c6",
    "0x9a3664a58b6a6579319c44bdd06a381ec8b47b6a",
    "0x9b883651182137f33efda174f831d9918182afe0",
    "0x9e02aca9865e1859bb7865f6f64801e804a173df",
    "0x9f3e77cb89df964003053aa5b438e5697c77f4f9",
    "0xa1e03bcb1a36da135ca61b86dd1d6da9a9357489",
    "0xa23dd5b874b8814e95d5fbb285cea68081e53488",
    "0xa24c5ca26c41cc52aeac1d6886d37f6dd5d6949f",
    "0xa289ee1e56c0d5d041db762e6123e78af0f7d9ad",
    "0xa38f19c4ccbcfd64cff676d72c436f7e6239dccf",
    "0xa3fa62c6b9e0ec7da92b7eca0b7a777e6e7f4e60",
    "0xa4a6e0fd7528a6f5c6ccbb3240ba8a2f825446cf",
    "0xa4ed966667b986c6a29aca2f290e861d31de6e7d",
    "0xa4f59cb283f23a8427e9e78e3882b4d2a1ce771b",
    "0xa54e0ec5f263ba19cb4ca301874ff866f05bf96e",
    "0xa59362891a6910c0d16171ac10ad43ba994bbcde",
    "0xa716706dcad2e4460a53b304d703b9a2c4450b22",
    "0xa7d1af5afed57829182b637d78fedff2def2bcc8",
    "0xa811c97b3006406f302dc0c2c2d50a22869ec80b",
    "0xa880d6cc607a05ea617307ab3b0d335e8d8424ee",
    "0xab83abe96a71d235a90b8788940dbd1d3707b03b",
    "0xac43acc4f2a5bc405ec61d3a0ad7eb9d57f8dba3",
    "0xac7476e14f768e3e67c195e79c2490dd20c70127",
    "0xacf38fc1301382b2d462bf4855abe80552923a7e",
    "0xae7a0f9f663bb54bfe95378c7e3de7dd6e28d6bc",
    "0xaeadec409b7b31b32063dcc280e8efbb86441eb1",
    "0xaf9f722a676230cc44045efe26fe9a85801ca4fa",
    "0xb1c517a30cb7cea401a538f6ac8160e75bba65dd",
    "0xb2b1d1a7a034d9209a627e754ebb8d24988ca23a",
    "0xb3162a3c788399d9ec236c67a5af083dd78c8022",
    "0xb3e343f5bcecced6325928a2c199b9a161f3e238",
    "0xb4321b142b2a03ce20fcab2007ff6990b9acba93",
    "0xb537ef40717b400ee2c38a200d859a37ab577e9a",
    "0xb7e7d0fdeff5473ed6ef8d3a762d096a040dbb18",
    "0xba6de73b64eca3ff95c4ab7e5328912c360bd003",
    "0xbb828515677729c4db2f790a52cf14b1c659e19f",
    "0xbbf7d7a9d0eaeab4115f022a6863450296112422",
    "0xbd9054cc7b3b51010eea4f456c649639386998cf",
    "0xbda495ebf6fc94d61f9a7f22b9877273378ed82b",
    "0xbfa317b7468a97e4e6a7a3c138b86ca0b7395477",
    "0xbffe2b5ba52516effa2f15ee674d1215bfc8ab04",
    "0xc06103cbd0ba469640d4139621977f65ca8e7041",
    "0xc1914d36f97dc5557e4df26cbdab98e9c988ef37",
    "0xc1f88c6d36032e55932bc14537acfa5abed355c9",
    "0xc1fce740d83a60de67d039aa927a678ff78c202f",
    "0xc1fd39727cd10fe97ccac228888f025309560a93",
    "0xc2e137ea6b79a7992dd54d44ee8223f2c7e13e22",
    "0xc30c7ea910a71ce06ae840868b0c7e47616ba4c9",
    "0xc3f43a41a396e5049d6f96c7d444fc6c315b738a",
    "0xc5ed4501500fcbfb2b88fb7f0aa52f834ed44346",
    "0xc799df4baa69044f59a3711f180a31e352829ba3",
    "0xc86242898f4b63883f05628d8cf2c326071797b8",
    "0xc926ddba8b7617dbc65712f20cf8e1b58b8598d3",
    "0xca163b98cbc2ad5b08b01ae7bcb4bef68b9161ad",
    "0xcbec2fd28c44cc34f69ce9e98029595157f47096",
    "0xcd353d93f3f9fc70f248dccdc944da768b55d6b2",
    "0xcdf99cc8fe5535a8c543cd38887c494597502158",
    "0xcfea17596c15e0f4fe3ba9cab1fcbe804bbbdbbf",
    "0xd00b847831d886330f8cdaaa7c2ab70329c0c262",
    "0xd0254dd9c10498b256b6ec3bd8de37a9ba4329de",
    "0xd02928c445d780ea954fe0b6f0be0f6cb9727678",
    "0xd053201c362f5da8ab549fd3328d990ad7ad727b",
    "0xd080b654474d679c03df4fd676aa818c1adfa608",
    "0xd11a880a60b889f9e88d260a3be04accd60c0efe",
    "0xd158ab29974537b266d37ef88ae71ff4334bab69",
    "0xd393fc74c7d944b8bb3c8e922cc9fa7fbcfa8c5d",
    "0xd4c1f7e8d876c4749228d515473d36f919583d1d",
    "0xd5efacf049100c0f7f5629cb4dd024df5aa07040",
    "0xd63c228ea09e4d999f5fb3ed7d3529756e3a724f",
    "0xd6e1a6f7bb47aca69aaa511210227d40926ff256",
    "0xd6ef5649200101eb44ce8949f4cfb167b0c5a07a",
    "0xd77a0944d0039e92f3309f9cf52b6926b7c14b6d",
    "0xd94606128b20680539a719b07bca6cbc30786a28",
    "0xd9c1ef5e179d706c4296b3abccbba362eb24158b",
    "0xde236b0b3b367d5ad2a2c42b914bded79d03fe28",
    "0xdf0ac4977091a5de1d32b280c45f7a09c97f381c",
    "0xdf52df044f1b150dfa79590909cdac6ad83d59fc",
    "0xe056eebc2c7acfc782d47ebe18ad71735480f72a",
    "0xe15ec2db7dcb1fced329e9b38865ce546e688764",
    "0xe2d2dbae54981833479038c70425e1e3ce6680ec",
    "0xe4d933fb1e35c6e6a489ccc65561094e39efcb76",
    "0xe778da1e2285d712d7de5d12ab017296d9ce3fe9",
    "0xe86b057f5eb764c9738d6b0d38170befd0723664",
    "0xe8b6e52ff40bf5ff32c7e655064a8d42814faddf",
    "0xe8de5d2ae47fa0a00d1de7a36b2a2d75b63a80f7",
    "0xe8e715d222e9684bca78723d12b84f16a3e354c3",
    "0xe8ef95e07161df78552da11fd95d287dd6d65201",
    "0xea4fc1ae43d644b35e6874c32c49516ccb21df32",
    "0xeaa123246b396028fc8fea0175f013c13e097157",
    "0xeb6eda4756f831824cd28e568ef8adcff35016e3",
    "0xeb7f0a03ac25675c21b419ec91f7aa2e743e40bd",
    "0xeb83d07a84ceb12ae6296fe10e880c05efb9cf7c",
    "0xec7c3257d80aa80d06d996517020744e40a70b05",
    "0xecb63caa47c7c4e77f60f1ce858cf28dc2b82b00",
    "0xecc391e0de3f730693c5ee71aa40f451eab5a470",
    "0xee162a5a60829bd346f0c1ac3514b21fe5f4b290",
    "0xee772e29e31b9972e1b683b04944bd9937ac0304",
    "0xef5fdcefdb38803a654ee9f3539bed8058443d99",
    "0xefd3ab65915e35105caa462442c9ecc1346728df",
    "0xf032648dfd22c4d81588bd83e09ab89cfe816705",
    "0xf1489077fa6df778a411a59b393774e7d0cde5a1",
    "0xf21cd17b9db49fdb21bfdf714558cf8bde3221bd",
    "0xf27ebb91ea420f73b59b205ac7e0b77a90ec8f3c",
    "0xf31eb7ad41eaf39ba6a9868c45422ae774ad887a",
    "0xf36c562be0598b8ce109998b6aa2d53ffcd8316b",
    "0xf7d72d9d3d5b985854e2a917c0c5bcc365235c85",
    "0xf9109ada2f73c62e9889b45453065f0d99260a2d",
    "0xfad17af4900f3d3afa663103f58b41607ee45d70",
    "0xfc667adba8d4837586078f4fdcdc29804337ca06",
    "0xfc8c156428a8e48cb8d0356db16e59bec4c0ecea",
    "0xfdba13d24cb423a393603d72ca80f31b251a3727",
    "0xfe72c242af1a4ca8114c6a170ba4a823785241ce",
    "0xfe96dd504543165efeb7ab545d161d0ba112f84e",
}


def main():
    default_log_level = os.environ.get("LOG_LEVEL", "warning")
    setup_console_logging(default_log_level=default_log_level)

    db_path_str = os.environ.get("TRADE_HISTORY_DB_PATH")
    db_path = Path(db_path_str).expanduser() if db_path_str else DEFAULT_TRADE_HISTORY_DB_PATH

    addresses_str = os.environ.get("ADDRESSES", "").strip()
    addresses = [a.strip().lower() for a in addresses_str.split(",") if a.strip()]

    labels_str = os.environ.get("LABELS", "").strip()
    labels = [l.strip() for l in labels_str.split(",") if l.strip()] if labels_str else []

    min_peak_tvl_str = os.environ.get("MIN_VAULT_PEAK_TVL", "").strip()
    scan_mode = os.environ.get("SCAN", "").strip().lower()
    interactive = os.environ.get("INTERACTIVE", "true").strip().lower() != "false"

    max_workers = int(os.environ.get("MAX_WORKERS", "1"))

    print("Hyperliquid trade history sync")  # noqa: T201
    print(f"DuckDB path: {db_path}")  # noqa: T201
    print(f"Max workers: {max_workers}")  # noqa: T201

    # Scan mode: use curated top traders set
    if scan_mode == "top_traders" and not addresses:
        addresses = sorted(TRADERS_TO_WATCH)
        print(f"\n  Using TRADERS_TO_WATCH: {len(addresses)} traders with account value > $25k")  # noqa: T201

    # Auto-discover vaults by peak TVL
    if min_peak_tvl_str and not addresses:
        min_peak_tvl = float(min_peak_tvl_str)

        parquet_path_str = os.environ.get("PARQUET_PATH", "").strip()
        parquet_path = Path(parquet_path_str).expanduser() if parquet_path_str else None

        vaults = fetch_vaults_by_peak_tvl(
            min_peak_tvl=min_peak_tvl,
            parquet_path=parquet_path,
        )

        if not vaults:
            print(f"\nNo vaults found with peak TVL >= ${min_peak_tvl:,.0f}")
            return

        # Display discovered vaults
        print(f"\nVaults with peak TVL >= ${min_peak_tvl:,.0f}:")
        print()
        discovery_rows = [[v["name"] or v["address"][:16], v["address"][:16] + "...", f"${v['current_tvl']:,.0f}", f"${v['peak_tvl']:,.0f}"] for v in vaults]
        print(
            tabulate(
                discovery_rows,
                headers=["Name", "Address", "Current TVL", "Peak TVL"],
                tablefmt="simple",
            )
        )
        print(f"\nTotal: {len(vaults)} vaults")

        # Interactive confirmation
        if not interactive:
            proceed = True
        else:
            try:
                answer = input("\nProceed with sync? [y/N] ").strip().lower()
                proceed = answer == "y"
            except (EOFError, KeyboardInterrupt):
                proceed = False

        if not proceed:
            print("Aborted.")
            sys.exit(0)

        # Convert to addresses + labels for the sync
        addresses = [v["address"] for v in vaults]
        labels = [v["name"] for v in vaults if v["name"]]

    # Always include HLP system vault addresses when syncing vaults
    if scan_mode != "top_traders":
        existing_set = set(addresses)
        hlp_added = []
        for addr in sorted(HYPERLIQUID_SYSTEM_VAULT_ADDRESSES):
            if addr not in existing_set:
                addresses.append(addr)
                hlp_added.append(addr)
        if hlp_added:
            print(f"Auto-added {len(hlp_added)} HLP system vault addresses")  # noqa: T201

    # Load proxies if WEBSHARE_API_KEY is set
    rotator = load_proxy_rotator()
    if rotator:
        print_proxy_dashboard(rotator)
    else:
        print("Proxies: disabled (set WEBSHARE_API_KEY to enable)")

    session = create_hyperliquid_session(
        requests_per_second=2.75,
        rotator=rotator,
    )
    db = HyperliquidTradeHistoryDatabase(db_path)

    try:
        # Add specified addresses to whitelist
        is_vault = scan_mode != "top_traders"
        if addresses:
            for i, addr in enumerate(addresses):
                label = labels[i] if i < len(labels) else None
                db.add_account(addr, label=label, is_vault=is_vault)
            print(f"Added {len(addresses)} addresses to whitelist")  # noqa: T201

        accounts = db.get_accounts()
        if not accounts:
            print("No whitelisted accounts. Set ADDRESSES, SCAN=top_traders, or MIN_VAULT_PEAK_TVL to add accounts.")  # noqa: T201
            return

        print(f"\nSyncing {len(accounts)} whitelisted accounts...")

        interrupted = False
        results = {}
        try:
            results = db.sync_all(session, max_workers=max_workers)
            db.save()
        except KeyboardInterrupt:
            interrupted = True
            print("\n\nInterrupted — saving checkpoint...")
            db.save()

        def _human(n):
            if n >= 1_000_000:
                return f"{n / 1_000_000:.1f}M"
            if n >= 1_000:
                return f"{n / 1_000:.0f}k"
            return str(n)

        # Per-account summary table (only when we have results from a full run)
        if results:
            rows = []
            for account in accounts:
                addr = account["address"]
                r = results.get(addr, {})
                state = db.get_sync_state(addr)
                rows.append(
                    [
                        account.get("label") or addr[:16],
                        addr[:16] + "...",
                        r.get("fills", 0),
                        r.get("funding", 0),
                        r.get("ledger", 0),
                        _human(state.get("fills", {}).get("row_count", 0)),
                        _human(state.get("funding", {}).get("row_count", 0)),
                        _human(state.get("ledger", {}).get("row_count", 0)),
                        "ERR" if r.get("error") else "OK",
                    ]
                )

            print(
                "\n"
                + tabulate(
                    rows,
                    headers=["Label", "Address", "New fills", "New funding", "New ledger", "Total fills", "Total funding", "Total ledger", "Status"],
                    tablefmt="simple",
                )
            )

        # Always print grand totals (even on Ctrl+C)
        totals = db.get_total_row_counts()
        print(f"\nDatabase totals: {_human(totals['fills'])} fills, {_human(totals['funding'])} funding, {_human(totals['ledger'])} ledger")
        print(f"Database path: {db_path}")

        if interrupted:
            sys.exit(130)

    finally:
        db.close()


if __name__ == "__main__":
    main()
