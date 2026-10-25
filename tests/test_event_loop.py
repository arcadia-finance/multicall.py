import asyncio
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
            finally:
                get_event_loop().close()
                asyncio.set_event_loop(None)

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


def test_await_awaitable_runs_without_a_loop_and_without_warnings():
    async def answer():
        return 42

    assert _in_fresh_thread(lambda: await_awaitable(answer())) == 42


def test_a_running_loop_is_returned():
    async def main():
        return get_event_loop() is asyncio.get_running_loop()

    assert asyncio.run(main())
