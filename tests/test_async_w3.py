import asyncio
import gc
import warnings

from web3 import Web3

from multicall.utils import async_w3s, get_async_w3


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
