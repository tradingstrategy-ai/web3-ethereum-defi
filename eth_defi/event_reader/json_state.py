"""Serialise event scan state as a JSON file."""
import os
from typing import Tuple

from eth_defi.event_reader.state import ScanState


class JSONFileScanState(ScanState):
    """Save and resume block event scan using state serialised in JSON file."""

    def __init__(self, fname: str):
        """
        :param fname: In which file we store the last processed block number.
        """
        self.fname = fname

    def save_state(self, last_block):
        """Saves the last block we have read."""
        with open(self.fname, "wt", encoding="utf-8") as f:
            print(f"{last_block}", file=f)

    def restore_state(self, default_block: int) -> Tuple[bool, int]:
        """Restore the last block we have processes.

        :return:
            Tuple (did we restore state, the first block numebr to scan)
        """
        if os.path.exists(self.fname):
            with open(self.fname, "rt", encoding="utf-8") as f:
                last_block_text = f.read()
                return True, int(last_block_text)

        return False, default_block
