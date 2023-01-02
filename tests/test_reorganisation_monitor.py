"""Test chain reorganisation monitor."""

from eth_defi.event_reader.reorganisation_monitor import MockChainAndReorganisationMonitor


def test_synthetic_block_mon_produce_blocks():
    """Create mocked blocks."""
    mock_reorg_mon = MockChainAndReorganisationMonitor()
    assert mock_reorg_mon.get_last_block_live() == 0
    assert mock_reorg_mon.get_last_block_read() == 0
    mock_reorg_mon.produce_blocks()


def test_synthetic_block_mon_find_reorgs():
    """There are never reorgs."""
    mock_reorg_mon = MockChainAndReorganisationMonitor()
    mock_reorg_mon.produce_blocks()
    mock_reorg_mon.figure_reorganisation_and_new_blocks()
    assert mock_reorg_mon.get_last_block_live() == 1
    assert mock_reorg_mon.get_last_block_read() == 1


def test_synthetic_block_mon_find_reorgs_100_blocks():
    """There are never reorgs in longer mock chain."""
    mock_reorg_mon = MockChainAndReorganisationMonitor()
    mock_reorg_mon.produce_blocks(100)
    mock_reorg_mon.figure_reorganisation_and_new_blocks()


def test_perform_chain_reorg():
    """Simulate a chain reorganisation."""

    mock_chain = MockChainAndReorganisationMonitor(check_depth=100)

    mock_chain.produce_blocks(100)
    assert mock_chain.get_last_block_live() == 100

    reorg_resolution = mock_chain.update_chain()
    assert reorg_resolution.last_live_block == 100
    assert not reorg_resolution.reorg_detected

    # Trigger reorg by creating a changed block in the chain
    mock_chain.produce_fork(70)

    mock_chain.produce_blocks(2)
    assert mock_chain.get_last_block_live() == 102
    assert mock_chain.get_last_block_read() == 100

    # This will do 100 blocks deep reorg check
    reorg_resolution = mock_chain.update_chain()

    assert reorg_resolution.reorg_detected
    assert reorg_resolution.latest_block_with_good_data == 69
    assert reorg_resolution.last_live_block == 102

    assert mock_chain.get_last_block_live() == 102
    assert mock_chain.get_last_block_read() == 102


def test_incremental():
    """Simulate incremental 1 block updates."""

    mock_chain = MockChainAndReorganisationMonitor()

    mock_chain.produce_blocks(100)
    assert mock_chain.get_last_block_live() == 100
    reorg_resolution = mock_chain.update_chain()
    assert reorg_resolution.last_live_block == 100
    assert not reorg_resolution.reorg_detected

    mock_chain.produce_blocks(1)
    mock_chain.update_chain()

    mock_chain.produce_blocks(1)
    mock_chain.update_chain()

    mock_chain.produce_blocks(1)
    mock_chain.update_chain()

    reorg_resolution = mock_chain.update_chain()
    assert reorg_resolution.last_live_block == 103

    # Trigger reorg by creating a changed block in the chain
    mock_chain.produce_fork(103)
    mock_chain.produce_blocks(1)
    reorg_resolution = mock_chain.update_chain()
    assert reorg_resolution.reorg_detected
    assert reorg_resolution.latest_block_with_good_data == 102
    assert reorg_resolution.last_live_block == 104
