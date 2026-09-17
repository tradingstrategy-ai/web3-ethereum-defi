"""Handwritten notes for specific Yearn vaults."""

#: Yearn BOLD receipt-token vault on Ethereum.
YBOLD_VAULT = "0x9f4330700a36b29952869fac9b33f45eedd8a3d8"

#: Yearn yBOLD Auto-Compounder vault on Ethereum.
YSYBOLD_VAULT = "0x23346b04a7f55b8760e5860aa5a77383d63491cd"

#: Address-keyed notes for Yearn vaults.
YEARN_VAULT_NOTES: dict[str, str] = {
    YBOLD_VAULT: (f"yBOLD is a 1:1 BOLD receipt token and does not accrue yield. Stake yBOLD in [ysyBOLD](https://yearn.fi/vaults/1/{YSYBOLD_VAULT}) to earn compounded yield."),
}
