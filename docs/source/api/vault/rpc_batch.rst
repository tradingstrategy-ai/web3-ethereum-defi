Rpc batch
=========

``prepare_metadata_batch()`` shares fixed-block core, paused and fee inputs,
warms token metadata and fetches eligible idle balances. Consumers validate
source identity before reusing these ephemeral observations.

.. automodule:: eth_defi.vault.rpc_batch
   :members:

The shared regular payload default is 100 encoded subcalls. Scanner
``MULTICALL_BATCH_SIZE`` overrides propagate through preparation, admission,
classification dispatch and token warmup; known constrained-chain caps still apply.
