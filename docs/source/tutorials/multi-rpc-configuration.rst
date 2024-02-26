.. meta::
   :description: How to use MEV blocker and fallbacks for EVM JSON-RPC in Python

.. _multi rpc:

MEV protection and multiple JSON-RPCs configuration
===================================================

`Web-Ethereum-Defi package <https://github.com/tradingstrategy-ai/web3-ethereum-defi>`__ supports creating :py:class:`eth_defi.provider.multi_provider.MultiProviderWeb3`
subclass instances of :py:class:`web3.Web3` core connector.

These instances support multiple JSON-RPC providers, mainly

- Gracefully deal and retry JSON-RPC errors, even on a single RPC provider.
  If multiple providers are provided, then recover if the provider goes down for a longer period of time.

- Automatic fallback to another JSON-RPC provider when one fails, also known as
  "hot spare" or "hot switch" strategy in DevOps

- Using `Malicious Extractable Value (MEV) protection <https://tradingstrategy.ai/glossary/mev>`__
  with a special endpoints when broadcasting a transaction to avoind being
  frontrun, sandwich attacked and such

- See also :py:class:`eth_defi.provider.fallback.FallbackProvider` and
  :py:class:`eth_defi.provider.mev_blocker.MEVBlockerProvider` for specialised
  :py:class:`web3.providers.rpc.HTTPProvider` subclasses

- The method is compatible with Ethereum, but also with other `EVM compatible <https://tradingstrategy.ai/glossary/evm-compatible>`__ blockchains
  like Polygon, Binance Smart Chain, Arbitrum, Avalanche C-Chain

- For some of JSON-RPC providers that provide private mempool, MEV blocking and backrunning services,
  see `MEV Blocker <https://mevblocker.io/>`__ by Cowswap, others. Alternatives include e.g.
  `Blocknative <https://docs.blocknative.com/blocknative-mev-protection/blocknative-protect-rpc-endpoint>`__.

- For list of JSON-RPC node providers please see `EthereumNodes.com <https://ethereumnodes.com>`__

Configuring
-----------

The multi RPC endpoints can be created with :py:func:`eth_defi.provider.multi_provider.create_multi_provider_web3`.

Instead of giving a single RPC endpoint URL, you give a list URLs.

- List can be newline separated or space separated

- For MEV protection endpoint, you prefix your URL with `mev+` -
  this endpoint is always used for `eth_sendTransaction` and `eth_sendRawTransction`
  JSON-RPC endpoints

Example:

.. code-block:: python

    from eth_defi.provider.multi_provider import MultiProviderWeb3
    from eth_defi.provider.multi_provider import create_multi_provider_web3


    # Uses MEVblocker.io to broadcast transactions
    # and two separate nodes for reading blockchain data
    config = "mev+https://rpc.mevblocker.io https://myethereumnode1.example.com https://fallback.example.com"
    web3: MultiProviderWeb3 = create_multi_provider_web3(config)

    # If one provider fails, MultiProviderWeb3 will switch to the next one
    print(f"Currently active call endpoint for JSON-RPC is {web3.get_active_call_provider()}")

 You can also have it new-line separated for readability:

.. code-block:: python

    config = """
        mev+https://rpc.mevblocker.io
        https://myethereumnode1.example.com
        https://fallback.example.com
        """

If you want to keep using a single RPC endpoint, you do not need to do any changes:

.. code-block:: python

    # Pasing a single RPC endpoint URL is ok
    web3 = create_multi_provider_web3("https://polygon-rpc.com")

Because JSON-RPC provider URLs contains API keys the preferred way to pass them around
is using environment variables.

In your UNIX shell:

.. code-block:: shell

    # Passing single provider: This URL may contain API key
    export JSON_RPC_POLYGON=https://polygon-rpc.com/

    # Passing multiple providers: These URLs may contain API key
    export JSON_RPC_BINANCE=https://bsc-rpc.gateway.pokt.network/ https://bsc-dataseed.bnbchain.org https://bsc.nodereal.io

And then:

.. code-block:: python

    import os
    from eth_defi.provider.multi_provider import create_multi_provider_web3

    web3 = create_multi_provider_web3(os.environ["JSON_RPC_POLYGON"])
