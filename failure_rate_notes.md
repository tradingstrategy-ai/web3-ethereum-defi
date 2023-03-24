# JSON-RPC provider failure rate notes

Pokt / Ethereum: found 977 uncertain/failed blocks out of 1,000 with the failure rate of 97.7%

## Pokt

## Ethereum mainnet

```
Finished, found 493 uncertain/failed blocks out of 1,000 with the failure rate of 49.3%

Double check uncertain blocks manually and with a block explorer:
    Block 260,309 - could not fetch transaction data for transaction 0x992300f61a013e905fe21a974698a3ea6968912b884eeddf1b508551145bfc82
    Block 467,028 - could not fetch transaction data for transaction 0xa4ef7efc0c74c6787ef1398cfd0835f65079c9c556e7e23cec7fc2930ba4cc56
    Block 642,020 - block missing
    Block 182,590 - could not fetch transaction data for transaction 0xbd65615e921e939908b23c27576cec0f2ab3c5be756d5ca4ba25fc8329137b60
    Block 385,477 - could not fetch transaction data for transaction 0x191edcf946795440aa29f6f5e3d70db6e0ca2c98fadfa940804c80f4fd3f2768
    Block 1,002,973 - could not fetch transaction data for transaction 0x3d67049d0b2b7fdab29c75245539d8600ceaf3f1937b45ae70159f40310bd001
    Block 956,549 - could not fetch transaction data for transaction 0x50e1a4ed9c7e22a5fe179055b7fc6df660bc097f384097e2517e4214fc8a3c65
    Block 1,148,068 - could not fetch transaction data for transaction 0xd0b4a1db95d639fd44980dfec67a62f75294f581b2ea2408932c4d7935a80ca4
    Block 679,438 - could not fetch transaction data for transaction 0xfd7046d7efaf7c6764d4121c627e740ed89a31613d29bf1dbab6a71db1ccd507
    Block 720,556 - could not fetch transaction data for transaction 0xdb2c52393042ff33b564d7d921efb621de194c95aa3fdf72a39c818b9985b39e
    Block 1,052,632 - could not fetch transaction data for transaction 0x337347f98ff83fbe872d324cae06f14463671ae15906b0013670cd53b1a99496
    Block 1,166,204 - could not fetch transaction data for transaction 0x98a588e9779717161d3abb9ec21e5233264a3bd565f947dc6b61144492bca10a
    Block 621,221 - could not fetch transaction data for transaction 0xa1d2f3f5647b39257c611fc5778eefbc6d978acfdcdd067d71ed5a40810cd380

```

## BNB Smart Cain

Pokt / BNB Chain: found 977 uncertain/failed blocks out of 1,000 with the failure rate of 97.7%

```
Finished, found 984 uncertain/failed blocks out of 1,000 with the failure rate of 98.4%
Double check uncertain blocks manually and with a block explorer:
    Block 17,384 - block missing
    Block 210,722 - block missing
    Block 92,142 - block missing
    Block 189,919 - block missing
    Block 318,397 - block missing
    Block 326,312 - block missing
    Block 325,373 - block missing
    Block 4,711,128 - could not fetch transaction receipt for transaction 0xbf822bffa3c746bd8166426cc4de4c866e21109a017250d2468c2c072e325e9e
    Block 5,368,886 - block missing
    Block 4,911,197 - could not fetch transaction data for transaction 0x94f5ab6eef1335c1b7f3e97c64279e1408e5e00e1c00b2043d367b3173d0ef9f
```

### Ethereum mainnet archival

```shell
START_BLOCK=4000000 CHECK_COUNT=1000 python scripts/verify-node-integrity.py
```

```
Finished, found 9 uncertain/failed blocks out of 1,000 with the failure rate of 0.9%
```

### BNB Smart Chain Archival

```
Finished, found 2 uncertain/failed blocks out of 1,000 with the failure rate of 0.2%
```

