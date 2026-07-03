#!/usr/bin/env python3
"""Repair Upshift vault metadata and prices from the Upshift API.

This script is a targeted production repair tool for Upshift vaults.

It avoids ``RESET_LEADS`` and any whole-chain rediscovery. Instead it uses the
Upshift tokenized vault API and a baked API snapshot to:

1. Upsert lead entries only for known Upshift vault addresses.
2. Upsert missing or broken vault metadata rows only for those addresses.
3. Populate historical price data only for those addresses, scanning each
   supported chain at most once per run.

The historical price scan is non-destructive for unrelated vaults. Caught-up
vaults are skipped. For the remaining target vaults, the chain scan starts from
the earliest block any selected Upshift vault needs, while parquet deletion
remains scoped to those selected Upshift addresses. Each supported chain is
scanned at most once per run.

API documentation:

- https://docs.upshift.finance/developer-docs/api-reference
- https://docs.upshift.finance/developer-docs/api-reference/vaults

Usage:

.. code-block:: shell

    source .local-test.env && poetry run python scripts/erc-4626/fix-upshift-vaults.py

Useful environment variables:

.. list-table::
   :header-rows: 1

   * - Variable
     - Description
   * - ``DRY_RUN``
     - If ``true``, only print planned work. Default: ``false``.
   * - ``UPSHIFT_FETCH_API``
     - Fetch the live Upshift API and prefer it over the baked snapshot. Default: ``true``.
   * - ``UPSHIFT_STATUS``
     - Comma-separated statuses to include, or ``all``. Default: ``all``.
   * - ``UPSHIFT_VISIBLE_ONLY``
     - If ``true``, process only visible API vaults. Default: ``false``.
   * - ``UPSHIFT_SCAN_PRICES``
     - If ``false``, update only leads and metadata. Default: ``true``.
   * - ``UPSHIFT_REWRITE_TARGETED``
     - If ``true``, rescan each target vault from its first known block and rewrite only
       that vault's rows. Default: ``false``.
   * - ``MAX_WORKERS``
     - Historical multicall worker count. Default: ``8``.
   * - ``FREQUENCY``
     - Historical price frequency, ``1h`` or ``1d``. Default: ``1h``.
   * - ``START_BLOCK``
     - Optional global minimum start block override.
   * - ``END_BLOCK``
     - Optional global end block override.
   * - ``VAULT_DB_PATH``
     - Optional metadata DB path. Default: production vault metadata DB.
   * - ``UNCLEANED_PRICE_DATABASE``
     - Optional uncleaned price parquet path. Default: production uncleaned price DB.
   * - ``READER_STATE_DATABASE``
     - Optional reader-state pickle path. Default: production reader state DB.

JSON-RPC URLs are read per chain using the normal ``JSON_RPC_<CHAIN_NAME>``
convention where available. Upshift API chains not yet present in
``eth_defi.chain.CHAIN_NAMES`` are skipped until the project supports them.
"""

import csv
import datetime
import json
import logging
import os
import pickle  # noqa: S403 - trusted local production reader-state pickle.
import sys
from collections import defaultdict
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from atomicwrites import atomic_write
from eth_typing import HexAddress
from web3 import Web3
from web3.exceptions import BadFunctionCallOutput, ContractLogicError, Web3Exception

from eth_defi.chain import CHAIN_NAMES, EVM_BLOCK_TIMES, get_chain_name
from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.classification import create_vault_instance, detect_vault_features
from eth_defi.erc_4626.core import ERC4262VaultDetection
from eth_defi.erc_4626.discovery_base import PotentialVaultMatch
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.hypersync.utils import configure_hypersync_from_env
from eth_defi.provider.env import get_json_rpc_env
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory, create_multi_provider_web3
from eth_defi.provider.named import get_provider_name
from eth_defi.token import TokenDiskCache
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultBase, VaultSpec
from eth_defi.vault.historical import ParquetScanResult, pformat_scan_result, scan_historical_prices_to_parquet
from eth_defi.vault.vaultdb import DEFAULT_READER_STATE_DATABASE, DEFAULT_UNCLEANED_PRICE_DATABASE, DEFAULT_VAULT_DATABASE, VaultDatabase

logger = logging.getLogger(__name__)

UPSHIFT_API_URL = "https://api.upshift.finance/v1/tokenized_vaults"
USER_AGENT = "web3-ethereum-defi-upshift-maintenance/1.0"

#: Baked snapshot from ``GET https://api.upshift.finance/v1/tokenized_vaults``.
#:
#: This is a fallback if the API is unavailable and a review aid for operators:
#: the script contains the full EVM vault list known at implementation time.
UPSHIFT_VAULT_SNAPSHOT_CSV = """chain_id,address,name,status,internal_type,is_visible,first_block,api_id
1,0x0243755a22E37b835486fdAE9A839523ADABd336,K3 EUROP Vault,closed,multiAssetVault,false,23985328,de0ccf7e-a5e7-4ae1-b7a2-953df019de66
1,0x02991EE6134dEE504668e2227C5879Dd78EfCDc3,Injective USDT,active,tokenizedVault,false,22349455,fd636fc4-fe82-40b7-8c83-16e442ebf957
1,0x076f3f14d87eA2D34DB66A4b5b9f091918008552,Upshift Clear RWA,active,multiAssetVault,true,24844119,9ae0f5c3-ceb3-4e00-a476-334ccc2c7878
1,0x0985C88929A776a2E059615137a48bA5A473E25D,Alpine Coinshift USDC,closed,tokenizedVault,false,22985302,8af7191e-5057-4298-8f23-d92391a79fc8
1,0x0C949AAf28bF0318bAa5f2cbF2F2D970f57879aB,HCL Stroom V2 Vault,closed,multiAssetVault,false,23842329,b8d8205b-67d7-4306-9a7d-8fd36441f516
1,0x0cB0F46007EF680B99595107d790ac8459cB2c21,AtestwBTC,active,multiAssetVault,false,25452151,6efbe534-a183-454b-8ca4-e94f04dc6917
1,0x0F54097295E97cE61736bb9a0a1066cDf3e31C8F,Theoriq Gold,active,multiAssetVault,false,24678570,9d4f418c-dfe8-4376-a216-2bb7cd26b736
1,0x18a5a3D575F34e5eBa92ac99B0976dBe26f9F869,Lombard LBTC,active,lendingPool,false,20917130,07bd6aac-4e8f-4a71-8b51-c0455194ce58
1,0x18EE038C114a07f4B08b420fb1E4149a4F357249,Upshift Wildcat USD,active,multiAssetVault,true,23522162,480c52a8-323c-41a6-b226-f11d63e4a577
1,0x21C6d11FeB07818E3d4D2E9ae8c211Bd85970F40,Upshift Liquid Collective ETH,active,multiAssetVault,false,25185637,302ffa6f-02f1-4591-b7fb-73f53b215062
1,0x2Cb3b86C32e723BE255E5ec4E443a0CA90876634,USCC Repo Vault,active,multiAssetVault,false,24000866,d3177be1-2a7a-429a-993e-94156d537e10
1,0x2fCa8109be17dd099a7df9b9064260D87195512b,Test M Vault,active,multiAssetVault,false,0,730d1d32-0622-4811-98c9-7ee01538da2d
1,0x3299A525986D2e94B3FC6c641C158f5e12dB912d,Fluent USDnr Pre-deposit,closed,multiAssetVault,false,24693495,a97f420c-cc63-471c-87c3-c6ee0dfc11e0
1,0x35C793892223B2A7C2BE8C7A240dD2083c14D9bD,Test WETH Tokenized Account ,closed,tokenizedVault,false,0,386f49be-d1a9-46aa-8c26-60292e0d5f34
1,0x396A3f77EE1faf5A3C46e878bA7b7a2dcbe55517,Multipli TAC USDC,closed,tokenizedVault,false,22270642,f6f8f8f6-3787-48eb-ae7a-1684ae261f88
1,0x3cC0D33B1AEac3d23eA89214b3AC5B4607032167,Sentora BTC,active,multiAssetVault,true,23686487,3050ac5b-518f-417c-ab9e-f54ae5a0537e
1,0x3E4EF6Ccc7E4A045C9d3F48B08813D59Df14e256,Upshift Gamma BTC Vault,active,multiAssetVault,true,24479854,4aaed308-9364-4d73-8f8f-9a39a8f8010c
1,0x419386E3Ef42368e602720CC458e00c0B28c47A7,Kelp TAC Vault,closed,tokenizedVault,true,21840991,9275a871-8df2-4222-9512-be58e5f18892
1,0x49494d2BdEE729fB23334F3C58b73b5ebEdE7556,Daylight Pre-deposit Vault,closed,multiAssetVault,false,23993752,9a068ce5-53bd-4cde-af1f-1ee9ec2223fb
1,0x5Fde59415625401278c4d41C6beFCe3790eb357f,The Treehouse Growth Vault,closed,lendingPool,true,21289632,8538ce9d-14b4-48b2-a385-7c7f36579453
1,0x63C5d615e937697a80606788965E209414738820,Aave Horizon USCC Vault,active,multiAssetVault,false,23686487,00ae9c4b-ded6-4369-a35c-6252353f7a5d
1,0x6625bA54DC861e9f5c678983dBa5BA96d19a9224,Alpine BTC Flagship,closed,tokenizedVault,false,23185735,edcac402-d98b-4cd0-9eae-b95b4fd2e269
1,0x686c83Aa81ba206354fDcbc2cd282B4531365E29,Upshift TAC USR,closed,tokenizedVault,false,22220455,97488771-8603-44a4-838b-06568a7d44c1
1,0x69FC3f84FD837217377d9Dae0212068cEB65818e,Azure Tide USDC,active,tokenizedVault,false,22734781,3e01aae9-231f-41e8-a6ed-d18626a88331
1,0x7383c2454D23A1e34B35ba02674be0a41Bd5aa56,Upshift cUSDO,active,tokenizedVault,false,22048553,b2dc36e0-0e5f-4a84-9b6d-79ba74d10d30
1,0x73aF97ad5CE9D02d5E73660575e18EFcdC69C01a,Test_sUSDnr,active,multiAssetVault,false,0,04404b6f-ad92-4d5a-bf9f-fac4fe5faa8f
1,0x73B04B604DF15A6d920Ee62E228548cD88DD3549,SingularV USDC Max Yield,active,multiAssetVault,true,23800870,bf66d234-40b1-4ba4-8dc3-342224730742
1,0x74aD2F789Ed583DBd141bbdafC673fE1F033718b,Sentora USD,active,multiAssetVault,true,24079650,c3ac6b77-60c3-4bcd-a3f0-a1dd7381d1f2
1,0x787f5541Ee40cD0c94175251686DDBbcf69A7344,Hardcore Labs Stroom BTC vault,closed,tokenizedVault,false,23229630,95879828-1976-4f91-9c45-948acf08ac20
1,0x80E1048eDE66ec4c364b4F22C8768fc657FF6A42,Upshift USDC,active,lendingPool,true,21096238,f6c5e47f-c9c1-4f58-a85f-61ac2f043ebe
1,0x828BC5895b78b2fb591018Ca5bDC2064742D6D0f,Agora earnAUSD,active,tokenizedVault,false,22949536,0566b224-f0dc-4a5f-aa5c-4f4a4484f267
1,0x866C6c6627303Be103814150fC0e886BE5D9ea83,K3 Neutrl Pre-Deposit Vault,closed,tokenizedVault,false,23530215,2b76f03e-b863-4e59-b68e-01c2e57f5d8a
1,0x8AcA0841993ef4C87244d519166e767f49362C21,August Mezo tBTC Vault,active,tokenizedVault,true,22370973,1cf82abc-c477-4616-81ea-e9768556f2c5
1,0x955256B31097dDf47a9E47A95aDfDFB4460D8522,NEMO USDC Yield,active,multiAssetVault,true,25396895,58c28ee6-aff6-49e0-9291-625f9f82e90f
1,0x998D7b14c123c1982404562b68edDB057b0477cB,Upshift Gamma USDC Vault,active,tokenizedVault,true,23121240,a0e0990d-f822-4cab-84fe-7bc09f9da101
1,0xA38d92eC538e9aa3edb980b89701A9d38a1FE015,Aptos Aave,closed,multiAssetVault,false,23629361,1e95eacb-8797-4f48-b5b1-73b64a6cefd3
1,0xA422C3018C46ba90a14AcD14f96CB60616F5c91B,NEMO ETH Yield,active,multiAssetVault,true,24629020,a4bda3d3-ae27-4e07-baaa-4bbc730f11cf
1,0xaBC578B79892FC0Dc2906Ab5749862622aD38695,Decibel Pre-Deposit Vault,closed,multiAssetVault,false,24122634,cfb54030-2caa-4e04-8443-41895f651b2c
1,0xAEEb2fB279a5aA837367B9D2582F898a63b06ca1,K3 Neutrl NUSD Vault,active,multiAssetVault,true,23842329,55cd74fa-f922-42aa-8222-1dbb5af86e9c
1,0xb2FdA773822E5a04c8A70348d66257DD5Cf442DB,LiquityV2 Airdrop Farming Vault,closed,multiAssetVault,false,23700766,663e7055-7dfe-4338-a7a0-b6847a0649e8
1,0xB78dAf3fD674B81ebeaaa88d711506fa069E1C5E,Upshift ETH Optimizer,closed,tokenizedVault,true,21819559,8b7275b5-7980-49db-af6f-7c4d2fed8921
1,0xC11DF5e9d6F6B5e02f4878A42c9a6456A37688a7,Clearstar Prism Enhanced Yield Vault,active,multiAssetVault,true,24791495,4d711d5b-fb21-46d1-98ff-ece39af65de5
1,0xc428439fB7B1EFE56360Eb837Ca98F551fdD9B26,Sylva Concentrated Liquidity,active,tokenizedVault,false,22134526,3028e103-6772-4f83-92f2-a2627a1541a6
1,0xc824A08dB624942c5E5F330d56530cD1598859fD,High Growth ETH,active,lendingPool,true,21225225,01e79260-27b6-4565-af40-a7ec97b0113c
1,0xc87DBBB8C67e4F19fCD2E297c05937567b2572Ce,Earn ctUSD,active,multiAssetVault,true,25002356,5dd9319f-9f32-4bfe-a9cb-e12899f48ee0
1,0xcd69123b3FBBfC666E1f6a501da27B564C00De54,Tori Ecosystem Vault,active,multiAssetVault,true,25294084,782c526f-2f8e-4963-b97b-9511dfbedefe
1,0xd000E6BcAd5457E8F4de67eDdeFe50BCC4B3d743,Sentora PRIME Looping,active,multiAssetVault,true,25279734,3e7a9fb2-79a8-4348-bd32-5d1e587ba357
1,0xD0271E199f886Ff943859579465498B18eCF1E9d,Sentora ETH,active,multiAssetVault,true,24270146,a4f13d19-f160-4b12-ac98-4a863e0c5339
1,0xD066649Bcb7d8D3335fE29CaD0AED6E17D5828B5,Alpine USDC Flagship,closed,tokenizedVault,false,22985302,fc7cd47f-57b7-4416-a067-4d561252b1d1
1,0xd684AF965b1c17D628ee0d77cae94259c41260F4,Ethena Growth sUSDe,closed,lendingPool,true,21325415,f81e59b2-bf80-42bc-9d91-00d92e0d5e43
1,0xD809197828f63Bf95f90931191Bd9e3E7C6366C6,Treehouse Growth Vault v2,active,multiAssetVault,true,24372471,951afc72-ef5a-49c4-8900-b36582aa276b
1,0xdA89af5bF2eb0B225d787aBfA9095610f2E79e7D,Resolv USR Yield Maxi,closed,tokenizedVault,false,23078306,697e5291-25c4-4fda-ac6a-953d88334fd5
1,0xe1B4d34E8754600962Cd944B535180Bd758E6c2e,Kelp Gain,active,lendingPool,true,20487316,82332223-c5da-469c-b38f-f1fb84691cb0
1,0xE9B725010A9E419412ed67d0fA5f3A5f40159D32,Upshift Core USDC,active,tokenizedVault,true,22520401,1f1cd45e-f521-4d64-918a-4e60fd152110
1,0xEAA3b922E9fEbCa37d1c02D2142A59595094C605,Upshift Edge USDC,closed,tokenizedVault,false,22842131,899be9df-89c1-4956-8513-f9b9c73bb59e
1,0xeb402fc96C7ed2f889d837C9976D6d821c1B5f01,Kelp TAC Vault ,closed,tokenizedVault,true,21933948,18db0446-4b2a-44f9-a372-6d4a6816e192
1,0xEBac5e50003d4B17Be422ff9775043cD61002f7f,Upshift BTC,closed,lendingPool,false,20874085,de365c37-d659-4fa5-afe6-4f3c951af8a0
1,0xf7b65fBA4d02110089fC6e2bE6d73809b45852f8,TAC tETH,closed,tokenizedVault,false,22349455,60f01899-e0fa-4462-911e-1894e8535950
14,0x2439D4bb753A0f3777d4C9011AFacc475ba6B951,Monarq XRP Yield Vault,active,multiAssetVault,true,60354722,931be03c-88e3-4f23-b73c-eb33cc049403
14,0x373D7d201C8134D4a2f7b5c63560da217e3dEA28,Flare XRP Yield Vault,active,multiAssetVault,true,52022553,2e79a5f5-94b0-4042-b8d0-8d927f5eb11d
56,0xD0b717ef23817b1a127139830Cf0FcD449ef74F0,Sienna USDT Tulipa,closed,tokenizedVault,false,48076462,b392e892-ab17-4ff5-a459-83757324126f
143,0x255E14eeaE50A77D7044B7eAa8CC5687db5D37BF,earnUSDC - Testing Vault12,closed,multiAssetVault,false,53274328,c337e8ce-da97-4ccf-ab70-e00741a5d3ad
143,0x3297310Af637b75771F7ae87687731D47BC75280,op_Test,closed,multiAssetVault,false,64198819,c3cea902-f5e4-4d39-8194-8419b9bc6ccd
143,0x36eDbF0C834591BFdfCaC0Ef9605528c75c406aA,earnAUSD,active,multiAssetVault,true,35432000,672db4d8-72cd-46cb-bde3-746d1dd973a8
143,0x3e06892B43c8f2D5785939e91A3FF7990021DaAf,Test_IX_AUSD_Monad,active,multiAssetVault,false,0,2e593e4f-4fe3-43fa-9aed-07fc1eced0a2
143,0x448176Db568BBFfACD5B02b2AcA6F17A3eB0a1a8,Upshift USDC Test ,active,multiAssetVault,false,62272948,ea754891-4381-41a6-9555-0ffb86c1685f
143,0x4f93f53af11937F5141B845a8c590E5f138b3a59,Bugbash test 1,closed,multiAssetVault,false,57586527,32063d41-d604-44d7-b301-5d663526f868
143,0x5d9b88b22579713A7C7EEC9A3f577f0d7fFd7986,earnBTC,closed,multiAssetVault,false,57800429,6670da6e-ec25-4b65-adb5-4a027a9fe1db
143,0x5E7568bf8DF8792aE467eCf5638d7c4D18A1881C,earnMON Vault,active,multiAssetVault,false,41212142,8c81667d-940f-4909-9ee1-9d91fb11121c
143,0x64996271ee085ef9e6E939Ab3eACd93F7d7080db,Monad wMON/AUSD,active,tokenizedVault,false,36714572,f626d970-b20f-42cc-be84-9514b5a77376
143,0x6A337cA225CBA291e5C82A2FcC2c2947d655B490,test_vault(IX)_Monad,closed,multiAssetVault,false,0,893ff305-a44f-49f7-a5c5-f10ae49a93c8
143,0x70E986090b0E3b1F5b675a47529f8600ac94D60a,IX_test,active,multiAssetVault,false,63731044,a5ed2059-9f2f-41e7-af17-4ca4b82f31ad
143,0x792C7c5fB5C996E588b9F4A5FB201C79974e267C,Kintsu superMON Vault,active,multiAssetVault,false,39318216,47c32974-6348-4f75-96db-c264175961e0
143,0xaF836e361DD1cc0cfB4200165083671D80fa7Af4,TEST_USDC,active,multiAssetVault,false,0,f9c01e77-2fed-48e4-ad89-d5bb6becc827
143,0xB667D005695D7f530A5621549aE31d9409486E29,Monad wBTC/AUSD,active,tokenizedVault,false,36714572,b7e31039-54d2-4277-8b2b-b6dfdc5f9010
143,0xCf1556A9bff5cC7A18d8e6a2Ae06DBf36Ed1751C,earn_test_AUSD11q,closed,multiAssetVault,false,59094503,15d2f786-7c24-457b-a20a-b2970106fc01
143,0xD793c04B87386A6bb84ee61D98e0065FdE7fdA5E,Savings AUSD,active,tokenizedVault,false,36909371,5627f13c-60e3-4c0f-b56c-606a59b61688
143,0xe1E3388FE52bC0992496e54e71e2ae9Ff1fA8e45,usdc_test,closed,multiAssetVault,false,53484993,6d385f51-0572-4bb7-8102-0f8533de7517
143,0xeB4f7671D1D63DA1B4A13c11221c9a066E41287d,vkTest,active,multiAssetVault,false,64883047,65530e29-1b7b-47f7-997b-72005aa6122d
999,0x4164B34496a7513b6bfE95D3120f2b9b19e08012,Test Vault,closed,tokenizedVault,false,12211372,7d117931-a57e-4b8f-88ae-ad2255ca5f79
999,0x8fFDcd8A96d293f45aA044d10b899F9D71897E8a,HLPe,active,multiAssetVault,true,17118803,073f469f-8ce7-486c-b4c2-3df91fca8870
999,0x96C6cBB6251Ee1c257b2162ca0f39AA5Fa44B1FB,Hyperbeat Ultra HYPE,active,tokenizedVault,true,2313970,e08a53ac-9a07-48a7-bc9c-3e1ebda406d6
999,0xA8719f8467c8700E5D5a4377fEDEd0AF5C9058dB,Curation Admin Test,active,multiAssetVault,false,28975964,01dbf3ec-2076-4a73-823e-a5146dc9abda
999,0xB075dd7D4cC561928665522e713027fe5Cf7F7cb,XYZ LP Vault,active,multiAssetVault,false,25109597,508e3fa4-3ed8-409a-aff9-f61ae1f42eef
999,0xc061d38903b99aC12713B550C2CB44B221674F94,Hyperbeat Ultra uBTC,active,tokenizedVault,true,3117306,c72d1873-1334-45a6-bc48-b37f2bc2f009
999,0xcfe06d2499aE635830D11859941e76354D5717CC,Coinmerce Capital USDC,active,multiAssetVault,true,31852460,878046f1-2838-48bc-b9b0-87c825d13527
4114,0x75Ce3a2A622dFf89b4bC68daFA37A57f2b1890fb,Upshift cBTC ,active,multiAssetVault,false,4197844,e697e63d-2909-4735-9f06-1544425dea8e
8453,0x04fE168F719611dc4BFDD2efeCe57659eE31fF12,vkTest_cbBTC,active,multiAssetVault,false,44088207,bddc883d-85ff-4d49-944c-d3ffed123bc4
8453,0x4e2D90f0307A93b54ACA31dc606F93FE6b9132d2,Lombard LBTC,active,lendingPool,false,22765326,b0a33f45-6c89-49c4-bf33-f1f2a07a78f5
8453,0x8EC5C8642397791cff83563C9C83b9C1409D5F48,Test WETH Tokenized Account ,closed,tokenizedVault,,0,db5c5190-83c2-405b-bcba-01c9dfda8e7c
8453,0xb4ce77E04f3EB3A97901f0eF85796D3bA787C5ae,USDC TEST 3,active,multiAssetVault,false,47232774,67f9533d-1518-4826-aff7-9f4a227cf072
8453,0xBcf12e8266d5181483651AeCE9a95267962e9103,stk_test,active,multiAssetVault,false,44392392,031f9100-86e5-4144-a02f-bd953f89ced2
8453,0xd6cF6D1d01403304c144EC3D69f07B565c00270e,vkTest_USDC,active,multiAssetVault,false,44088207,2ccbfeee-1521-4b32-b707-6ee0cc306f90
8453,0xd9c51Acb69310a1eC563a59A685F3639AB5E3ca6,USDC TEST,active,multiAssetVault,false,47232774,e1dcff34-8010-40e2-bb79-4b229e6284fe
9745,0x517677A19D8ae6FF600FB86C3C7bFCCD651e3eec,Yuzu Money USDT0 Vault,active,multiAssetVault,true,6736371,17a7bcdd-ab3c-4293-aefb-f1a9808d2d84
25363,0x50AE83DBDC44208eDa1Ef722F87Bab0FFB195Eea,Staked Nerona Dollar,active,multiAssetVault,true,3111288,0111d90c-03c7-4d37-b39d-16a233ef7ece
25363,0xeaB765200189909c806FD6e20eBb4E57D6703C82,Nerona Dollar Yield Strategy,active,multiAssetVault,true,3111288,95e3a93d-472f-48fb-b1bd-c05486f579f7
31612,0x221B2D9aD7B994861Af3f4c8A80c86C4aa86Bf53,Mezo MUSD Vault ,closed,tokenizedVault,false,2730640,25cd0b23-f7c6-45fe-9633-388de717e239
43114,0x3408b22d8895753C9A3e14e4222E981d4E9A599E,Upshift Avalanche AUSD,closed,lendingPool,false,52666319,caf63030-9b79-4d1d-888b-c38e23a21d5a
43114,0x635BCC289E71f38E96E60F25B53B21c4BBa218C6,ep_test,active,multiAssetVault,false,82345098,c2bf727c-376c-428b-a12e-17ba09b847a3
43114,0xB2bFb52cfc40584AC4e9e2B36a5B8d6554A56e0b,Upshift Avalanche AVAX,closed,lendingPool,false,54231271,f7d65bb2-fd2d-4eeb-aa6a-db779f54df13
43114,0xFBD3988eC3799790B12599D8051Df2B64314C8f6,Nonco FX Onchain,closed,multiAssetVault,false,79167786,2906adae-af6f-4f32-8698-d2bee4b6d02d
57073,0x3260d0231bf51452519f67D9333Fc01Ed6839ee9,Tydro Ink USDT0 Vault,active,multiAssetVault,false,30714004,bf07ae44-fd27-4e2b-b14f-00794f546f80
57073,0x71BE0E419FbB98762C7b6D9e81EaFdA99363333c,Tydro Ink KBTC Vault,closed,multiAssetVault,false,30714004,568bd8f5-c4f5-4f80-a4af-f274a681eaaa
"""


@dataclass(slots=True, frozen=True)
class UpshiftVaultReference:
    """Known Upshift vault from the API or baked snapshot.

    :param chain_id:
        EVM chain id.

    :param address:
        Checksum vault contract address.

    :param name:
        Human-readable Upshift vault name.

    :param status:
        Upshift API status, e.g. ``active`` or ``closed``.

    :param internal_type:
        Upshift implementation family, e.g. ``multiAssetVault`` or
        ``tokenizedVault``.

    :param is_visible:
        Whether the Upshift frontend currently shows the vault.

    :param first_seen_at_block:
        Earliest API snapshot block. ``1`` when the API did not expose any
        historical snapshot yet.

    :param api_id:
        Upshift API UUID.
    """

    chain_id: int
    address: HexAddress
    name: str
    status: str
    internal_type: str
    is_visible: bool | None
    first_seen_at_block: int
    api_id: str

    def get_spec(self) -> VaultSpec:
        """Return the canonical vault spec."""
        return VaultSpec(self.chain_id, self.address.lower())


@dataclass(slots=True)
class ChainRepairResult:
    """Repair counters for one chain."""

    chain_id: int
    lead_upserts: int = 0
    metadata_upserts: int = 0
    metadata_preserved: int = 0
    metadata_failures: int = 0
    price_scans: int = 0
    price_failures: int = 0
    skipped_unsupported: int = 0


def parse_bool_env(name: str, *, default: bool = False) -> bool:
    """Parse a boolean environment variable."""
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_status_filter() -> set[str] | None:
    """Parse ``UPSHIFT_STATUS``.

    :return:
        ``None`` means include all statuses.
    """
    value = os.environ.get("UPSHIFT_STATUS", "all").strip().lower()
    if value in {"", "all", "*"}:
        return None
    return {item.strip() for item in value.split(",") if item.strip()}


def parse_optional_int_env(name: str) -> int | None:
    """Parse an optional integer environment variable."""
    value = os.environ.get(name)
    if not value:
        return None
    return int(value)


def parse_snapshot_csv(text: str) -> list[UpshiftVaultReference]:
    """Parse baked Upshift vault CSV snapshot.

    :param text:
        CSV text from :data:`UPSHIFT_VAULT_SNAPSHOT_CSV`.

    :return:
        Parsed vault references.
    """
    reader = csv.DictReader(StringIO(text.strip()))
    refs = []
    for row in reader:
        visible_raw = row["is_visible"].strip().lower()
        if visible_raw == "":
            is_visible = None
        else:
            is_visible = visible_raw == "true"

        refs.append(
            UpshiftVaultReference(
                chain_id=int(row["chain_id"]),
                address=HexAddress(Web3.to_checksum_address(row["address"])),
                name=row["name"],
                status=row["status"],
                internal_type=row["internal_type"],
                is_visible=is_visible,
                first_seen_at_block=max(1, int(row["first_block"] or "0")),
                api_id=row["api_id"],
            )
        )
    return refs


def fetch_upshift_vaults(timeout: float = 30.0) -> list[UpshiftVaultReference]:
    """Fetch the full EVM Upshift vault list from the official API.

    The endpoint returns EVM, Solana, Sui and Stellar records. This script only
    repairs the EVM ERC-4626 pipeline, so non-positive chain ids and non-EVM
    addresses are ignored.

    :param timeout:
        HTTP timeout in seconds.

    :return:
        All API records that look like EVM vault addresses.
    """
    request = Request(  # noqa: S310 - constant HTTPS Upshift API endpoint.
        UPSHIFT_API_URL,
        headers={
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - constant HTTPS Upshift API endpoint.
        payload = json.load(response)

    if not isinstance(payload, list):
        raise ValueError(f"Unexpected Upshift API response type: {type(payload)}")

    refs = []
    for item in payload:
        if not isinstance(item, dict):
            continue

        chain_id = item.get("chain")
        address = item.get("address")
        if not isinstance(chain_id, int) or chain_id <= 0:
            continue
        if not isinstance(address, str) or not Web3.is_address(address):
            continue

        snapshots = item.get("historical_snapshots") or []
        first_seen_at_block = 1
        snapshot_blocks = [snapshot.get("block_id") for snapshot in snapshots if isinstance(snapshot, dict)]
        snapshot_blocks = [block for block in snapshot_blocks if isinstance(block, int) and block > 0]
        if snapshot_blocks:
            first_seen_at_block = min(snapshot_blocks)

        refs.append(
            UpshiftVaultReference(
                chain_id=chain_id,
                address=HexAddress(Web3.to_checksum_address(address)),
                name=item.get("vault_name") or "",
                status=item.get("status") or "",
                internal_type=item.get("internal_type") or "",
                is_visible=item.get("is_visible"),
                first_seen_at_block=first_seen_at_block,
                api_id=item.get("id") or "",
            )
        )

    refs.sort(key=lambda ref: (ref.chain_id, ref.address.lower()))
    return refs


def load_upshift_vault_references() -> list[UpshiftVaultReference]:
    """Load live Upshift API vaults, falling back to the baked full snapshot."""
    snapshot_refs = parse_snapshot_csv(UPSHIFT_VAULT_SNAPSHOT_CSV)

    if not parse_bool_env("UPSHIFT_FETCH_API", default=True):
        logger.info("Using baked Upshift API snapshot with %d EVM vaults", len(snapshot_refs))
        return snapshot_refs

    try:
        live_refs = fetch_upshift_vaults()
    except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError, OSError) as e:
        logger.warning("Could not fetch Upshift API, using baked snapshot: %s", e)
        return snapshot_refs

    snapshot_specs = {ref.get_spec() for ref in snapshot_refs}
    live_specs = {ref.get_spec() for ref in live_refs}
    new_specs = live_specs - snapshot_specs
    removed_specs = snapshot_specs - live_specs
    if new_specs:
        logger.warning("Upshift API has %d EVM vaults not in the baked snapshot: %s", len(new_specs), sorted(map(str, new_specs))[:20])
    if removed_specs:
        logger.warning("Baked Upshift snapshot has %d EVM vaults no longer returned by the API", len(removed_specs))

    logger.info("Fetched %d EVM vaults from Upshift API", len(live_refs))
    return live_refs


def filter_references(refs: list[UpshiftVaultReference]) -> list[UpshiftVaultReference]:
    """Apply operator status and visibility filters."""
    statuses = parse_status_filter()
    visible_only = parse_bool_env("UPSHIFT_VISIBLE_ONLY", default=False)

    filtered = []
    for ref in refs:
        if statuses is not None and ref.status.lower() not in statuses:
            continue
        if visible_only and ref.is_visible is not True:
            continue
        filtered.append(ref)

    return filtered


def get_rpc_env_candidates(chain_id: int) -> list[str]:
    """Get possible JSON-RPC environment variable names for an Upshift chain."""
    names = []
    if chain_id in CHAIN_NAMES:
        names.append(get_json_rpc_env(chain_id))
    names.append(f"JSON_RPC_CHAIN_{chain_id}")
    names.append(f"JSON_RPC_{chain_id}")
    return list(dict.fromkeys(names))


def is_supported_chain(chain_id: int) -> bool:
    """Check whether the production vault scanner supports a chain.

    Upshift's API includes EVM chains that are not yet configured in
    :mod:`eth_defi.chain`. Skip those chains instead of inventing partial
    scanner configuration in this repair script.

    :param chain_id:
        EVM chain id from the Upshift API.

    :return:
        ``True`` if the project knows the chain.
    """
    return chain_id in CHAIN_NAMES


def read_rpc_url_for_chain(chain_id: int) -> tuple[str | None, str | None]:
    """Read the JSON-RPC URL for a chain.

    :return:
        Tuple ``(url, env_var_name)``. Both are ``None`` if no env var is set.
    """
    for env_name in get_rpc_env_candidates(chain_id):
        value = os.environ.get(env_name)
        if value:
            return value, env_name
    return None, None


def get_step_for_frequency(chain_id: int, frequency: str) -> int:
    """Convert frequency to block step for a chain."""
    seconds = {
        "1h": 60 * 60,
        "1d": 24 * 60 * 60,
    }[frequency]

    override = os.environ.get(f"BLOCK_TIME_SECONDS_{chain_id}")
    if override:
        block_time = float(override)
    else:
        block_time = float(EVM_BLOCK_TIMES.get(chain_id, 1.0))

    return max(1, int(seconds / block_time))


def load_vault_database(path: Path) -> VaultDatabase:
    """Load or initialise the vault metadata database."""
    if path.exists():
        return VaultDatabase.read(path)
    logger.warning("Vault metadata DB %s does not exist, creating a new DB", path)
    return VaultDatabase()


def load_reader_states(path: Path) -> dict[VaultSpec, dict]:
    """Load existing historical reader states."""
    if not path.exists():
        return {}
    with path.open("rb") as inp:
        return pickle.load(inp)  # noqa: S301 - trusted local production reader-state pickle.


def write_reader_states(path: Path, states: dict[VaultSpec, dict]) -> None:
    """Write historical reader states atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_write(str(path), mode="wb", overwrite=True) as out:
        pickle.dump(states, out)


def fetch_latest_existing_price_blocks(price_path: Path, chain_id: int, addresses: set[str]) -> dict[str, int]:
    """Fetch latest existing price blocks for several vaults in one parquet read.

    :param price_path:
        Existing uncleaned price parquet path.

    :param chain_id:
        Chain id to inspect.

    :param addresses:
        Lowercase vault addresses.

    :return:
        Mapping ``vault address -> latest block number``.
    """
    if not price_path.exists() or not addresses:
        return {}

    table = pq.read_table(price_path, columns=["chain", "address", "block_number"])
    mask = pc.and_(
        pc.equal(table["chain"], chain_id),
        pc.is_in(table["address"], pa.array(sorted(addresses))),
    )
    filtered = table.filter(mask)
    if filtered.num_rows == 0:
        return {}

    latest_blocks: dict[str, int] = {}
    data = filtered.to_pydict()
    for address, block_number in zip(data["address"], data["block_number"], strict=True):
        latest_blocks[address] = max(latest_blocks.get(address, 0), block_number)
    return latest_blocks


def upsert_lead(vault_db: VaultDatabase, ref: UpshiftVaultReference, updated_at: datetime.datetime) -> bool:
    """Upsert one API-listed Upshift vault into the lead map."""
    spec = ref.get_spec()
    existing = vault_db.leads.get(spec)
    first_seen_at_block = max(1, ref.first_seen_at_block)
    lead = PotentialVaultMatch(
        chain=ref.chain_id,
        address=HexAddress(ref.address.lower()),
        first_seen_at_block=first_seen_at_block,
        first_seen_at=getattr(existing, "first_seen_at", updated_at) if existing else updated_at,
        # API-seeded leads did not come from a historical flow-event count.
        # Keep non-zero deposit count so legacy candidate filters treat the
        # operator-curated API list as intentional input.
        deposit_count=max(getattr(existing, "deposit_count", 0) if existing else 0, 1),
        withdrawal_count=getattr(existing, "withdrawal_count", 0) if existing else 0,
    )
    vault_db.leads[spec] = lead
    return existing is None


def create_detection(ref: UpshiftVaultReference, features: set, updated_at: datetime.datetime) -> ERC4262VaultDetection:
    """Create an ERC-4626 detection envelope for an API-listed Upshift vault."""
    return ERC4262VaultDetection(
        chain=ref.chain_id,
        address=HexAddress(ref.address.lower()),
        first_seen_at_block=max(1, ref.first_seen_at_block),
        first_seen_at=updated_at,
        features=features,
        updated_at=updated_at,
        # API-seeded metadata does not have historical event counts. Use the
        # minimum production threshold so targeted and future price scans do not
        # discard known API vaults before feature-based protocol routing runs.
        deposit_count=5,
        redeem_count=0,
    )


def is_broken_metadata_row(row: dict) -> bool:
    """Check whether a vault metadata row is an RPC failure placeholder."""
    name = row.get("Name") or ""
    return name.startswith("<broken") or (not name and not row.get("Denomination"))


def should_refresh_metadata(vault_db: VaultDatabase, ref: UpshiftVaultReference) -> bool:
    """Decide whether to refresh a metadata row."""
    if parse_bool_env("UPSHIFT_REFRESH_EXISTING_METADATA", default=False):
        return True

    row = vault_db.rows.get(ref.get_spec())
    if row is None:
        return True

    return is_broken_metadata_row(row)


def upsert_metadata_row(web3: Web3, vault_db: VaultDatabase, token_cache: TokenDiskCache, ref: UpshiftVaultReference, updated_at: datetime.datetime) -> bool:
    """Create or repair one vault metadata row from live chain data."""
    if not should_refresh_metadata(vault_db, ref):
        return False

    features = detect_vault_features(web3, ref.address, verbose=False)
    detection = create_detection(ref, features, updated_at)
    row = create_vault_scan_record(web3, detection, web3.eth.block_number, token_cache)
    vault_db.rows[ref.get_spec()] = row
    return True


def create_price_vault(web3: Web3, vault_db: VaultDatabase, token_cache: TokenDiskCache, ref: UpshiftVaultReference) -> VaultBase | None:
    """Create a vault reader instance from the metadata DB row."""
    row = vault_db.rows.get(ref.get_spec())
    if row is None:
        return None

    detection = row.get("_detection_data")
    if not isinstance(detection, ERC4262VaultDetection):
        return None

    vault = create_vault_instance(web3, ref.address, detection.features, token_cache=token_cache)
    if vault is not None:
        vault.first_seen_at_block = detection.first_seen_at_block
    return vault


def fetch_vault_price_start_block(ref: UpshiftVaultReference, latest_existing_blocks: dict[str, int], *, rewrite_targeted: bool) -> int:
    """Get the historical price repair start block for one vault.

    :param ref:
        Target Upshift vault.

    :param latest_existing_blocks:
        Mapping ``vault address -> latest block number`` from the existing
        price parquet.

    :param rewrite_targeted:
        If ``True``, start from the first known API block even when the vault
        already has price rows.

    :return:
        First block that needs scanning for this vault.
    """
    explicit_start_block = parse_optional_int_env("START_BLOCK")
    if explicit_start_block is not None:
        return explicit_start_block

    latest_existing_block = latest_existing_blocks.get(ref.address.lower())
    if rewrite_targeted or latest_existing_block is None:
        return max(1, ref.first_seen_at_block)

    return latest_existing_block + 1


def scan_chain_price_history(  # noqa: PLR0917 - explicit operational script arguments keep the call site auditable.
    web3: Web3,
    json_rpc_url: str,
    token_cache: TokenDiskCache,
    reader_states: dict[VaultSpec, dict],
    refs: list[UpshiftVaultReference],
    vaults: list[VaultBase],
    price_path: Path,
    end_block: int | None,
    frequency: str,
    max_workers: int,
    *,
    rewrite_targeted: bool,
) -> ParquetScanResult | None:
    """Scan one chain's Upshift historical prices once.

    The underlying parquet writer deletes and rewrites only rows whose address
    is in ``vault_addresses``. We therefore first drop caught-up vaults, then
    use the earliest required start block across the remaining target vaults
    and scan all of those vaults in one pass for the chain, instead of
    repeatedly walking the same chain once per vault.

    :param web3:
        Web3 connection for the chain.

    :param json_rpc_url:
        RPC URL used to create worker connections.

    :param token_cache:
        Shared token metadata cache.

    :param reader_states:
        Existing reader states. Target vault states are removed for this scan
        to force a targeted backfill from ``start_block``.

    :param refs:
        Upshift vault references that match ``vaults``.

    :param vaults:
        Supported vault reader instances.

    :param price_path:
        Raw historical price parquet path.

    :param end_block:
        End block for this chain scan.

    :param frequency:
        Historical price frequency, ``1h`` or ``1d``.

    :param max_workers:
        Historical multicall worker count.

    :param rewrite_targeted:
        If ``True``, rewrite target vault rows from their first known API
        block.

    :return:
        Parquet scan result, or ``None`` if all target vaults are already
        caught up or unsupported.
    """
    if not vaults:
        return None

    latest_existing_blocks = fetch_latest_existing_price_blocks(price_path, web3.eth.chain_id, {ref.address.lower() for ref in refs})
    selected_refs: list[UpshiftVaultReference] = []
    selected_vaults: list[VaultBase] = []
    selected_start_blocks: list[int] = []
    caught_up_count = 0

    for ref, vault in zip(refs, vaults, strict=True):
        vault_start_block = fetch_vault_price_start_block(ref, latest_existing_blocks, rewrite_targeted=rewrite_targeted)
        if end_block is not None and vault_start_block >= end_block:
            caught_up_count += 1
            continue
        selected_refs.append(ref)
        selected_vaults.append(vault)
        selected_start_blocks.append(vault_start_block)

    if not selected_vaults:
        logger.info("Skipping chain %s price scan: %d Upshift vaults already caught up at block %s", web3.eth.chain_id, len(vaults), end_block)
        return None

    if caught_up_count:
        logger.info("Skipping %d caught-up Upshift vaults on chain %s", caught_up_count, web3.eth.chain_id)

    start_block = min(selected_start_blocks)
    latest_start_block = max(selected_start_blocks)
    vault_addresses = {ref.address.lower() for ref in selected_refs}

    if end_block is not None and start_block >= end_block:
        logger.info("Skipping chain %s price scan: %d Upshift vaults already caught up at block %s", web3.eth.chain_id, len(selected_vaults), end_block)
        return None

    logger.info(
        "Scanning %d Upshift vaults on chain %s once from block %d; latest per-vault start=%d, rewrite_targeted=%s",
        len(selected_vaults),
        web3.eth.chain_id,
        start_block,
        latest_start_block,
        rewrite_targeted,
    )

    # Remove only targeted Upshift vault reader states. This prevents stale
    # state from skipping a missing backfill, while preserving every other
    # vault state on this and other chains.
    target_specs = {ref.get_spec() for ref in selected_refs}
    scan_reader_states = {state_spec: state for state_spec, state in reader_states.items() if state_spec not in target_specs}
    hypersync_config = configure_hypersync_from_env(web3)
    result = scan_historical_prices_to_parquet(
        output_fname=price_path,
        web3=web3,
        web3factory=MultiProviderWeb3Factory(json_rpc_url, retries=5),
        vaults=selected_vaults,
        token_cache=token_cache,
        start_block=start_block,
        end_block=end_block,
        step=get_step_for_frequency(refs[0].chain_id, frequency),
        chunk_size=32,
        max_workers=max_workers,
        frequency=frequency,
        reader_states=scan_reader_states,
        hypersync_client=hypersync_config.hypersync_client,
        vault_addresses=vault_addresses,
    )

    reader_states.clear()
    reader_states.update(result["reader_states"])
    logger.info("Price scan result:\n%s", pformat_scan_result(result))
    return result


def repair_chain(  # noqa: PLR0917 - explicit operational script arguments keep the call site auditable.
    chain_id: int,
    refs: list[UpshiftVaultReference],
    vault_db: VaultDatabase,
    vault_db_path: Path,
    price_path: Path,
    reader_state_path: Path,
    *,
    dry_run: bool,
) -> ChainRepairResult:
    """Repair all configured Upshift vaults on one chain."""
    result = ChainRepairResult(chain_id=chain_id)
    updated_at = native_datetime_utc_now()

    if not is_supported_chain(chain_id):
        result.skipped_unsupported = len(refs)
        logger.warning(
            "Skipping unsupported Upshift chain %s with %d API vaults: chain is not configured in eth_defi.chain",
            chain_id,
            len(refs),
        )
        return result

    for ref in refs:
        if upsert_lead(vault_db, ref, updated_at):
            result.lead_upserts += 1

    if dry_run:
        logger.info("Dry run: not writing vault DB for chain %s", chain_id)

    json_rpc_url, env_name = read_rpc_url_for_chain(chain_id)
    if not json_rpc_url:
        logger.warning(
            "Skipping metadata and prices for chain %s (%s): no RPC env set. Tried: %s",
            chain_id,
            get_chain_name(chain_id),
            ", ".join(get_rpc_env_candidates(chain_id)),
        )
        if not dry_run:
            vault_db_path.parent.mkdir(parents=True, exist_ok=True)
            vault_db.write(vault_db_path)
        return result

    web3 = create_multi_provider_web3(json_rpc_url)
    if web3.eth.chain_id != chain_id:
        raise ValueError(f"RPC env {env_name} points to chain {web3.eth.chain_id}, expected {chain_id}")

    logger.info("Repairing %d Upshift vaults on chain %s using %s (%s)", len(refs), chain_id, env_name, get_provider_name(web3.provider))

    token_cache = TokenDiskCache()
    for ref in refs:
        if dry_run:
            continue

        try:
            if upsert_metadata_row(web3, vault_db, token_cache, ref, updated_at):
                result.metadata_upserts += 1
            else:
                result.metadata_preserved += 1
        except (BadFunctionCallOutput, ContractLogicError, ValueError, Web3Exception, TimeoutError, OSError) as e:
            result.metadata_failures += 1
            logger.warning("Could not upsert metadata for %s %s (%s): %s", ref.get_spec(), ref.name, ref.internal_type, e)

    if dry_run:
        return result

    vault_db_path.parent.mkdir(parents=True, exist_ok=True)
    vault_db.write(vault_db_path)
    token_cache.commit()

    if not parse_bool_env("UPSHIFT_SCAN_PRICES", default=True):
        return result

    frequency = os.environ.get("FREQUENCY", "1h")
    if frequency not in {"1h", "1d"}:
        raise ValueError(f"Unsupported FREQUENCY: {frequency}")

    max_workers = int(os.environ.get("MAX_WORKERS", "8"))
    end_block = parse_optional_int_env("END_BLOCK")
    if end_block is None:
        end_block = web3.eth.block_number
    rewrite_targeted = parse_bool_env("UPSHIFT_REWRITE_TARGETED", default=False)
    reader_states = load_reader_states(reader_state_path)

    price_refs: list[UpshiftVaultReference] = []
    price_vaults: list[VaultBase] = []
    for ref in refs:
        vault = create_price_vault(web3, vault_db, token_cache, ref)
        if vault is None:
            result.skipped_unsupported += 1
            logger.info("Skipping price scan for unsupported vault %s %s (%s)", ref.get_spec(), ref.name, ref.internal_type)
            continue
        price_refs.append(ref)
        price_vaults.append(vault)

    if not price_vaults:
        logger.info("Skipping chain %s price scan: no supported Upshift vault readers", chain_id)
        token_cache.commit()
        return result

    try:
        scan_result = scan_chain_price_history(
            web3=web3,
            json_rpc_url=json_rpc_url,
            token_cache=token_cache,
            reader_states=reader_states,
            refs=price_refs,
            vaults=price_vaults,
            price_path=price_path,
            end_block=end_block,
            frequency=frequency,
            max_workers=max_workers,
            rewrite_targeted=rewrite_targeted,
        )
        if scan_result is not None:
            result.price_scans += 1
            write_reader_states(reader_state_path, reader_states)
    except (AssertionError, BadFunctionCallOutput, ContractLogicError, ValueError, Web3Exception, TimeoutError, OSError) as e:
        result.price_failures += 1
        logger.warning("Could not scan prices for chain %s Upshift vault batch: %s", chain_id, e)

    token_cache.commit()
    return result


def main() -> None:
    """Run the targeted Upshift repair."""
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))

    dry_run = parse_bool_env("DRY_RUN", default=False)
    vault_db_path = Path(os.environ.get("VAULT_DB_PATH", str(DEFAULT_VAULT_DATABASE))).expanduser()
    price_path = Path(os.environ.get("UNCLEANED_PRICE_DATABASE", str(DEFAULT_UNCLEANED_PRICE_DATABASE))).expanduser()
    reader_state_path = Path(os.environ.get("READER_STATE_DATABASE", str(DEFAULT_READER_STATE_DATABASE))).expanduser()

    refs = filter_references(load_upshift_vault_references())
    if not refs:
        message = "No Upshift vault references selected"
        raise RuntimeError(message)

    logger.info("Selected %d Upshift EVM vaults across %d chains", len(refs), len({ref.chain_id for ref in refs}))
    vault_db = load_vault_database(vault_db_path)

    refs_by_chain: dict[int, list[UpshiftVaultReference]] = defaultdict(list)
    for ref in refs:
        refs_by_chain[ref.chain_id].append(ref)

    results = []
    for chain_id, chain_refs in sorted(refs_by_chain.items()):
        results.append(
            repair_chain(
                chain_id=chain_id,
                refs=chain_refs,
                vault_db=vault_db,
                vault_db_path=vault_db_path,
                price_path=price_path,
                reader_state_path=reader_state_path,
                dry_run=dry_run,
            )
        )

    logger.info("Upshift repair summary")
    for result in results:
        logger.info(
            "chain=%s lead_upserts=%d metadata_upserts=%d metadata_preserved=%d metadata_failures=%d price_scans=%d price_failures=%d skipped_unsupported=%d",
            result.chain_id,
            result.lead_upserts,
            result.metadata_upserts,
            result.metadata_preserved,
            result.metadata_failures,
            result.price_scans,
            result.price_failures,
            result.skipped_unsupported,
        )

    failures = sum(result.metadata_failures + result.price_failures for result in results)
    if failures:
        logger.warning("Completed with %d per-vault failures; see logs above", failures)
    else:
        logger.info("All selected Upshift repairs completed")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
