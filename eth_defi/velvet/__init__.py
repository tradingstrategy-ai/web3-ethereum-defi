import requests

from eth_defi.vault.base import VaultBase, VaultInfo


class VelvetVaultInfo(VaultInfo):
    pass



class VelvetVault(VaultBase):

    def __init__(
        self,
        vault_address: str,
        api_url: str,
    ):
        self.api_url = api_url
        self.session = requests.Session()

        assert vault_address.startswith('0x')
        self.vault_address = vault_address

    def has_block_range_event_support(self):
        return False



    def _make_api_requesT(
        self,
        endpoint: str,
        params: dict,
    ):
        pass


