"""Offline concurrency tests: image tasks overlap, workflow mutations do not."""
import threading

import pytest

from marketing_agent.enterprise.review_scheduler import ReviewBatch, run_review_workflows


def test_cross_group_reviews_overlap_and_callbacks_stay_on_caller_thread():
    caller = threading.get_ident()
    barrier = threading.Barrier(3, timeout=5)
    events = []

    def workflow(group):
        assert threading.get_ident() == caller

        def task():
            assert threading.get_ident() != caller
            barrier.wait()
            return group

        def received(key, value):
            assert threading.get_ident() == caller
            events.append((group, key, value))

        rows = yield ReviewBatch({1: task}, received, lambda key: pytest.fail('unexpected skip'))
        assert threading.get_ident() == caller
        return rows[1]

    assert run_review_workflows((workflow(i) for i in range(6)), workers=3, stopped=lambda: False) == list(range(6))
    assert sorted(events) == [(i, 1, i) for i in range(6)]


def test_global_limit_covers_groups_pages_and_multiple_review_waves():
    lock = threading.Lock()
    barrier = threading.Barrier(3, timeout=5)
    counts = {'tasks': 0, 'peak_tasks': 0, 'workflows': 0, 'peak_workflows': 0}
    callbacks = []

    def workflow(group):
        counts['workflows'] += 1
        counts['peak_workflows'] = max(counts['peak_workflows'], counts['workflows'])
        answers = []
        for wave in range(2):
            def task(page):
                with lock:
                    counts['tasks'] += 1
                    counts['peak_tasks'] = max(counts['peak_tasks'], counts['tasks'])
                barrier.wait()
                with lock:
                    counts['tasks'] -= 1
                return group, wave, page

            tasks = {page: lambda page=page: task(page) for page in range(3)}
            result = yield ReviewBatch(tasks, lambda key, value: callbacks.append(value), lambda key: None)
            assert list(result) == [0, 1, 2]
            answers.extend(result.values())
        counts['workflows'] -= 1
        return answers

    result = run_review_workflows((workflow(i) for i in range(6)), workers=3, stopped=lambda: False)
    assert result == [[(group, wave, page) for wave in range(2) for page in range(3)] for group in range(6)]
    assert len(callbacks) == 36
    assert counts == {'tasks': 0, 'peak_tasks': 3, 'workflows': 0, 'peak_workflows': 3}


def test_review_is_submitted_before_preparing_the_next_workflow():
    started, prepared = threading.Event(), threading.Event()

    def first():
        def task():
            started.set()
            assert prepared.wait(5), 'next workflow preparation did not overlap the review'
            return 'first'
        result = yield ReviewBatch({1: task}, lambda *args: None, lambda key: None)
        return result[1]

    def second():
        assert started.wait(5), 'review must start before preparing this workflow'
        prepared.set()
        result = yield ReviewBatch({1: lambda: 'second'}, lambda *args: None, lambda key: None)
        return result[1]

    assert run_review_workflows([first(), second()], workers=2, stopped=lambda: False) == ['first', 'second']


def test_finished_workflow_is_replaced_without_waiting_for_a_slow_neighbor():
    replacement_started = threading.Event()
    finished = []

    def workflow(group):
        def task():
            if group == 0:
                assert replacement_started.wait(5), 'completed slot was not refilled'
            elif group == 2:
                replacement_started.set()
            return group
        result = yield ReviewBatch({1: task}, lambda *args: None, lambda key: None)
        finished.append(group)
        return result[1]

    assert run_review_workflows((workflow(i) for i in range(3)), workers=2, stopped=lambda: False) == [0, 1, 2]
    assert finished[0] == 1


def test_budget_stop_marks_every_unstarted_page_including_later_workflows():
    caller = threading.get_ident()
    stop = threading.Event()
    calls, received = [], []

    def workflow(group):
        def task(page):
            calls.append((group, page))
            stop.set()
            return 'budget exhausted'

        def skip(key):
            assert threading.get_ident() == caller
            return f'skipped {group}/{key}'

        def record(key, value):
            assert threading.get_ident() == caller
            received.append((group, key, value))

        result = yield ReviewBatch({page: lambda page=page: task(page) for page in range(4)}, record, skip)
        assert list(result) == [0, 1, 2, 3]
        return result

    result = run_review_workflows((workflow(i) for i in range(5)), workers=2, stopped=stop.is_set)
    assert 1 <= len(calls) <= 2  # At most the already-running window can finish.
    assert result == [{page: 'budget exhausted' if (group, page) in calls else f'skipped {group}/{page}'
                      for page in range(4)} for group in range(5)]
    assert len(received) == 20


def test_empty_batches_and_immediately_finished_workflows():
    def workflow(number):
        if number % 2:
            assert (yield ReviewBatch({}, lambda *args: pytest.fail('empty callback'), lambda key: None)) == {}
        return number

    assert run_review_workflows((workflow(i) for i in range(7)), workers=2, stopped=lambda: False) == list(range(7))
    assert run_review_workflows([], workers=1, stopped=lambda: False) == []


@pytest.mark.parametrize('where', ['prepare', 'task', 'callback', 'resume', 'skip'])
def test_exceptions_propagate(where):
    def fail():
        raise RuntimeError(where)

    def workflow():
        if where == 'prepare':
            fail()
        yield ReviewBatch({1: fail if where == 'task' else lambda: 1},
            lambda *args: fail() if where == 'callback' else None,
            lambda key: fail())
        if where == 'resume':
            fail()

    with pytest.raises(RuntimeError, match=where):
        run_review_workflows([workflow()], workers=2, stopped=lambda: where == 'skip')


@pytest.mark.parametrize('workers', [0, -1, True, 1.5])
def test_invalid_worker_counts_fail_before_starting_work(workers):
    with pytest.raises(ValueError, match='positive integer'):
        run_review_workflows([], workers=workers, stopped=lambda: False)
