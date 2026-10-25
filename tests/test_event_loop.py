import asyncio
import subprocess
import sys
import threading
import warnings

from multicall.utils import await_awaitable, get_event_loop


def _in_fresh_thread(fn):
    result = {}

    def target():
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            try:
                result["value"] = fn()
            except BaseException as e:
                result["error"] = e
            try:
                get_event_loop().close()
                asyncio.set_event_loop(None)
            except BaseException as e:
                result.setdefault("error", e)

    thread = threading.Thread(target=target)
    thread.start()
    thread.join()
    if "error" in result:
        raise result["error"]
    return result["value"]


def test_no_loop_gives_one_reused_loop_without_warnings():
    def check():
        first, second = get_event_loop(), get_event_loop()
        return first is second, first.is_closed()

    assert _in_fresh_thread(check) == (True, False)


def test_a_loop_set_by_the_caller_is_returned():
    def check():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return get_event_loop() is loop

    assert _in_fresh_thread(check)


def test_a_closed_loop_is_replaced():
    def check():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.close()
        replacement = get_event_loop()
        return replacement is not loop, replacement.is_closed()

    assert _in_fresh_thread(check) == (True, False)


def test_await_awaitable_runs_without_a_loop_and_without_warnings():
    async def answer():
        return 42

    assert _in_fresh_thread(lambda: await_awaitable(answer())) == 42


def test_await_awaitable_in_the_main_thread_without_warnings():
    code = (
        "import asyncio\n"
        "from multicall.utils import await_awaitable, get_event_loop\n"
        "async def answer():\n"
        "    return 42\n"
        "assert await_awaitable(answer()) == 42\n"
        "assert await_awaitable(answer()) == 42\n"
        "get_event_loop().close()\n"
    )
    ignore_web3_import = "ignore:websockets.legacy is deprecated:DeprecationWarning"
    subprocess.run(
        [sys.executable, "-W", "error", "-W", ignore_web3_import, "-c", code], check=True
    )


def test_a_running_loop_is_returned():
    async def main():
        return get_event_loop() is asyncio.get_running_loop()

    assert asyncio.run(main())
