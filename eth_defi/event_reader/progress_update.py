"""Different reporters for event reading progress."""
from eth_defi.event_reader.reader import ProgressUpdate


class PrintProgressUpdate(ProgressUpdate):
    """Print to stdout the read progress."""

    def __call__(
        self,
        current_block: int,
        start_block: int,
        end_block: int,
        chunk_size: int,
        total_events: int,
        last_timestamp: int,
        context,
    ):
        done = (current_block - start_block) / (end_block - start_block)
        print(f"Scanning blocks {current_block:,} - {current_block + chunk_size:,}, done {done * 100:.1f}%")
