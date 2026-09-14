"""Yearn primary-list exclusions used for protocol attribution.

The snapshot contains contracts whose entry in `Yearn's public vault registry
<https://kong.yearn.fi/api/rest/list/vaults>`__ has ``inclusion.isSet`` and not
``inclusion.isYearn``. This is a Yearn front-end list-membership decision, not
a statement of who deployed or operates a contract. In particular, it includes
some Yearn-origin Katana pre-deposit vaults.

The scanner preserves technical Yearn interface features for the appropriate
vault adapter, but records a provenance marker so protocol and curator views do
not attribute these contracts to Yearn. This deliberately applies to every
matching entry, including Yearn-origin and Yearn Juiced vaults, because the
product rule follows the warning's exact predicate.

Snapshot refreshed: 2026-09-14.
"""

from eth_typing import HexAddress

from eth_defi.erc_4626.core import ERC4626Feature

#: Addresses excluded from Yearn's primary vault list by the public registry.
YEARN_REGISTRY_EXCLUDED_VAULTS_BY_CHAIN: dict[int, frozenset[HexAddress]] = {
    1: frozenset(
        {
            "0x02b00478084692436a7b57b8bb5fef343cd4933f",
            "0x0655977feb2f289a4ab78af67bab0d17aab84367",
            "0x09fec9578fa5790d44358ef6906eaf76e1a8e55a",
            "0x0abd93da8387b5ef0511a2859d85d84fe4519e94",
            "0x120b128cf1a5117fe8e9e12b79b3437e7396bd14",
            "0x140d0d77eec240f9d3a07c92df37e257cf506081",
            "0x1489310ab64830aa242b957b3a0ff5efb28093b2",
            "0x17afab7686c088b0e2feda46cfa7c656db00a9c2",
            "0x17e04994ad0342a4e5afacff723a26862ea500a7",
            "0x18170f7e2da9f36758e191cd80ecbe077b6e55ea",
            "0x18f365ecce58fa865ec9fb027e9175125db93b91",
            "0x1a985b7a40d19b7263e867d13cd89c787c787925",
            "0x1b0642d5adf25aad960c5d5bb189dedcdd51223c",
            "0x1d8d9048b43723ff83188d1b24cae48c3d2081da",
            "0x21b0f8109f7f797e8b40a6189d2b773be642666e",
            "0x254bd33e2f62713f893f0842c99e68f855cda315",
            "0x26fcb50eec367ddab060ccf5e7394cecd95f7db2",
            "0x270bf0c8a184ff528dba84fba14493b1ce48517e",
            "0x281128b2099ab21e10ecb63750f52be5deffdecf",
            "0x28b2dc219c047b10f353caa2934ad8803e089a88",
            "0x28b703ab847fb4a9548af7a4bce38cd94f508298",
            "0x29a7adb4c69f697b6d9f04114175ae9989884926",
            "0x29ee8c3fb7ea0b52261b18782f1a060c2e210afa",
            "0x2c517bf1db472ab1d65f0d776b7d947986c76ecf",
            "0x2ca52c78d7932e285f2ea5928fb7251baec3f166",
            "0x2d97ccda31fa1e97b1b7f6c046b9b9b36fc558aa",
            "0x2fcc9f8f7ab421856f8c1a1b2b4b794f6d930022",
            "0x30dd04107dee5e64fbd2981f7ab980b9f82c2429",
            "0x3211855bd9dddae065b7b56e9a6a64fa90a3fcd8",
            "0x358d94b5b2f147d741088803d932acb566acb7b6",
            "0x35d7488d13937a56556d19a20b336e59d4bd6b88",
            "0x381e0be61b3aeb1f321c4775046e6f57bc84c804",
            "0x38be95a8837d715b31e91f3e223d52af6ca37e1f",
            "0x3a49f5a6a8af9b2103d882278193112cf9f73a25",
            "0x3ac911110c7d1d14d9a65af1204d5af58ac6bb57",
            "0x3bc9731de20aa4fa7d6eb6bb84b359be3f28d230",
            "0x3ed6aa32c930253fc990de58ff882b9186cd0072",
            "0x408295aaf78cc6d12b511a752b05f1554eaceed1",
            "0x4147cb38fae27a737ecd55551d3315fec11c28d2",
            "0x42842754abce504e12c20e434af8960fdf85c833",
            "0x437d41b2ebef15a91618f4bf010fa4751bb48be6",
            "0x43fc23e85a2f1078de7c0ab7e6b5c1eaa648f8f3",
            "0x44abee98cc69d9d81782dba715ae37ebbd381975",
            "0x44e0a1134157d911c9a16ebedfc86e825b2f1c5f",
            "0x46e9893422b9ae9246793489433f72c548cb2455",
            "0x482cce30cde09292279371956a3c7c57bbe125a9",
            "0x48c03b6ffd0008460f8657db1037c7e09deedfcb",
            "0x48c05c25fb0c92152b57aa057e121cb2e5da81e4",
            "0x4968236bf362f1c6080ce04fd95231335614162c",
            "0x4d1ac746e4a71be24221d05a8dfcd3e1523a161c",
            "0x4df40f310cd760e5f59850c1aeb58242cdc6e2fb",
            "0x4dfdca4e46761fe88d0db622e1d026c831410a8c",
            "0x4e7aa7085ef4651ee2270859c1702fbe5a7cf847",
            "0x503e0bab6acdae73ea7fb7cf6ae5792014dbe935",
            "0x531f882a76a949c51457a3eb9e5d09a9cad50418",
            "0x5326d3e4c769bf9ea532470ecbf6d4898b81756b",
            "0x53eeddb86c822f8d4cc031ebfc84ccaaaa17d0a8",
            "0x552868611d2641144454140dded98e6160b3bfc9",
            "0x55f85ebb12f54c58ac1e8c1d56e12e4767ff262e",
            "0x5b88a0ddf1ca18e50e7a7cd86eee441ae7d51eb5",
            "0x5ff1ba7273968c4a166ca99d79df9f9ad712091b",
            "0x610e01895cb39078890e3fe5239b78a51c07892f",
            "0x620082f33d442e5806122f71a72b12134f75ff2d",
            "0x64dc55d76d1541e0853cbd7cf6586615e9e6f4c7",
            "0x6568921f9059b6b8a3902651783a7a0e74ca83ff",
            "0x661077153a02628786d180dbdf633f790774df10",
            "0x66c44eb61bd22a1f53b18e1570e514de39c79997",
            "0x697c54a84d83f37380d034e2bfc6f7ce8d89f4ee",
            "0x6d8f61e142e8f789bfb9e6227a18eee25beec4fc",
            "0x6f24a0228c1e8b32424cc5d78a49ef33b6ff58c4",
            "0x6f8430f4a5cb815bb72e59000511db24d0d798f9",
            "0x70568ada62b82282b030c220ad96bebc4e57fadb",
            "0x7204d6dfbba99827c93ab6877d152e724d16a2f0",
            "0x73e4c11b670ef9c025a030a20b72cb9150e54523",
            "0x763b5be2a58e47d4f9299c57063184e083626072",
            "0x76cf74eba6413fd290f5599a2ec412882119af32",
            "0x77570cfecf83bc6bb08e2cd9e8537aea9f97ea2f",
            "0x7810e336f8b3d9ae7c15632f11f80ae715dde900",
            "0x7b5a0182e400b241b317e781a4e9dedfc1429822",
            "0x7c7569585ba5e7f5c0b1d18ab630d1769ca27193",
            "0x81d40243c649f7d6092295f50f4b1d3a8e53da99",
            "0x8670120c32de7bc990e0fe3bbd04704e98492f0a",
            "0x87588d3fbcdff1bf84555f0a22056d94534a74a6",
            "0x87898bf3197e6713397a83c9ac0e04481c33f441",
            "0x89678540206e7d6964a4e22ae5cf4ac55926651a",
            "0x8b98165bc61841f1df64e1509a450e11e25181cc",
            "0x8bdb719c46e010c6a0701195e8ee7f9712f24588",
            "0x8f03f179d65fa98f27060c06eba6349852bae34a",
            "0x8f22f75c9ba36d7dba4061d9c73781f206c74a3d",
            "0x91603c7fb539f2da2da93ba6bec696a47e46bba2",
            "0x92c82f5f771f6a44cfa09357dd0575b81bf5f728",
            "0x93ebc3ca85f96afd72edb914e833fe18888de179",
            "0x95f19b19aff698169a1a0bbc28a2e47b14cb9a86",
            "0x968d4227eecc1d47e46c68d19de2b6858a3f39a7",
            "0x980703fae7ab5bdefc3e1cefc9d15014461fe39d",
            "0x9bb4d5a122d85e24d8c9b8025a5162fa6b385292",
            "0x9c3a4c14a249504b4cdcf5c27867be790c92bb05",
            "0x9c54050a66a2457c01b508155373ebe4d94aa0f5",
            "0x9cb8c5136ad079d3311f5b766d6fc370bf29e76b",
            "0x9cfb40acedac259b1d23e790f6c6d0c3898361ad",
            "0x9ff95d3a070259f579f26a4fe12e145edde8e65a",
            "0xa5dab32dbe68e6fa784e1e50e4f620a0477d3896",
            "0xa83308d681ee69a4afad3d5768b4bfe441305e15",
            "0xa87958c4e0cfae74b6f7a158ab639359cdf989be",
            "0xac64c681102e23f4114bc6fc873499267aeb9ea8",
            "0xaca399117ac588e1f48398d34eca76cdb1e45fa5",
            "0xae4c90620f9adf9974423b3d4135f5b661614cd3",
            "0xaed098db0e39bed6ddc2c07727b8ffc0ba470d9c",
            "0xaf2361999e087b64594df9b467d6ef1529e7693c",
            "0xb5bb36bf71edb4f5b5b3ab90ae0540944b8e6a7f",
            "0xb5ff5da1ca2ac2abd9dfabee3b105cce5a1eb057",
            "0xbd564bfe6f2366336ecd707862eda79b97bce383",
            "0xc0c31393c0b406c9ccfdbfb772202ce3a7cb667e",
            "0xc2e73c0f03d977675f1febda74c3c2686ff1e9e7",
            "0xc4244a652872ac9b9b6d1a0dc3b1cbc3a4f54f5e",
            "0xcc6a16be713f6a714f68b0e1f4914fd3db15fbef",
            "0xcd3b300a0dced996608f12e3c09f7deaa3022863",
            "0xcf3005699a305ad1ba800c00fddad8d7e905ec49",
            "0xcf7b325aeddcb3dfdfa752e277c8e437dd542828",
            "0xcfb23d05f32ea0be0dbb5078d189cca89688945e",
            "0xd469e2a86331a9286de08dfe76083d9e8b168ca9",
            "0xd699751ec0e1e52907d3cb52f001c95ea31f5655",
            "0xd74aecff75e15767734a1d59b9dc7cfb278b3dbe",
            "0xd91b6ded55c0fba2c1e88f2cdf6e051ecd788952",
            "0xdcaba03509e719a1e136b7266cbd8ad8399b43b7",
            "0xdcba6f240bb7e8ea00f23a4f970b29d22ddd860e",
            "0xdead8193791a6dc90564e6856ad12878200e90ec",
            "0xe1ac97e2616ad80f69f705ff007a4bbb3655544a",
            "0xe24ba27551abe96ca401d39761ca2319ea14e3cb",
            "0xe2fdf0526629d85b719dcbc6efe833b1b5b3cb36",
            "0xe30f5409c13f77ee94480800e7be329342e14718",
            "0xe3faed70ccaf49c9baa95ca8d41b1777d7ae440f",
            "0xe4b0f1967a3098e26e4c939036d58d713b30ddbf",
            "0xe64542b15f18f6926b49e0ef53681a84dc8c28f1",
            "0xe7383fe1c0c2d26d3ed90049552a9822067ec333",
            "0xe7c4bd9ae62b96bf1b77ac5edb7ccdeb662b479c",
            "0xe7e1043ea932dbaa7de79450824a92f40481c82b",
            "0xeadc18ca9363565e1c8bc5415db0110d7c91cde5",
            "0xec1e21c752081efb25612bf0a892abe9c14b0db4",
            "0xecb32aa437efcd4e6dd4e750684846b66a2010c6",
            "0xeeb4290377763a3fadbfbf81ab8f2958aec8bb96",
            "0xef0face04f2d9cc4dc47f560b0dbfc5dd6b56cbb",
            "0xef3f0167b97eff6979aef3bf7ad29cd8e20c5b09",
            "0xf20b02131c45b22e147c98e30cc889a20dc8a00d",
            "0xf2a9ef3b50d7615b455691540ccc21ef934daab6",
            "0xf470eb50b4a60c9b069f7fd6032532b8f5cc014d",
            "0xf543fb92a316885f345319257efb3b444dcb5284",
            "0xf7a7873c32c73813b5411ca58eaadbbb6d806e4f",
            "0xf96e4c5eee8095f918ed3312b3de3d68156798f8",
            "0xfb2e038f179f5dfb34a695225f8fd9b854360636",
            "0xfc1adcb2c25ff45cf26870852071d9dab337d707",
            "0xfcc0dd0aebf4ea4b751799169e45f014ba2e02dd",
            "0xfe08fc631be2da2b47aa6adba275adab2197c0cd",
            "0xff8b9980fda386cafaafab472963c4cb5fb8c412",
            "0xffcaf6549a229d63351b71313559edbe0c467b78",
            "0xffeeb0b47d0ede7e4ff3e7851cefb944a6a74e0f",
        }
    ),
    100: frozenset(
        {
            "0x1b66b4a3a80ebcfa3ecabf5580cd9929aba57bb1",
            "0x4b40c9ff9d654fd7f1e24ed66d8f0f260b199b43",
            "0x5012c6bf79b3047ecfff2f212dffda4d2128188f",
            "0x94c5786393ab223e0fc111d7463405f165ce86e3",
            "0x99474363f03d21375da1af4c9694cabcf995e02a",
            "0xa21cc1a4a239708690134baed3a1b93cad55f625",
            "0xa48359e7e970be9f38fb6d183c57c8d0b28268b9",
        }
    ),
    137: frozenset(
        {
            "0x41f63bd9180eaaa58185e1a88e2b22cea52634b9",
            "0x4572c3c8f2cfdd77d1dd7987bc46ca20d72f32b0",
            "0x56b33795c24b1c46049031ea9bb8466c5b6cb3fb",
            "0x72449578349b69544de5ff2fb70f0cfba18a6ad1",
            "0x9bc04dabde16f559d9c8dde00325aad15f0b0a47",
            "0xb40f69b0cc508dfbaa0c8c5fd34107e3a191ac95",
            "0xd2d0c98684050222ec9418b21e97e3f9a70d9fb0",
            "0xd600d760477ffaed25bf2450e29ff6bd3c47c9ea",
            "0xd7be0ec4f6d7ec344311f8284b448f5bccfd0fd1",
            "0xd92a3a0ad7fbe4b8f6382e0cada87471ef800515",
            "0xd9df4b212c9565a6281a2025b7ed87dd0f4a6cb4",
            "0xe4244bf798f63881591113126b64493ac36ac525",
            "0xf8fd29a8f95e96a125984c4e384534ace18cbfbd",
            "0xfc47efc0baf8ee64b2d94ef2be400d90ceca0f47",
            "0xfd179b9793b467fa3dd85edc715ae580b0240090",
            "0xffcf7fb615614b96f5730af9da3d7114c119aabe",
        }
    ),
    146: frozenset(
        {
            "0x2f7397fd2d49e5b636ef44503771b17eded67620",
            "0x8db97437f8e28a11019128d7b2604a42e333025e",
            "0xa355715145530fe4b0be8f79946f619f77c8fd65",
            "0xa369c32fd1fa2cd265f0bdf76f39201ff333f6ac",
            "0xe272e402d817c8f20ac4426aac21b95a13f76c99",
            "0xf8815e49bd30c79d7a026eae414aa92faff29541",
        }
    ),
    8453: frozenset(
        {
            "0x0dcb575fd705f1352b0c0f8fa6a64a0561ce8c3c",
            "0x26adf26c86bef23c4e1f66ddf2a62e6ecb98152e",
            "0x338dfa34f66b6922b9edec82da86519ce7938184",
            "0x4d73de04431050347d6d94f7fab5887ea6737a18",
            "0x50fd1e6e0e2153c2b26ebbcd9bcded4639a1aae3",
            "0x5b6c2b7ac69ad287c7c9eb85e725f3799695df09",
            "0x61812b7226d8cd931be995177056c0e90a8554fa",
            "0x69efa3cd7fc773fe227b9cc4f41132dcde020a29",
            "0x7509d40ef0087611d2dfd76f0752d32783b8c2a2",
            "0x7bc2fdef0cf03d8488d7ab06c4a36b61725d06ec",
            "0x7f678f3f661364dcad39cc2946c31388a43ce1f4",
            "0x854020fb245753c1e71dff9d1b14528518050afb",
            "0x878b75772c0abf2d22a9d4a5ae062b1aadaaebb3",
            "0x8907b99db2393af47a48b4bb91abb509b52c5d8f",
            "0x8e462a2b3a8c2713b5e157f5f3a3cd503588e59a",
            "0x9eb7675377d1191bed352552267e5b5e592fcf53",
            "0xa7a8eb9f200ddd4137f71b92d3d44b403af20e44",
            "0xbcbe4815fa99971b4dd918adfb12599679ca4b0e",
            "0xc31dfb6b3e053c4e06fa76853a6b3e367bc2233d",
            "0xcad1719469bc67341eec1a29a07684fe075596da",
            "0xd1468af648565f11393e4033cb0cd270b62495c9",
            "0xde9de9dfb3cc912cccda145bd6cb8a145a66ea92",
            "0xf595152ceaf50fa854c4606a457d2388375d4fa0",
            "0xfeeefb32e88efef50aedd9630e07fa4f47ae886d",
            "0xff3035968a44df696297d710cb7c0d7f807bf641",
            "0xffec1057a5dd33fc74f453b88a6c62cc3d9446be",
        }
    ),
    42161: frozenset(
        {
            "0x12bf88c63bf060355a8d2e4c6ee960365a5e8335",
            "0x20db1d8e053e448428b19dbc7c6eed1ccb851023",
            "0x244f22dd5f309506129513d0954787d52d80ba3d",
            "0x2e48847fe29c3883c98125cb2c44244d6602d549",
            "0x2ec7fa7a87eba241636b2c03ba287ac3897601fc",
            "0x39ce72ace7722fe38a4aff02bfbe7789b58c6905",
            "0x482cc95bc6c92d6254529dc2d45095663ae726a2",
            "0x54a77d0628e472643753ccb6cd929ac768f5f634",
            "0x67c2e47682d7b32c3294a67a0cd14c150cbc10f0",
            "0x723a85b4554d79ed20e061efc64c5a6e04f196aa",
            "0x7f9b714965cd915011e651f5cd9622315b60616a",
            "0x7fd798ed77368e787e216dfdb5be5f6cae4c5ad5",
            "0x801c26fcfd916719631e0cf7d36ca1e049df0373",
            "0x989b2a056db791aca934686eebe9b0514d635a11",
            "0x9cd4a7c7e7869aa8a2a6b28da6b4cfa1f3e2f1f0",
            "0x9ed00a5dc4b8e96ecda1b863975a597917948c38",
            "0xa7fea37ae7eb3fe0b26ab454399cf42fe5e308a3",
            "0xace460a8e3f5f219ea9ddb10a0764aedee8c09e1",
            "0xc80e7eee121570f36c629378de42eb80dc22ecad",
            "0xd21e503d8ac357a7c8f8897bf84a4bfd14fec6e1",
            "0xd49afd20aff04f86127c4b7b2ed893aad82f8d9b",
            "0xe6dbfb035b44e94d07f7b3e4f6bfbf1c6e68e3d0",
            "0xea7dd7285350d1153510069ac4b382a96e479683",
            "0xeadb712d95f347c3223261ef0f67505579eac159",
            "0xedc4949adb7c6e7aeed4ae2545cb4aef82583bd8",
            "0xf7d015bd465beaf7360064f451fdf16949199848",
            "0xfe7aebd759c6e251afa44af17de4cc88f83ff6a4",
        }
    ),
    747474: frozenset(
        {
            "0x639bccf37cc0415812a6f110cfca33127a81c0e9",
            "0xcea8d1281ddacfdcd68eaaf88dd59ced0e627746",
        }
    ),
}


def is_yearn_registry_excluded_vault(chain_id: int | None, vault_address: HexAddress) -> bool:
    """Check whether Yearn excludes a vault from its primary product list.

    The check normalises the address because scanner and explorer sources use
    different EIP-55 casing. A missing chain/address pair means only that this
    snapshot has no primary-list exclusion for it.

    :param chain_id:
        EVM chain identifier for the vault deployment, if known.
    :param vault_address:
        ERC-4626 vault contract address.
    :return:
        ``True`` when the snapshot records the primary-list exclusion.
    """
    return vault_address.lower() in YEARN_REGISTRY_EXCLUDED_VAULTS_BY_CHAIN.get(chain_id, frozenset())


def add_yearn_registry_exclusion(
    chain_id: int | None,
    vault_address: HexAddress,
    features: set[ERC4626Feature],
) -> set[ERC4626Feature]:
    """Add a Yearn primary-list exclusion marker to matching vault features.

    The marker changes protocol and curator attribution only. Technical Yearn
    interface features stay intact, allowing the scanner to select the correct
    Yearn adapter for deposit, fee, and permission handling.

    :param chain_id:
        EVM chain identifier for the vault deployment, if known.
    :param vault_address:
        ERC-4626 vault contract address.
    :param features:
        Features returned by the generic ABI probe.
    :return:
        Original features, with the exclusion marker added when applicable.
    """
    if not is_yearn_registry_excluded_vault(chain_id, vault_address):
        return features
    return features | {ERC4626Feature.yearn_registry_excluded}
