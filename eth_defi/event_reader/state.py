import abc
from typing import Tuple


class ScanState(abc.ABC):
    """Scan state resume interface.

    Save and load scan state somewhere, so we do not need to start scan from the scratch
    on abort.
    """

    @abc.abstractmethod
    def save_state(self, last_block):
        """Saves the last block we have read."""

    @abc.abstractmethod
    def restore_state(self, default_block: int) -> Tuple[bool, int]:
        """Restore the last block we have processes.

        :return:
            Tuple (did we restore state, the first block numebr to scan)
        """
