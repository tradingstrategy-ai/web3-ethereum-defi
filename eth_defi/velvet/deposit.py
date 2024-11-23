"""Velvet deposit handling.

- Need to call proprietary centralised API to make a deposit
"""
from eth_typing import HexAddress


def deposit_to_velvet(
    portfolio: HexAddress | str,
    from_address: HexAddress | str,
    deposit_token_address: HexAddress | str,
    amount: int,
) -> dict:
    """Construct Velvet deposit payload.

    - See https://github.com/Velvet-Capital/3rd-party-integration/issues/2#issuecomment-2490845963 for details
    """
    assert portfolio.startswith("0x")
    assert from_address.startswith("0x")
    assert deposit_token_address.startswith("0x")
    assert type(amount) == int
    # payload = {
    #     "portfolio": "0x444ef5b66f3dc7f3d36fe607f84fcb2f3a666902",
    #     "depositAmount": 1,
    #     "depositToken": "0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb",
    #     "user": "0x3C96e2Fc58332746fbBAB5eC44f01572F99033ed",
    #     "depositType": "batch",
    #     "tokenType": "erc20"
    # }

    payload = {
        "portfolio": portfolio,
        "depositAmount": amount,
        "depositToken": deposit_token_address,
        "user": from_address,
        "depositType": "batch",
        "tokenType": "erc20"
    }





