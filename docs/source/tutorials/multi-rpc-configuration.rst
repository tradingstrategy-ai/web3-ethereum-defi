.. _multi rpc:

Configuring multiple RPC endpoints
==================================

The package supports creating :py:class:`eth_defi.provider.multi_provider.MultiProviderWeb3`
instances of :py:class:`Web3` core connector.

These instances support multiple JSON-RPC providers, mainly

- Automatic fallback to another JSON-RPC provider when one fails

- Using `Malicious Extractable Value protection <https://tradingstrategy.ai/glossary/mev>`__
  with a special endpoints when broadcasting a transaction

Configuring
-----------

The multi RPC endpoints can be created with :py:func:`eth_defi.provider.multi_provider.create_multi_provider_web3`.

Instead of giving a single RPC endpoint URL, you give a list URLs.

- List can be newline separated or space separated

- For MEV protection endpoint, you prefix your URL with `mev+`

Example:

.. code-block::

    # Uses MEVblocker.io to broadcast transactions
    # and two separate nodes for reading blockchain data
    config = "mev+https://rpc.mevblocker.io https://myethereumnode1.example.com https://fallback.example.com"
    web3 = create_multi_provider_web3(config)


