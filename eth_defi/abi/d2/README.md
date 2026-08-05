# D2 Finance ABI sources

``VaultV1Whitelisted.json`` is the verified interface for D2's
``VaultV1Whitelisted`` deployment, including its historical
``onlyWhitelisted`` deposit modifier and the ``whitelisted``,
``whitelistAsset`` and ``whitelistBalance`` reads. The canonical verified
implementation is
[0x350856A672e7bF7D7327c8a5e72Ac49833DBfB75 on Arbiscan](https://arbiscan.io/address/0x350856A672e7bF7D7327c8a5e72Ac49833DBfB75#code).

The contract terminology does not indicate a KYC or manual identity gate. Its
mapping and token-balance checks are D2 eligibility conditions, so the public
``deposit_permission`` status is ``permissionless``. Epoch timing, open dates,
lock-ups and deposit caps likewise do not alter this KYC classification.
