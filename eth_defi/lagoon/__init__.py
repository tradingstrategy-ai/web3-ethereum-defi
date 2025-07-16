"""Lagoon vault protocol integration.

Lagoon v0.5.0 changes to the original release

- Affect the vault interactions greatlty
- Vault initialisation parameters changed: fee registry and wrapped native token moved from parameters payload to constructor arguments
- Beacon proxy replaced with BeaconProxyFactory.createVault() patterns
- ``pendingSilo()`` accessor removed, now needs a direct storage slot read
- ``safe()`` accessor added

How to detect version:

- Call pendingSilo(): if reverts is a new version

How to get ``pendingSilo()``: see :py:meth:`eth_defi.lagoon.vault.LagoonVault.silo_address`.
"""
