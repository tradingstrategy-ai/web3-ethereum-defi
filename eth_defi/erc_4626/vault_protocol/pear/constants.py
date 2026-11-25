"""Reviewed Pear deployment identifiers."""

PEAR_CHAIN_ID = 999

#: Raw price source markers accepted for a verified Pear time series.
PEAR_PRICE_SOURCES = frozenset({"latest", "pear_api"})

#: Factory recorded in the live vaults' creation receipts and factory() getters.
PEAR_VAULT_FACTORY = "0x038b9e0eef0f926682d3a9b1918611efec9cc741"

#: First standard Deposit blocks of the funded deployments reviewed at onboarding.
PEAR_FIRST_DEPOSIT_BLOCKS = {
    "0x65c9f4665c7d9ce96f53aafc67796dcad46f4096": 45_896_885,
    "0xd38a67a4f54a9b4286d72f08440485a395ddfa70": 46_593_274,
    "0xdf6917ee9a2d9b34075bb099a0a1e955bcfcdf84": 50_373_141,
    "0x69f2ed780a4cec49e552dbcd35a60f41368bc362": 50_802_226,
}

#: Vault operators identified by Pear's catalogue and the individual mandates.
#: https://vaults.pear.garden/
PEAR_CURATORS = {
    "0x65c9f4665c7d9ce96f53aafc67796dcad46f4096": "pear",
    "0xd38a67a4f54a9b4286d72f08440485a395ddfa70": "emporium-ventures",
    "0xdf6917ee9a2d9b34075bb099a0a1e955bcfcdf84": "valkyrie-finance",
    "0x69f2ed780a4cec49e552dbcd35a60f41368bc362": "moritz-pike",
}
