from web3 import EthereumTesterProvider, Web3

from eth_defi.event_reader.block_time import measure_block_time


def test_measure_block_time():
    """Measure block time."""

    tester_provider = EthereumTesterProvider()
    tester = tester_provider.ethereum_tester

    web3 = Web3(tester_provider)

    tester.mine_blocks(100)

    time = measure_block_time(web3)
    assert time == 1.0  # EthereumTesterProvider ticks 1 sec / block
