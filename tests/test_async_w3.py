import asyncio
import gc
import warnings

from aiohttp import ClientSession
from web3 import AsyncHTTPProvider, AsyncWeb3, Web3

from multicall.utils import async_w3s, close_async_w3s, get_async_w3


def test_concurrent_first_calls_share_one_session():
    w3 = Web3(Web3.HTTPProvider("http://127.0.0.1:1"))

    async def main():
        first, second = await asyncio.gather(get_async_w3(w3), get_async_w3(w3))
        assert first is second
        gc.collect()
        await async_w3s.pop(w3).provider.disconnect()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ResourceWarning)
        asyncio.run(main())
        gc.collect()

    assert not [w for w in caught if "Unclosed client session" in str(w.message)]


def test_close_async_w3s_closes_every_cached_session():
    w3s = [Web3(Web3.HTTPProvider(f"http://127.0.0.1:{port}")) for port in (1, 2)]

    async def main():
        for w3 in w3s:
            await get_async_w3(w3)
        await close_async_w3s()
        assert async_w3s == {}

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ResourceWarning)
        asyncio.run(main())
        gc.collect()

    assert not [w for w in caught if "Unclosed" in str(w.message)]


def test_close_async_w3s_leaves_a_callers_own_async_w3_connected():
    async def main():
        own = AsyncWeb3(AsyncHTTPProvider("http://127.0.0.1:1"))
        session = await own.provider.cache_async_session(ClientSession())
        assert await get_async_w3(own) is own
        await close_async_w3s()
        assert not session.closed
        await session.close()

    asyncio.run(main())
