"""Different reporters for event reading progress.

- Stdout printing

- TQDM progress bars

- Python logging based notifications

"""

import datetime

from tqdm_loggable.auto import tqdm

from eth_defi.compat import native_datetime_utc_fromtimestamp
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


class TQDMProgressUpdate(ProgressUpdate):
    """Use TQDM progress bars to display the progress.

    - Works in console

    - Works in Jupyter Notebook with HTML progress bars

    - Can be set to loggable output for headless process

    You need to have `tqdm-loggable module installed <https://github.com/tradingstrategy-ai/tqdm-loggable>`__.

    `See more info <https://github.com/tradingstrategy-ai/tqdm-loggable>`__.

    Example:

    .. code-block:: python

        reader = MultithreadEventReader(
            provider.endpoint_uri,
            max_threads=16,
            notify=TQDMProgressUpdate("Scanning Enzyme Asset List"),
            max_blocks_once=10_000,
            reorg_mon=None,
        )

        logger.info(f"Scanning for Enzyme price feed events {start_block:,} - {end_block:,}")

        feeds = fetch_updated_price_feed(
            deployment,
            start_block=start_block,
            end_block=end_block,
            read_events=reader,
        )

        reader.close()
    """

    def __init__(self, name: str, colour="green"):
        """

        :param name:
            Progress bar label

        :param colour:
            Used in Jupyter notebooks

        """
        self.name = name
        self.colour = colour
        self.progress_bar = None

    def create_progress_bar(self, start_block, end_block):
        blocks = end_block - start_block
        progress_bar = tqdm(total=blocks, colour=self.colour)
        return progress_bar

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
        if self.progress_bar is None:
            self.progress_bar = self.create_progress_bar(start_block, end_block)

        if last_timestamp:
            friendly_time = native_datetime_utc_fromtimestamp(last_timestamp).strftime("%Y-%m-%d %H:%M")
        else:
            friendly_time = "NA"

        self.progress_bar.set_description(f"{self.name}, {start_block:,} - {end_block:,}, {chunk_size:,} block per request, last block time {friendly_time}")

        self.progress_bar.update(chunk_size)

        self.progress_bar.set_postfix(
            {
                "Events found": total_events,
            }
        )

    def close(self):
        self.progress_bar.close()
