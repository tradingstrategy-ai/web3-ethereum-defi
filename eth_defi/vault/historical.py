"""Read historical state of vaults.

- Use multicall to get data points for multiple vaults once
- Include
    - Share price
    - TVL
    - Fees

See :py:class:`VaultHistoricalReadMulticaller` for usage.
"""
import logging
from collections import defaultdict

from typing import Iterable

from eth_typing import HexAddress

from eth_defi.event_reader.multicall_batcher import EncodedCall, read_multicall_historical, EncodedCallResult
from eth_defi.event_reader.web3factory import Web3Factory
from eth_defi.token import TokenDetails
from eth_defi.vault.base import VaultBase, VaultHistoricalReader, VaultHistoricalRead


logger = logging.getLogger(__name__)


class VaultReadNotSupported(Exception):
    """Vault cannot be read due to misconfiguration somewhere."""


class VaultHistoricalReadMulticaller:
    """Read historical data from multiple vaults using multicall and archive node polling."""

    def __init__(
        self,
        web3factory: Web3Factory,
        supported_quote_tokens=set[TokenDetails],
    ):
        for a in supported_quote_tokens:
            assert isinstance(a, TokenDetails)

        self.supported_quote_tokens = supported_quote_tokens
        self.web3factory = web3factory

    def validate_vaults(
        self,
        vaults: list[VaultBase],
    ):
        """Check that we can read these vaults.

        - Validate that we know how to read vaults

        :raise VaultReadNotSupported:
            In the case we cannot read some of the vaults
        """
        for vault in vaults:
            denomination_token = vault.denomination_token
            if denomination_token not in self.supported_quote_tokens:
                raise VaultReadNotSupported(f"Vault {vault} has denomination token {denomination_token} which is not supported denomination token set: {self.supported_quote_tokens}")

    def prepare_readers(self, vaults: list[VaultBase]) -> dict[HexAddress, VaultHistoricalReader]:
        """Create readrs for vaults."""
        readers = {}
        for vault in vaults:
            assert not vault.address in readers, f"Vault twice: {vault}"
            readers[vault.address] = vault.get_historical_reader()
        return readers

    def generate_vault_historical_calls(
        self,
        readers: dict[HexAddress, VaultHistoricalReader],
    ) -> Iterable[EncodedCall]:
        """Generate multicalls for each vault to read its state at any block."""
        for reader in readers.values():
            yield from reader.construct_multicalls()

    def read_historical(
        self,
        vaults: list[VaultBase],
        start_block: int,
        end_block: int,
        step: int,
    ) -> Iterable[VaultHistoricalRead]:
        """Create an iterable that extracts vault record from RPC.

        :return:
            Unordered results
        """
        readers = self.prepare_readers(vaults)
        calls = self.generate_vault_historical_calls(readers)
        for combined_result in read_multicall_historical(
            web3factory=self.web3factory,
            calls=calls,
            start_block=start_block,
            end_block=end_block,
            step=step,
            ):

            # Transform single multicall call results to calls batched by vault-results
            block_number = combined_result.block_number
            timestamp = combined_result.timestamp
            vault_data: dict[HexAddress, list[EncodedCallResult]] = defaultdict(list)
            for call_result in combined_result.results:
                vault: HexAddress = call_result.call.extra_data["vault"]
                vault_data[vault].append(call_result)

            for vault_address, results in vault_data.items():
                reader = readers[vault_address]
                yield reader.process_result(block_number, timestamp, results)




