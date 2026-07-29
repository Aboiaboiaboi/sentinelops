"""Job queue abstraction.

One of exactly two modules permitted to know about a cloud or broker SDK — the
other is storage.py. Nothing in services/ or scanners/ may import a client
directly. That boundary, not the choice of hosting product, is what makes the
rest of the codebase portable: swapping Redis for SQS is a change here and
nowhere else.

Two implementations: `ArqQueue` for real use, and `InMemoryQueue` for tests and
for any environment with no broker.
"""

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from arq import ArqRedis


@runtime_checkable
class Queue(Protocol):
    """What the rest of the application is allowed to assume about a queue."""

    async def publish(self, task: str, /, **payload: Any) -> str:
        """Enqueue a task by name, returning an opaque job id.

        The id is for logging and correlation only. Callers must not parse it —
        every backend formats it differently.
        """
        ...


@dataclass
class InMemoryQueue:
    """Records published jobs without running anything.

    Used in development and tests. Keeping published jobs inspectable means a
    test can assert that requesting a scan enqueued work, without a broker
    running and without waiting on one.
    """

    published: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def publish(self, task: str, /, **payload: Any) -> str:
        self.published.append((task, payload))
        return f"mem-{uuid.uuid4()}"

    def clear(self) -> None:
        self.published.clear()


class ArqQueue:
    """Publishes to Redis for the arq worker to consume."""

    def __init__(self, pool: "ArqRedis") -> None:
        self._pool = pool

    async def publish(self, task: str, /, **payload: Any) -> str:
        """Enqueue a task.

        `_job_id` in the payload is arq's deduplication key rather than ordinary
        task data — enqueueing an id that is already queued returns None instead
        of creating a second job. That is the desired behaviour, not a failure,
        so the existing id is returned and the caller cannot tell the difference.
        """
        job = await self._pool.enqueue_job(task, **payload)
        if job is not None:
            return job.job_id

        existing = payload.get("_job_id")
        if isinstance(existing, str):
            return existing
        # arq only declines an enqueue because of a duplicate id, so reaching
        # here means it declined one we never supplied.
        raise RuntimeError(f"Queue refused task {task!r} and no job id was supplied")


_queue: Queue = InMemoryQueue()


def get_queue() -> Queue:
    return _queue


def set_queue(queue: Queue) -> None:
    """Swap the implementation. Called at startup once a broker is configured,
    and by tests that need a clean queue."""
    global _queue
    _queue = queue
