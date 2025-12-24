import os

from eth_account import Account
from eth_account.signers.local import LocalAccount
from web3.auto import w3

from eth_defi.compat import construct_sign_and_send_raw_middleware

private_key = os.environ.get("PRIVATE_KEY")
assert private_key is not None, "You must set PRIVATE_KEY environment variable"
assert private_key.startswith("0x"), "Private key must start with 0x hex prefix"

account: LocalAccount = Account.from_key(private_key)
w3.middleware_onion.add(construct_sign_and_send_raw_middleware(account))

print(f"Your hot wallet account is {account.address}")
