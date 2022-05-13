"""Web3 thread pool worker helpers."""

import logging
import threading
from typing import Iterator

import futureproof
from futureproof.task_manager import Task, TaskManager
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


def create_thread_pool_executor(factory: Web3Factory, context: LogContext, max_workers=16) -> futureproof.ThreadPoolExecutor:
    """Create a thread pool executor.

    All pool members have the thread locals initialized at start,
    so that there is Web3 connection available.
    """

    def init():
        _thread_local_storage.web3 = factory(context)
        logger.info("Worker thread %d initialized", threading.get_ident())

    executor = futureproof.ThreadPoolExecutor(max_workers=max_workers, initializer=init)

    return executor


def complete_tasks(self: TaskManager) -> Iterator[Task]:
    """Start the manager and return an iterator of completed tasks.

    When using the task manager as a context manager as_completed must be used
    *inside* the context, otherwise there will be no effect as the task manager
    will wait until all tasks are completed.
    """
    logger.info("Starting to process tasks with %d workers", self._executor.max_workers)
    for task in self._tasks:
        if self._shutdown:
            break

        logger.info("Submit task, queue is %d", self._tasks_in_queue)
        if self._tasks_in_queue == self._executor.max_workers:
            print("Queue full, waiting for result")
            yield self._wait_for_result()

        self._submit_task(task)

    print("hophop")
    while len(self.completed_tasks) < self._submitted_task_count:
        print("Not yet")
        yield self._wait_for_result()

    self._executor.join()