"""Manage the official Aave v3 deployer.

Deploy Aave v3 to a local Anvil test backend using the official Aave v3 deployer.

`See aave-deployer repo for more details <https://github.com/aave/aave-v3-deploy>`__.

.. note ::

    The hardhat export has been bundled and you unlikely need to do run the Aave deployer yourself.

The Aavec deployment localhost report belwo. Addesses seem to be deterministc:

.. code-block:: text

    MARKET_NAME=Aave npx hardhat --network hardhat deploy
    Nothing to compile
    No need to generate any newer typings.

    Accounts
    ========
    ┌─────────┬──────────────────────────────────┬──────────────────────────────────────────────┬───────────┐
    │ (index) │               name               │                   account                    │  balance  │
    ├─────────┼──────────────────────────────────┼──────────────────────────────────────────────┼───────────┤
    │    0    │            'deployer'            │ '0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266' │ '10000.0' │
    │    1    │            'aclAdmin'            │ '0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266' │ '10000.0' │
    │    2    │         'emergencyAdmin'         │ '0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266' │ '10000.0' │
    │    3    │           'poolAdmin'            │ '0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266' │ '10000.0' │
    │    4    │ 'addressesProviderRegistryOwner' │ '0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266' │ '10000.0' │
    │    5    │       'treasuryProxyAdmin'       │ '0x70997970C51812dc3A010C7d01b50e0d17dc79C8' │ '10000.0' │
    │    6    │      'incentivesProxyAdmin'      │ '0x70997970C51812dc3A010C7d01b50e0d17dc79C8' │ '10000.0' │
    │    7    │   'incentivesEmissionManager'    │ '0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266' │ '10000.0' │
    │    8    │     'incentivesRewardsVault'     │ '0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266' │ '10000.0' │
    └─────────┴──────────────────────────────────┴──────────────────────────────────────────────┴───────────┘
    deploying "PoolAddressesProviderRegistry" (tx: 0x5812317317304f8188e61bf96ffc3f49c82a4106babc3b4b9a6a7ec5a082bbf2)...: deployed at 0x5FbDB2315678afecb367f032d93F642f64180aa3 with 799500 gas
    [Deployment] Transferred ownership of PoolAddressesProviderRegistry to: 0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266
    deploying "SupplyLogic" (tx: 0xf084fa1dbb8f3feb8bf11ebaae3c15045fc3a23ca4399fd0f4193055058cc657)...: deployed at 0x9fE46736679d2D9a65F0992F2272dE9f3c7fa6e0 with 3290846 gas
    deploying "BorrowLogic" (tx: 0x3a1d10231927f25739714971c0792cde588dd3edb3dac9dc1a49a6cef5bbd302)...: deployed at 0xCf7Ed3AccA5a467e9e704C703E8D87F634fB0Fc9 with 4930630 gas
    deploying "LiquidationLogic" (tx: 0x8c6a125d1fb5e698e978ed2a9d0c9056183c3cc18af60960244662936e42471f)...: deployed at 0xDc64a140Aa3E981100a9becA4E685f962f0cF6C9 with 3443045 gas
    deploying "EModeLogic" (tx: 0xebda10ccee2f89ee7c7a3859464e8311b3ea201eae04d6b2bf26de98228271ca)...: deployed at 0x5FC8d32690cc91D4c39d9d3abcBD16989F875707 with 1174568 gas
    deploying "BridgeLogic" (tx: 0x9d103b9f95623f41259d3abb063044238661d41dc4c010c8beb45fef3dda6a90)...: deployed at 0x0165878A594ca255338adfa4d48449f69242Eb8F with 1832752 gas
    deploying "ConfiguratorLogic" (tx: 0xe2e5dd48343c7f77b26397fd41e78ee3a4f6866a38ff041cb464c6439023d271)...: deployed at 0xa513E6E4b8f2a923D98304ec87F64353C4D5C853 with 1941989 gas
    deploying "FlashLoanLogic" (tx: 0xf8fd0eccf821db827abfc7ff8a5ce6eabf8684223739e697007c04a368bc2f38)...: deployed at 0x2279B7A0a67DB372996a5FaB50D91eAA73d2eBe6 with 2428977 gas
    deploying "PoolLogic" (tx: 0xfbf78bfead43d44c1f8ac9ed4c5d5a81de3465850255acd3faab5bc0043e6c23)...: deployed at 0x8A791620dd6260079BF849Dc5567aDC3F2FdC318 with 2138051 gas
    deploying "Treasury-Controller" (tx: 0x6758f5c9f06ca9b11d9560acccf43dc86e4d21860a5adeafeeba3cb9ddbca5f7)...: deployed at 0xB7f8BC63BbcaD18155201308C8f3540b07f84F5e with 701700 gas
    deploying "Treasury-Implementation" (tx: 0x1fef6435cae860e1e815ca2c8a9dfb40815e09d73e50a98a1ce9af4217d28ded)...: deployed at 0xA51c1fc2f0D1a1b8494Ed1FE312d7C3a78Ed91C0 with 2116188 gas
    Live network: false
    - Deployment of FaucetOwnable contract
    deploying "Faucet-Aave" (tx: 0xdee2716c172b5204449c8238ae2927c0dc767c71b9e3425a1f588f374665c931)...: deployed at 0x0B306BF915C4d645ff596e518fAf3F9669b97016 with 466549 gas
    - Setting up testnet tokens for "Aave" market at "hardhat" network
    Deploy of TestnetERC20 contract DAI
    deploying "DAI-TestnetMintableERC20-Aave" (tx: 0x6ed6bce903c0044da080f8878ca281ed7ad6649f89d20fc3c36014722df1dd61)...: deployed at 0x959922bE3CAee4b8Cd9a407cc3ac1C251C2007B1 with 1341117 gas
    Deploy of TestnetERC20 contract LINK
    deploying "LINK-TestnetMintableERC20-Aave" (tx: 0x1c07dedbddf22e0a1e44c4afc1005c30785c8408ddec7803cc8df281df500cda)...: deployed at 0x9A9f2CCfdE556A7E9Ff0848998Aa4a0CFD8863AE with 1341141 gas
    Deploy of TestnetERC20 contract USDC
    deploying "USDC-TestnetMintableERC20-Aave" (tx: 0x7beb1b41dbb31e84379ab67f4f93c2cdb57f6c779b86962a42e78f2359068fc1)...: deployed at 0x68B1D87F95878fE05B998F19b66F4baba5De1aed with 1341141 gas
    Deploy of TestnetERC20 contract WBTC
    deploying "WBTC-TestnetMintableERC20-Aave" (tx: 0xa74b160ee7129902a570bd049f82224057635388c3088bbc80c84d34975c3eda)...: deployed at 0x3Aa5ebB10DC797CAC828524e59A333d0A371443c with 1341141 gas
    Deploy of WETH9 mock
    deploying "WETH-TestnetMintableERC20-Aave" (tx: 0x99707c9863203879ff864d97aa7485678b1ff3013684b84fd31c7e1fcb33e6cd)...: deployed at 0xc6e7DF5E7b4f2A278906862b61205850344D4e7d with 905129 gas
    Deploy of TestnetERC20 contract USDT
    deploying "USDT-TestnetMintableERC20-Aave" (tx: 0x792f81ba7b5ab12bcababa477fbeef02251cde5534edb7de56549c5e8d94ec75)...: deployed at 0x59b670e9fA9D0A427751Af201D676719a970857b with 1341141 gas
    Deploy of TestnetERC20 contract AAVE
    deploying "AAVE-TestnetMintableERC20-Aave" (tx: 0xecbf1ee96f5721e82c0b85bd58aceaaff73d9da7a215deadffee731b0c261a19)...: deployed at 0x4ed7c70F96B99c776995fB64377f0d4aB3B0e1C1 with 1341141 gas
    Deploy of TestnetERC20 contract EURS
    deploying "EURS-TestnetMintableERC20-Aave" (tx: 0xadac6e9819186a9a01b12bfbb7a4660b0ec8b6bbf4dfdc29421e79df21b225a2)...: deployed at 0x322813Fd9A801c5507c9de605d63CEA4f2CE6c44 with 1341141 gas
    [Deployment][WARNING] Remember to setup the above testnet addresses at the ReservesConfig field inside the market configuration file and reuse testnet tokens
    [Deployment][WARNING] Remember to setup the Native Token Wrapper (ex WETH or WMATIC) at `helpers/constants.ts`
    [WARNING] Using deployed Testnet tokens instead of ReserveAssets from configuration file
    {
      DAI: '0x959922bE3CAee4b8Cd9a407cc3ac1C251C2007B1',
      LINK: '0x9A9f2CCfdE556A7E9Ff0848998Aa4a0CFD8863AE',
      USDC: '0x68B1D87F95878fE05B998F19b66F4baba5De1aed',
      WBTC: '0x3Aa5ebB10DC797CAC828524e59A333d0A371443c',
      WETH: '0xc6e7DF5E7b4f2A278906862b61205850344D4e7d',
      USDT: '0x59b670e9fA9D0A427751Af201D676719a970857b',
      AAVE: '0x4ed7c70F96B99c776995fB64377f0d4aB3B0e1C1',
      EURS: '0x322813Fd9A801c5507c9de605d63CEA4f2CE6c44'
    }
    deploying "PoolAddressesProvider-Aave" (tx: 0x9597c87e9a8117f8e09beede216ac4bbd0d7afd1f8a93bcc0ae7803d78a7b7c4)...: deployed at 0xa85233C63b9Ee964Add6F2cffe00Fd84eb32338f with 2234555 gas
    Added LendingPoolAddressesProvider with address "0xa85233C63b9Ee964Add6F2cffe00Fd84eb32338f" to registry located at 0x5FbDB2315678afecb367f032d93F642f64180aa3
    deploying "PoolDataProvider-Aave" (tx: 0xd18b867fb0b92d03b8bf334e5849ccbd6fce9e387e57ba76efe353d4427955d9)...: deployed at 0x09635F643e140090A9A8Dcd712eD6285858ceBef with 2694618 gas
    [WARNING] Using deployed Testnet tokens instead of ReserveAssets from configuration file
    deploying "DAI-TestnetPriceAggregator-Aave" (tx: 0x6af71965b0b59d84d9650a31738da4b88cf8a2963fcaa320544bc37d64354d29)...: deployed at 0x67d269191c92Caf3cD7723F116c85e6E9bf55933 with 114466 gas
    deploying "LINK-TestnetPriceAggregator-Aave" (tx: 0x58608841e314d13b16d270d8a6f3cc868e4cb3a2860f78929529cd91c5e93493)...: deployed at 0xE6E340D132b5f46d1e472DebcD681B2aBc16e57E with 114466 gas
    deploying "USDC-TestnetPriceAggregator-Aave" (tx: 0x7ba120c7adf08028aedb17d3b667f29f7892922ecd4fc083a4249b2d59637b5c)...: deployed at 0xc3e53F4d16Ae77Db1c982e75a937B9f60FE63690 with 114466 gas
    deploying "WBTC-TestnetPriceAggregator-Aave" (tx: 0x5d85a59270181f185653c1d971ba3206ecdca9f06c0432cd53b34c8c5c2656e2)...: deployed at 0x84eA74d481Ee0A5332c457a4d796187F6Ba67fEB with 114490 gas
    deploying "WETH-TestnetPriceAggregator-Aave" (tx: 0xdf12c2e4033d7109ace47300f029b2c0575d5840c9832480e2db7d59c80d2e2b)...: deployed at 0x9E545E3C0baAB3E08CdfD552C960A1050f373042 with 114478 gas
    deploying "USDT-TestnetPriceAggregator-Aave" (tx: 0xacb4a932a46f59bea3efaee12b1796f8959bbf8605d3422f1435b9aa28f5f88a)...: deployed at 0xa82fF9aFd8f496c3d6ac40E2a0F282E47488CFc9 with 114466 gas
    deploying "AAVE-TestnetPriceAggregator-Aave" (tx: 0x517efc91fd2bfaddad559f3dd94211d83945ab08e3f7e84712567f6094859e43)...: deployed at 0x1613beB3B2C4f22Ee086B2b38C1476A3cE7f78E8 with 114478 gas
    deploying "EURS-TestnetPriceAggregator-Aave" (tx: 0x9330ddb88ac2656a26e36b6bd1303bb0ca98edffb4f8b6266e8393686c121537)...: deployed at 0x851356ae760d987E095750cCeb3bC6014560891C with 114478 gas
    deploying "Pool-Implementation" (tx: 0x196206beb401ba893236a926832d366d0b10266fb8e29f6fa5e34f598b66d158)...: deployed at 0xf5059a5D33d5853360D16C683c16e67980206f36 with 4712826 gas
    Initialized Pool Implementation
    [INFO] Skipped L2 Pool due current network 'hardhat' is not supported
    deploying "PoolConfigurator-Implementation" (tx: 0x218bfb31921fd2c3b1317be1753788ec74825d942e4f98501a24ee9bb2e5c8d7)...: deployed at 0x998abeb3E57409262aE5b751f60747921B33613E with 5247664 gas
    Initialized PoolConfigurator Implementation
    deploying "ACLManager-Aave" (tx: 0x42e64ec1b3a4ae6f6cc2b534d040fe91996d43c8fbb6a6ebe861ee1b10b805f9)...: deployed at 0x0E801D84Fa97b50751Dbf25036d067dCf18858bF with 1155521 gas
    == Market Admins ==
    - ACL Admin 0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266
    - Pool Admin 0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266
    - Emergency Admin 0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266
    [WARNING] Using deployed Testnet tokens instead of ReserveAssets from configuration file
    [WARNING] Using deployed Mock Price Aggregators instead of ChainlinkAggregator from configuration file
    deploying "AaveOracle-Aave" (tx: 0x5f4793f1a865fd17de41a7560adaadce63a78b80f5771b0d5266f09881aec965)...: deployed at 0x36C02dA8a0983159322a80FFE9F24b1acfF8B570 with 1010732 gas
    [Deployment] Added PriceOracle 0x36C02dA8a0983159322a80FFE9F24b1acfF8B570 to PoolAddressesProvider
    [Deployment] Attached Pool implementation and deployed proxy contract:
    - Tx hash: 0x41d8295ffb142f292925dea743151f494ee446d1959756df0c442cff31809f1e
    - Deployed Proxy: 0x763e69d24a03c0c8B256e470D9fE9e0753504D07
    [Deployment] Attached PoolConfigurator implementation and deployed proxy
    - Tx hash: 0x7edc38589186110a1f7989fd2405492c72b608f16457c7c2c057d63e3af63e4c
    - Deployed Proxy: 0x46682cA783d96a4A65390211934D5714CDb788E4
    deploying "EmissionManager" (tx: 0x2fba1251a567595733b0e4b48c9fa8906df28870e6a0327b53e1c1f0b8cd8370)...: deployed at 0xCD8a1C3ba11CF5ECfa6267617243239504a98d90 with 1170680 gas
    deploying "IncentivesV2-Implementation" (tx: 0xdae672e9dcfe63184cc20b21019f6c46b2738279238b53afda7d87615f0f98f3)...: deployed at 0x82e01223d51Eb87e16A03E24687EDF0F294da6f1 with 4060353 gas
    [Deployment] Attached Rewards implementation and deployed proxy contract:
    - Tx hash: 0x265543911190550bc7b0731ba8db2e16d8a8e7d334b2cd69a6d3935a2bc73c9c
    deploying "PullRewardsTransferStrategy" (tx: 0x2284d5489e527577abd7ecc664dde77b9582e36aca978ba74f4837ce1c0231b6)...: deployed at 0xc351628EB244ec633d5f21fBD6621e1a683B1181 with 416442 gas
    [WARNING] Missing StkAave address. Skipping StakedTokenTransferStrategy deployment.
    deploying "AToken-Aave" (tx: 0xd5363041711f68f7413e044f8ad486eca13ffb1787bf677f238092d47af90f85)...: deployed at 0xcbEAF3BDe82155F56486Fb5a1072cb8baAf547cc with 3073781 gas
    deploying "DelegationAwareAToken-Aave" (tx: 0xe50189ee77b8bbdffeaeb723d51d67dbe2df0598fbd8f3e1270d64fd6eced672)...: deployed at 0xB0D4afd8879eD9F52b28595d31B441D079B2Ca07 with 3212412 gas
    deploying "StableDebtToken-Aave" (tx: 0x893212874c97872058bf85dafac394180f3d18d778f0a6df5225f8f108393c22)...: deployed at 0x922D6956C99E12DFeB3224DEA977D0939758A1Fe with 2416843 gas
    deploying "VariableDebtToken-Aave" (tx: 0xdad0e47128fc3a46bb354704172567f53a048b2b28911800e4ec8f2154f2dc17)...: deployed at 0x1fA02b2d6A771842690194Cf62D91bdd92BfE28d with 2137239 gas
    deploying "ReserveStrategy-rateStrategyVolatileOne" (tx: 0xeb4b041d63e930028fa73289307ab9c2df7e120888dee17c0ffb178f06a873c5)...: deployed at 0x04C89607413713Ec9775E14b954286519d836FEf with 722840 gas
    deploying "ReserveStrategy-rateStrategyStableOne" (tx: 0x76555688ada7fa619578dc0f97a12c5fa1359b0369fc96f19d1c5ef603631c23)...: deployed at 0x4C4a2f8c81640e47606d3fd77B353E87Ba015584 with 722828 gas
    deploying "ReserveStrategy-rateStrategyStableTwo" (tx: 0xc5957e43519925f8546fdb419a4edba5e3e831724bad5073d4716eb2acf4dee8)...: deployed at 0x21dF544947ba3E8b3c32561399E88B52Dc8b2823 with 722828 gas
    [WARNING] Using latest deployed Treasury proxy instead of ReserveFactorTreasuryAddress from configuration file
    [WARNING] Using deployed Testnet tokens instead of ReserveAssets from configuration file
    Strategy address for asset DAI: 0x21dF544947ba3E8b3c32561399E88B52Dc8b2823
    Strategy address for asset LINK: 0x04C89607413713Ec9775E14b954286519d836FEf
    Strategy address for asset USDC: 0x4C4a2f8c81640e47606d3fd77B353E87Ba015584
    Strategy address for asset WBTC: 0x04C89607413713Ec9775E14b954286519d836FEf
    Strategy address for asset WETH: 0x04C89607413713Ec9775E14b954286519d836FEf
    Strategy address for asset USDT: 0x4C4a2f8c81640e47606d3fd77B353E87Ba015584
    Strategy address for asset AAVE: 0x04C89607413713Ec9775E14b954286519d836FEf
    Strategy address for asset EURS: 0x4C4a2f8c81640e47606d3fd77B353E87Ba015584
    - Reserves initialization in 3 txs
      - Reserve ready for: DAI, LINK, USDC
        - Tx hash: 0x493ec44f200e9af1d5b1e26bbd5c5ed9a3b8a6ba11ed34890076677f38088c3a
      - Reserve ready for: WBTC, WETH, USDT
        - Tx hash: 0x48d9ce79b3bb1b567a7691226c778f384a70831c77494d031743a17b14245ff3
      - Reserve ready for: AAVE, EURS
        - Tx hash: 0x0b24637caf785011b275208380c0984c4c71742c9f6062f6334a442f90de33c1
    [Deployment] Initialized all reserves
    - Configure reserves in 1 txs
      - Init for: DAI, LINK, USDC, WBTC, WETH, USDT, AAVE, EURS
        - Tx hash: 0x84c10dc6148f109337dac705746b03e94b79408d406af5d186033229919f366f
    [Deployment] Configured all reserves
    deploying "MockFlashLoanReceiver" (tx: 0xefbc49408fe559b6512a9c506c426b5735266bee13da634c19c541c0d1175c4a)...: deployed at 0x0355B7B8cb128fA5692729Ab3AAa199C1753f726 with 649887 gas
    deploying "WalletBalanceProvider" (tx: 0x9a05297ece5e41cf4ce560b4a30a537601570b21e2f8dbfc563a09693733a0ee)...: deployed at 0xf4B146FbA71F41E0592668ffbF264F1D186b2Ca8 with 777160 gas
    [Deployments] Skipping the deployment of UiPoolDataProvider due missing constant "chainlinkAggregatorProxy" configuration at ./helpers/constants.ts
    [WARNING] Skipping the deployment of the Paraswap Liquidity Swap and Repay adapters due missing 'ParaswapRegistry' address at pool configuration.
    === Post deployment hook ===
    - Enable stable borrow in selected assets
    - Checking reserve DAI , normalized symbol DAI
      - Reserve DAI Borrow Stable Rate follows the expected configuration
    - Checking reserve LINK , normalized symbol LINK
      - Reserve LINK Borrow Stable Rate follows the expected configuration
    - Checking reserve USDC , normalized symbol USDC
      - Reserve USDC Borrow Stable Rate follows the expected configuration
    - Checking reserve WBTC , normalized symbol WBTC
      - Reserve WBTC Borrow Stable Rate follows the expected configuration
    - Checking reserve WETH , normalized symbol WETH
      - Reserve WETH Borrow Stable Rate follows the expected configuration
    - Checking reserve USDT , normalized symbol USDT
      - Reserve USDT Borrow Stable Rate follows the expected configuration
    - Checking reserve AAVE , normalized symbol AAVE
      - Reserve AAVE Borrow Stable Rate follows the expected configuration
    - Checking reserve EURS , normalized symbol EURS
      - Reserve EURS Borrow Stable Rate follows the expected configuration
    - Review rate strategies
    - Checking reserve DAI , normalized symbol DAI
      - Reserve DAI Interest Rate Strategy matches the expected configuration
    - Checking reserve LINK , normalized symbol LINK
      - Reserve LINK Interest Rate Strategy matches the expected configuration
    - Checking reserve USDC , normalized symbol USDC
      - Reserve USDC Interest Rate Strategy matches the expected configuration
    - Checking reserve WBTC , normalized symbol WBTC
      - Reserve WBTC Interest Rate Strategy matches the expected configuration
    - Checking reserve WETH , normalized symbol WETH
      - Reserve WETH Interest Rate Strategy matches the expected configuration
    - Checking reserve USDT , normalized symbol USDT
      - Reserve USDT Interest Rate Strategy matches the expected configuration
    - Checking reserve AAVE , normalized symbol AAVE
      - Reserve AAVE Interest Rate Strategy matches the expected configuration
    - Checking reserve EURS , normalized symbol EURS
      - Reserve EURS Interest Rate Strategy matches the expected configuration
    - Setup Debt Ceiling
    - Updated debt ceiling of USDT at 5,000,000.00
    - Updated debt ceiling of EURS at 5,000,000.00
    - Successfully setup debt ceiling: USDT, EURS
    - Setup Borrowable assets in Isolation Mode
    - Successfully setup isolation mode for: DAI, USDC, USDT
    - Setup E-Modes
    - Added E-Mode:
      - Label: Stablecoins
      - Id: 1
      - LTV: 9700
      - LQT: 9750
      - LQB: 10100
      - Oracle: undefined with address 0x0000000000000000000000000000000000000000
      - Added USDC asset to E-Mode Stablecoins
      - Added USDT asset to E-Mode Stablecoins
      - Added DAI asset to E-Mode Stablecoins
      - Added EURS asset to E-Mode Stablecoins
    - Setup Liquidation protocol fee
    - Successfully setup liquidation protocol fee: DAI, LINK, USDC, WBTC, WETH, USDT, AAVE, EURS
    - Pool unpaused and accepting deposits.

    Accounts after deployment
    ========
    ┌─────────┬──────────────────────────────────┬──────────────────────────────────────────────┬───────────────────────────┐
    │ (index) │               name               │                   account                    │          balance          │
    ├─────────┼──────────────────────────────────┼──────────────────────────────────────────────┼───────────────────────────┤
    │    0    │            'deployer'            │ '0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266' │ '9999.898348956972076635' │
    │    1    │            'aclAdmin'            │ '0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266' │ '9999.898348956972076635' │
    │    2    │         'emergencyAdmin'         │ '0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266' │ '9999.898348956972076635' │
    │    3    │           'poolAdmin'            │ '0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266' │ '9999.898348956972076635' │
    │    4    │ 'addressesProviderRegistryOwner' │ '0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266' │ '9999.898348956972076635' │
    │    5    │       'treasuryProxyAdmin'       │ '0x70997970C51812dc3A010C7d01b50e0d17dc79C8' │         '10000.0'         │
    │    6    │      'incentivesProxyAdmin'      │ '0x70997970C51812dc3A010C7d01b50e0d17dc79C8' │         '10000.0'         │
    │    7    │   'incentivesEmissionManager'    │ '0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266' │ '9999.898348956972076635' │
    │    8    │     'incentivesRewardsVault'     │ '0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266' │ '9999.898348956972076635' │
    └─────────┴──────────────────────────────────┴──────────────────────────────────────────────┴───────────────────────────┘

    Deployments
    ===========
    ┌─────────────────────────────────────────┬──────────────────────────────────────────────┐
    │                 (index)                 │                   address                    │
    ├─────────────────────────────────────────┼──────────────────────────────────────────────┤
    │      PoolAddressesProviderRegistry      │ '0x5FbDB2315678afecb367f032d93F642f64180aa3' │
    │               SupplyLogic               │ '0x9fE46736679d2D9a65F0992F2272dE9f3c7fa6e0' │
    │               BorrowLogic               │ '0xCf7Ed3AccA5a467e9e704C703E8D87F634fB0Fc9' │
    │            LiquidationLogic             │ '0xDc64a140Aa3E981100a9becA4E685f962f0cF6C9' │
    │               EModeLogic                │ '0x5FC8d32690cc91D4c39d9d3abcBD16989F875707' │
    │               BridgeLogic               │ '0x0165878A594ca255338adfa4d48449f69242Eb8F' │
    │            ConfiguratorLogic            │ '0xa513E6E4b8f2a923D98304ec87F64353C4D5C853' │
    │             FlashLoanLogic              │ '0x2279B7A0a67DB372996a5FaB50D91eAA73d2eBe6' │
    │                PoolLogic                │ '0x8A791620dd6260079BF849Dc5567aDC3F2FdC318' │
    │              TreasuryProxy              │ '0x610178dA211FEF7D417bC0e6FeD39F05609AD788' │
    │           Treasury-Controller           │ '0xB7f8BC63BbcaD18155201308C8f3540b07f84F5e' │
    │         Treasury-Implementation         │ '0xA51c1fc2f0D1a1b8494Ed1FE312d7C3a78Ed91C0' │
    │               Faucet-Aave               │ '0x0B306BF915C4d645ff596e518fAf3F9669b97016' │
    │       PoolAddressesProvider-Aave        │ '0xa85233C63b9Ee964Add6F2cffe00Fd84eb32338f' │
    │          PoolDataProvider-Aave          │ '0x09635F643e140090A9A8Dcd712eD6285858ceBef' │
    │     DAI-TestnetPriceAggregator-Aave     │ '0x67d269191c92Caf3cD7723F116c85e6E9bf55933' │
    │    LINK-TestnetPriceAggregator-Aave     │ '0xE6E340D132b5f46d1e472DebcD681B2aBc16e57E' │
    │    USDC-TestnetPriceAggregator-Aave     │ '0xc3e53F4d16Ae77Db1c982e75a937B9f60FE63690' │
    │    WBTC-TestnetPriceAggregator-Aave     │ '0x84eA74d481Ee0A5332c457a4d796187F6Ba67fEB' │
    │    WETH-TestnetPriceAggregator-Aave     │ '0x9E545E3C0baAB3E08CdfD552C960A1050f373042' │
    │    USDT-TestnetPriceAggregator-Aave     │ '0xa82fF9aFd8f496c3d6ac40E2a0F282E47488CFc9' │
    │    AAVE-TestnetPriceAggregator-Aave     │ '0x1613beB3B2C4f22Ee086B2b38C1476A3cE7f78E8' │
    │    EURS-TestnetPriceAggregator-Aave     │ '0x851356ae760d987E095750cCeb3bC6014560891C' │
    │           Pool-Implementation           │ '0xf5059a5D33d5853360D16C683c16e67980206f36' │
    │     PoolConfigurator-Implementation     │ '0x998abeb3E57409262aE5b751f60747921B33613E' │
    │           ReservesSetupHelper           │ '0x4826533B4897376654Bb4d4AD88B7faFD0C98528' │
    │             ACLManager-Aave             │ '0x0E801D84Fa97b50751Dbf25036d067dCf18858bF' │
    │             AaveOracle-Aave             │ '0x36C02dA8a0983159322a80FFE9F24b1acfF8B570' │
    │             Pool-Proxy-Aave             │ '0x763e69d24a03c0c8B256e470D9fE9e0753504D07' │
    │       PoolConfigurator-Proxy-Aave       │ '0x46682cA783d96a4A65390211934D5714CDb788E4' │
    │             EmissionManager             │ '0xCD8a1C3ba11CF5ECfa6267617243239504a98d90' │
    │       IncentivesV2-Implementation       │ '0x82e01223d51Eb87e16A03E24687EDF0F294da6f1' │
    │             IncentivesProxy             │ '0x0A41804810f008e5EE565aa4B95a6a7c50a09082' │
    │       PullRewardsTransferStrategy       │ '0xc351628EB244ec633d5f21fBD6621e1a683B1181' │
    │               AToken-Aave               │ '0xcbEAF3BDe82155F56486Fb5a1072cb8baAf547cc' │
    │       DelegationAwareAToken-Aave        │ '0xB0D4afd8879eD9F52b28595d31B441D079B2Ca07' │
    │          StableDebtToken-Aave           │ '0x922D6956C99E12DFeB3224DEA977D0939758A1Fe' │
    │         VariableDebtToken-Aave          │ '0x1fA02b2d6A771842690194Cf62D91bdd92BfE28d' │
    │ ReserveStrategy-rateStrategyVolatileOne │ '0x04C89607413713Ec9775E14b954286519d836FEf' │
    │  ReserveStrategy-rateStrategyStableOne  │ '0x4C4a2f8c81640e47606d3fd77B353E87Ba015584' │
    │  ReserveStrategy-rateStrategyStableTwo  │ '0x21dF544947ba3E8b3c32561399E88B52Dc8b2823' │
    │             DAI-AToken-Aave             │ '0x3E180b566A1ef3Ad836ee42c9519BE95B13e7473' │
    │       DAI-VariableDebtToken-Aave        │ '0xD5f384B615Da6db2E8BA839DfC04e2113dc103f3' │
    │        DAI-StableDebtToken-Aave         │ '0xCAdC1b73f0f225dD5BADB4245cbF7D9a4fFa9878' │
    │            LINK-AToken-Aave             │ '0xac782440070E7a23CCB04c539489ba42eD1c0e3a' │
    │       LINK-VariableDebtToken-Aave       │ '0xa385064DE8625ed6eDD2E18e288Dd5FaCa880aE1' │
    │        LINK-StableDebtToken-Aave        │ '0x82ebD3c18a91db4eC87d18FEdF392c1135937246' │
    │            USDC-AToken-Aave             │ '0x07AA7A1a1eAE23162130ac661Ef9D37868A6D91C' │
    │       USDC-VariableDebtToken-Aave       │ '0x0063Ca09768fb64BBFd0fFd12Ed6b036971c9b64' │
    │        USDC-StableDebtToken-Aave        │ '0xB9327CeC4641157ed56f688cB46f030d00229fCA' │
    │            WBTC-AToken-Aave             │ '0xb0A338eD2DAB8455ca83b4D71C64bf8E8868b2D5' │
    │       WBTC-VariableDebtToken-Aave       │ '0xa1F008dEf52E184f69366Bf653f590770dd49FF8' │
    │        WBTC-StableDebtToken-Aave        │ '0x0Bd497156d4F9Aa78076C89a52dCBC9277dDA565' │
    │            WETH-AToken-Aave             │ '0x26A011701ac2199398E1fd86901Fa950409867b0' │
    │       WETH-VariableDebtToken-Aave       │ '0x5042DDe6a13212aadFE8Ed62F0796CC0A0d45fcf' │
    │        WETH-StableDebtToken-Aave        │ '0xaE5dcf893737EFd2DD8348bE6990245172DE9EC7' │
    │            USDT-AToken-Aave             │ '0xc932ef01Bd75786bC4aE2fb312839d9a80d16bFA' │
    │       USDT-VariableDebtToken-Aave       │ '0x5122206f99dB1192990455B4D6649bcB56EB2Bb8' │
    │        USDT-StableDebtToken-Aave        │ '0xCB5C9fa2e9Ec3e01c17f01E7bB4994ceB2317868' │
    │            AAVE-AToken-Aave             │ '0x4d845bFA191a93412238104c7a7F5e5Ba08Eb45a' │
    │       AAVE-VariableDebtToken-Aave       │ '0xdc33934083dF198a70f2D722E4855D65aF27A0a5' │
    │        AAVE-StableDebtToken-Aave        │ '0x319f10e6273B93b0b8F0c95e6ebf21D91A8EfdA6' │
    │            EURS-AToken-Aave             │ '0xA6f2A783f5a818A92189b3D6Aa24Cba3ad47Be76' │
    │       EURS-VariableDebtToken-Aave       │ '0x605ceA931B42C1Cd387694D3720D11340a6CDfdf' │
    │        EURS-StableDebtToken-Aave        │ '0xe72eF9C6db7D89B63185c587e5a33d9F5a913c4F' │
    │          MockFlashLoanReceiver          │ '0x0355B7B8cb128fA5692729Ab3AAa199C1753f726' │
    │          WrappedTokenGatewayV3          │ '0x202CCe504e04bEd6fC0521238dDf04Bc9E8E15aB' │
    │          WalletBalanceProvider          │ '0xf4B146FbA71F41E0592668ffbF264F1D186b2Ca8' │
    └─────────────────────────────────────────┴──────────────────────────────────────────────┘

    Mintable Reserves and Rewards
    ┌────────────────────────────────┬──────────────────────────────────────────────┐
    │            (index)             │                   address                    │
    ├────────────────────────────────┼──────────────────────────────────────────────┤
    │ DAI-TestnetMintableERC20-Aave  │ '0x959922bE3CAee4b8Cd9a407cc3ac1C251C2007B1' │
    │ LINK-TestnetMintableERC20-Aave │ '0x9A9f2CCfdE556A7E9Ff0848998Aa4a0CFD8863AE' │
    │ USDC-TestnetMintableERC20-Aave │ '0x68B1D87F95878fE05B998F19b66F4baba5De1aed' │
    │ WBTC-TestnetMintableERC20-Aave │ '0x3Aa5ebB10DC797CAC828524e59A333d0A371443c' │
    │ WETH-TestnetMintableERC20-Aave │ '0xc6e7DF5E7b4f2A278906862b61205850344D4e7d' │
    │ USDT-TestnetMintableERC20-Aave │ '0x59b670e9fA9D0A427751Af201D676719a970857b' │
    │ AAVE-TestnetMintableERC20-Aave │ '0x4ed7c70F96B99c776995fB64377f0d4aB3B0e1C1' │
    │ EURS-TestnetMintableERC20-Aave │ '0x322813Fd9A801c5507c9de605d63CEA4f2CE6c44' │
    └────────────────────────────────┴──────────────────────────────────────────────┘

"""

import json
import logging
import os
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from shutil import which
from typing import Type

from eth_typing import ChecksumAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.abi import get_contract, get_linked_contract

logger = logging.getLogger(__name__)


#: What is the default location of our aave deployer script
#:
#: aave-v3-deploy is not packaged with eth_defi as they exist outside the Python package,
#: in git repository. However, it is only needed for tests and normal users should not need these files.
DEFAULT_REPO_PATH = Path(__file__).resolve().parents[2] / "contracts/aave-v3-deploy"


#: Default location of Aave v3 compiled ABI
DEFAULT_ABI_PATH = Path(__file__).resolve().parents[1] / "abi/aave_v3"


#: We maintain our forked and patched deployer
#:
#: (No patches yet)
AAVE_DEPLOYER_REPO = "https://github.com/tradingstrategy-ai/aave-v3-deploy.git"


#: List of manually parsed addressed from Hardhat deployment
#:
#:
HARDHAT_CONTRACTS = {
    # "PoolAdderssProvider": "0xa85233C63b9Ee964Add6F2cffe00Fd84eb32338f",
    # "PoolImplementation": "0xf5059a5D33d5853360D16C683c16e67980206f36",
    # PoolImplementation can't be used directly, we should interact with PoolProxy
    # this is the same as mainnet deployment
    "PoolProxy": "0x763e69d24a03c0c8B256e470D9fE9e0753504D07",
    "PoolDataProvider": "0x09635F643e140090A9A8Dcd712eD6285858ceBef",
    "PoolAddressProvider": "0xa85233C63b9Ee964Add6F2cffe00Fd84eb32338f",
    # https://github.com/aave/aave-v3-periphery/blob/1fdd23b38cc5b6c095687b3c635c4d761ff75c4c/contracts/mocks/testnet-helpers/Faucet.sol
    "Faucet": "0x0B306BF915C4d645ff596e518fAf3F9669b97016",
    # TestnetERC20 https://github.com/aave/aave-v3-periphery/blob/1fdd23b38cc5b6c095687b3c635c4d761ff75c4c/contracts/mocks/testnet-helpers/TestnetERC20.sol#L12
    "USDC": "0x68B1D87F95878fE05B998F19b66F4baba5De1aed",
    "WBTC": "0x3Aa5ebB10DC797CAC828524e59A333d0A371443c",
    "WETH": "0xc6e7DF5E7b4f2A278906862b61205850344D4e7d",
    "aUSDC": "0x07AA7A1a1eAE23162130ac661Ef9D37868A6D91C",
    "vWETH": "0x5042DDe6a13212aadFE8Ed62F0796CC0A0d45fcf",
    "AaveOracle": "0x36C02dA8a0983159322a80FFE9F24b1acfF8B570",
    "WETHAgg": "0x9E545E3C0baAB3E08CdfD552C960A1050f373042",
    "USDCAgg": "0xc3e53F4d16Ae77Db1c982e75a937B9f60FE63690",
}


class AaveDeployer:
    """Aave v3 deployer wrapper.

    - Install Aave v3 deployer locally

    - Run the deployment command against the local Anvil installation

    """

    def __init__(
        self,
        repo_path: Path = DEFAULT_REPO_PATH,
        abi_path: Path = DEFAULT_ABI_PATH,
    ):
        """Create Aave deployer.

        :param repo_path:
            Path to aave-v3-deploy git checkout.

        :param abi_path:
            Path to Aave v3 compiled ABI.
        """
        assert isinstance(repo_path, Path)
        assert isinstance(abi_path, Path)
        self.repo_path = repo_path
        self.abi_path = abi_path

    def is_checked_out(self) -> bool:
        """Check if we have a Github repo of the deployer."""
        return (self.repo_path / "package.json").exists()

    def is_installed(self) -> bool:
        """Check if we have a complete Aave deployer installation."""
        return (self.repo_path / "node_modules/.bin/hardhat").exists()

    def checkout(self, echo=False):
        """Clone aave-v3-deploy repo."""

        if echo:
            out = subprocess.STDOUT
        else:
            out = subprocess.DEVNULL

        logger.info("Checking out Aave deployer installation at %s", self.repo_path)
        git = which("git")
        assert git is not None, "No git command in path, needed for Aave v3 deployer installation"

        logger.info("Cloning %s", AAVE_DEPLOYER_REPO)
        result = subprocess.run(
            [git, "clone", AAVE_DEPLOYER_REPO, self.repo_path],
            stdout=out,
            stderr=out,
        )
        assert result.returncode == 0

        assert self.repo_path.exists()

    def install(self, echo=False) -> bool:
        """Make sure we have Aave deployer installed.

        .. note ::

            Running this function takes long time on the first attempt,
            as it downloads 1000+ NPM packages and few versions of Solidity compiler.

        - Aave v3 deployer is a NPM/Javascript package we need to checkout with `git clone`

        - We install it via NPM modules and run programmatically using subprocesses

        - If already installed do nothing

        :param echo:
            Mirror NPM output live  to stdout

        :return:
            False is already installed, True if we did perform the installation.
        """

        logger.info("Installing Aave deployer installation at %s", self.repo_path)

        npm = which("npm")
        assert npm is not None, "No npm command in path, needed for Aave v3 deployer installation"

        if self.is_installed():
            logger.info("aave-v3-deploy NPM installation already complete")
            return False

        assert self.is_checked_out(), f"{self.repo_path.absolute()} does not contain aave-v3-deploy checkout"

        if echo:
            out = None
        else:
            out = subprocess.DEVNULL

        logger.info("NPM install on %s - may take long time", self.repo_path)

        result = subprocess.run(
            [npm, "ci"],
            cwd=self.repo_path,
            stdout=out,
            stderr=out,
        )
        assert result.returncode == 0, f"npm install failed: {result.stderr and result.stderr.decode('utf-8')}"

        logger.info("Installation complete")
        return True

    def deploy_local(self, web3: Web3, echo=False):
        """Deploy Aave v3 at Anvil.

        Deploys all infrastructure mentioned in the :py:mod:`eth_defi.aave_v3.deployer` documentation,
        in those fixed addresses.

        .. note ::

            Currently Aave v3 deployer is hardcoded to deploy at localhost:8545
            Anvil cannot run in other ports.

        :param echo:
            Mirror NPM output live  to stdout
        """

        assert self.is_installed(), "Deployer not installed"

        assert not self.is_deployed(web3), "Already deployed on this chain"

        npx = which("npx")
        assert npx is not None, "No npx command in path, needed for Aave v3 deployment"

        if echo:
            out = None
        else:
            out = subprocess.PIPE

        logger.info("Running Aave deployer at %s", self.repo_path)

        env = os.environ.copy()
        env["MARKET_NAME"] = "Aave"

        result = subprocess.run(
            [npx, "hardhat", "--network", "localhost", "deploy", "--reset", "--export", "hardhat-deployment-export.json"],
            cwd=self.repo_path,
            env=env,
            stderr=out,
            stdout=out,
        )
        ret_text = result.stderr
        assert result.returncode == 0, f"Aave deployment failed:\n{ret_text}"

    def is_deployed(self, web3: Web3) -> bool:
        """Check if Aave is deployed on chain"""
        # assert web3.eth.block_number > 1, "This chain does not contain any data"
        try:
            usdc = self.get_contract_at_address(web3, "MintableERC20.json", "USDC")
            return usdc.functions.symbol().call() == "USDC"
        except Exception as e:
            print(e)
            return False

    def get_contract(self, web3: Web3, name: str) -> Type[Contract]:
        """Get Aave deployer ABI file.

        ABI files contain hardcoded library addresses from the deployment
        and cannot be reused.

        This function links the contract against other deployed contracts.

        See :py:meth:`get_contract_at_address`.

        :return:
            A Contract proxy class
        """
        path = self.abi_path / name
        assert path.exists(), f"No ABI file at: {path.absolute()}"
        # return get_linked_contract(web3, path, get_aave_hardhard_export())
        return get_contract(web3, path)

    def get_contract_address(self, contract_name: str) -> ChecksumAddress:
        """Get a deployed contract address.

        See :py:data:`HARDHAT_CONTRACTS` for the list.

        See :py:meth:`get_contract_at_address`.
        """
        assert contract_name in HARDHAT_CONTRACTS, f"Does not know Aave contract {contract_name}"
        return Web3.to_checksum_address(HARDHAT_CONTRACTS[contract_name])

    def get_contract_at_address(self, web3: Web3, contract_fname: str, address_name: str) -> Contract:
        """Get a singleton Aave deployed contract.

        Example:

        .. code-block:: python

            pool = aave_deployer.get_contract_at_address(web3, "Pool.json", "Pool")
            assert pool.functions.POOL_REVISION().call() == 1

        """
        address = self.get_contract_address(address_name)
        ContractProxy = self.get_contract(web3, contract_fname)
        instance = ContractProxy(address)
        return instance


@lru_cache(maxsize=1)
def get_aave_hardhard_export() -> dict:
    """Read the bunled hardhad localhost deployment export.

    Precompiled hardhat for a localhost deployment.
    Needed to deploy any contracts that contain linked libraries.

    See :py:func:`eth_defi.abi.get_linked_contract`.
    """
    hardhat_export_path = Path(__file__).resolve().parent / "aave-hardhat-localhost-export.json"
    return json.loads(hardhat_export_path.read_bytes())


def install_aave_for_testing():
    """Entry-point to ensure Aave dev env is installedon Github Actions.

    Because pytest-xdist does not have very good support for preventing
    race conditions with fixtures, we run this problematic test
    before test suite.

    It will do npm install for Aave deployer.
    """
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    logging.info("Preparing to install Aave dev env")
    deployer = AaveDeployer()
    deployer.install(echo=True)
