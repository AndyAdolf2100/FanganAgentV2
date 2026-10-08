"""Bounded image reviews with workflow state transitions on the caller thread."""
from collections import deque
from collections.abc import Callable, Generator, Iterable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ReviewBatch:
    tasks: dict[int, Callable[[], Any]]
    on_result: Callable[[int, Any], None]
    skipped_result: Callable[[int], Any]


@dataclass
class _Workflow:
    index: int
    generator: Generator[ReviewBatch, dict, Any]
    batch: ReviewBatch | None = None
    pending: deque = field(default_factory=deque)
    results: dict = field(default_factory=dict)
    in_flight: int = 0


def run_review_workflows(
    workflows: Iterable[Generator[ReviewBatch, dict, Any]], *, workers: int,
    stopped: Callable[[], bool],
) -> list[Any]:
    """Run at most ``workers`` workflows and image calls concurrently.

    Generators prepare immutable candidates and yield their review callables.
    Only those callables run in worker threads. Result callbacks, skipped-result
    factories, and all generator advancement stay on the calling thread.
    """
    if type(workers) is not int or workers < 1:
        raise ValueError('workers must be a positive integer')
    source = iter(enumerate(workflows))
    active, completed, futures = {}, {}, {}
    exhausted = False

    def advance(state):
        try:
            if state.batch is None:
                batch = next(state.generator)
            else:
                # Preserve declared page order regardless of completion order.
                batch = state.generator.send({key: state.results[key] for key in state.batch.tasks})
            while True:
                if not isinstance(batch, ReviewBatch):
                    raise TypeError('workflow must yield a ReviewBatch')
                if batch.tasks:
                    state.batch = batch
                    state.pending = deque(batch.tasks.items())
                    state.results = {}
                    return
                batch = state.generator.send({})
        except StopIteration as done:
            completed[state.index] = done.value
            active.pop(state.index, None)

    def receive(state, key, value):
        state.batch.on_result(key, value)
        state.results[key] = value

    def execute(task):
        # A budget stop can arrive after submission but before a worker starts.
        # Construct its skipped result on the caller thread when it returns.
        if stopped():
            return False, None
        return True, task()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        def dispatch(state):
            if state.pending and len(futures) < workers and not stopped():
                key, task = state.pending.popleft()
                futures[pool.submit(execute, task)] = (state, key)
                state.in_flight += 1
                return True
            return False

        while active or not exhausted:
            # Apply finished observations before preparing more GLM work.
            for future in [future for future in futures if future.done()]:
                state, key = futures.pop(future)
                state.in_flight -= 1
                ran, value = future.result()
                receive(state, key, value if ran else state.batch.skipped_result(key))

            if stopped():
                for state in active.values():
                    while state.pending:
                        key, _ = state.pending.popleft()
                        receive(state, key, state.batch.skipped_result(key))

            for state in list(active.values()):
                if not state.pending and not state.in_flight:
                    advance(state)
                    if state.index in active:
                        dispatch(state)

            # Start a review before preparing the next workflow: slow GLM or
            # browser preparation can overlap an already submitted image call.
            while len(active) < workers and not exhausted:
                try:
                    index, generator = next(source)
                except StopIteration:
                    exhausted = True
                    break
                state = _Workflow(index, generator)
                active[index] = state
                advance(state)
                if index in active:
                    dispatch(state)

            # Share all remaining capacity across groups and continuation pages.
            while len(futures) < workers:
                dispatched = False
                for state in active.values():
                    dispatched = dispatch(state) or dispatched
                if not dispatched:
                    break

            # New batches yielded after a stop still need a complete result map.
            if stopped() and any(state.pending for state in active.values()):
                continue
            if futures:
                wait(futures, return_when=FIRST_COMPLETED)

    return [completed[index] for index in sorted(completed)]
