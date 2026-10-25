"""Unit tests for NotSoBrightBatcher and the single-call rebatch guard.

Self-contained (no network); run with:
    pytest tests/test_batcher.py
"""

import asyncio

import pytest

from multicall import multicall as multicall_module
from multicall.multicall import Multicall, NotSoBrightBatcher


@pytest.mark.parametrize("n", range(40))
@pytest.mark.parametrize("step", [1, 2, 3, 5, 9, 10, 39, 40, 10000])
def test_batch_calls_covers_every_call_in_order(n, step):
    calls = list(range(n))
    batches = NotSoBrightBatcher().batch_calls(calls, step)
    assert [call for batch in batches for call in batch] == calls
    assert all(len(batch) <= step for batch in batches)


@pytest.mark.parametrize("step", [0, -1])
def test_batch_calls_treats_a_step_below_one_as_one(step):
    assert NotSoBrightBatcher().batch_calls([1, 2, 3], step) == [[1], [2], [3]]


def test_batch_calls_of_nothing_is_one_empty_batch():
    assert NotSoBrightBatcher().batch_calls([], 5) == [[]]


@pytest.mark.parametrize("n", [1, 2, 3, 99, 100, 101])
def test_rebatch_never_sets_the_step_below_one(n):
    batcher = NotSoBrightBatcher()
    batcher.step = n
    batcher.rebatch(list(range(n)))
    assert batcher.step >= 1


def test_a_single_call_that_keeps_timing_out_raises_instead_of_rebatching(monkeypatch):
    class _TimingOut:
        async def coroutine(self, args):
            raise TimeoutError("node did not answer")

    monkeypatch.setattr(multicall_module, "get_args", lambda calls, require_success: [])
    monkeypatch.setattr(Multicall, "aggregate", property(lambda self: _TimingOut()))
    mc = Multicall.__new__(Multicall)
    mc.require_success = True
    mc.gas_limited = False
    before = multicall_module.batcher.step

    with pytest.raises(TimeoutError, match="node did not answer"):
        asyncio.run(asyncio.wait_for(mc.fetch_outputs(["only-call"]), timeout=5))

    assert multicall_module.batcher.step == before
