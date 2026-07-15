Kiln API
--------

Kiln OmniVault support is provided through the shared ERC-4626 classification API. The
``additionalRewardsStrategy()`` protocol probe identifies Kiln vaults and
:py:func:`eth_defi.erc_4626.core.get_vault_protocol_name` returns ``"Kiln"``. The
:py:class:`eth_defi.erc_4626.vault_protocol.kiln.vault.KilnVault` adapter reads the fixed deposit
fee and percentage reward fee from the vault contract.

Kiln vaults use the certified shared :py:class:`eth_defi.erc_4626.deposit_redeem.ERC4626DepositManager`
for synchronous deposits and redemptions.

See :doc:`the Kiln vault documentation </vaults/kiln/index>` for product and contract references.
