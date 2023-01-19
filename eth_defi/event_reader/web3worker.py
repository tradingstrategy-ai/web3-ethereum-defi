"""Web3 thread pool worker helpers."""

import logging
import threading
from typing import Optional

import futureproof
from web3 import Web3

from eth_defi.event_reader.logresult import LogContext
from eth_defi.event_reader.web3factory import Web3Factory


logger = logging.getLogger(__name__)


_thread_local_storage = threading.local()


def get_worker_web3() -> Web3:
    """Get the Web3 connection for the worker.

    The connection was initialized when the worker thread was created.
    """
    return _thread_local_storage.web3


def create_thread_pool_executor(factory: Web3Factory, context: Optional[LogContext] = None, max_workers=16) -> futureproof.ThreadPoolExecutor:
    """Create a thread pool executor.

    All pool members have the thread locals initialized at start,
    so that there is Web3 connection available.

    :param factory:
        The factory that provides web3 connection
        for each threaad after a thread has been launched.

    :param context:
        Event reader context.

        If you want to pass something extra for the event reader.

    :param max_workers:
        How many threads are allocated for futureproof pool.
    """

    def init():
        _thread_local_storage.web3 = factory(context)
        logger.debug("Worker thread %d initialized", threading.get_ident())

    executor = futureproof.ThreadPoolExecutor(max_workers=max_workers, initializer=init)

    return executor
