"""Job queue abstraction.

One of exactly two modules permitted to know about a cloud or broker SDK — the
other is storage.py. Nothing in services/ or scanners/ may import a client
directly. That boundary, not the choice of hosting product, is what makes the
rest of the codebase portable: swapping Redis for SQS is a change here and
nowhere else.

The Redis/arq implementation lands with the scanning engine. Publishing to a
queue that nothing consumes would be worse than not publishing — it would look
like work was scheduled when no worker exists to run it.
"""

import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


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


_queue: Queue = InMemoryQueue()


def get_queue() -> Queue:
    return _queue


def set_queue(queue: Queue) -> None:
    """Swap the implementation. Called at startup once a broker is configured,
    and by tests that need a clean queue."""
    global _queue
    _queue = queue
