from asyncio import AbstractEventLoop, Semaphore, get_running_loop, new_event_loop, set_event_loop
from asyncio import gather as _gather
from asyncio import get_event_loop as _get_event_loop
from collections.abc import Awaitable, Iterable
from functools import lru_cache
from typing import Any, Final

from aiohttp import ClientSession, ClientTimeout, TCPConnector
from eth_utils import to_checksum_address as _to_checksum_address
from web3 import AsyncHTTPProvider, AsyncWeb3, HTTPProvider, Web3
from web3.providers.async_base import AsyncBaseProvider

from multicall.constants import AIOHTTP_TIMEOUT, ASYNC_SEMAPHORE, NO_STATE_OVERRIDE

to_checksum_address: Final = lru_cache(maxsize=4096)(_to_checksum_address)

chainids: Final[dict[Web3, int]] = {}


def chain_id(w3: Web3) -> int:
    """
    Returns chain id for an instance of Web3. Helps save repeat calls to node.
    Handles AsyncWeb3 by making a sync HTTP request to avoid unawaited coroutines.
    """
    try:
        return chainids[w3]
    except KeyError:
        if w3.eth.is_async:
            chainids[w3] = Web3(HTTPProvider(get_endpoint(w3))).eth.chain_id
        else:
            chainids[w3] = w3.eth.chain_id
        return chainids[w3]


async_w3s: Final[dict[Web3, Web3]] = {}

# Cap on simultaneous keep-alive connections per provider session (see get_async_w3).
_KEEPALIVE_CONNECTION_LIMIT: Final = 100


def get_endpoint(w3: Web3) -> str:
    provider = w3.provider
    if isinstance(provider, str):
        return provider
    if hasattr(provider, "_active_provider"):
        provider = provider._get_active_provider(False)
    return provider.endpoint_uri  # type: ignore [no-any-return]


async def get_async_w3(w3: Web3) -> Web3:
    if w3 in async_w3s:
        return async_w3s[w3]
    if w3.eth.is_async and isinstance(w3.provider, AsyncBaseProvider):
        timeout = w3.provider._request_kwargs.get("timeout")
        if isinstance(timeout, ClientTimeout):
            timeout = timeout.total

        if timeout is None or timeout < AIOHTTP_TIMEOUT.total:
            w3.provider._request_kwargs["timeout"] = AIOHTTP_TIMEOUT

        async_w3s[w3] = w3
        return w3

    endpoint = get_endpoint(w3)
    request_kwargs = {"timeout": AIOHTTP_TIMEOUT}
    provider = AsyncHTTPProvider(endpoint, request_kwargs)
    async_w3 = AsyncWeb3(provider, middleware=[])

    # web3.py v7's AsyncHTTPProvider lazily caches an aiohttp session built with
    # TCPConnector(force_close=True), which disables HTTP keep-alive — every RPC call then
    # pays a fresh TCP+TLS handshake. Under the concurrency Multicall drives this serializes
    # into multi-second latencies and request timeouts. Pre-seed a pooled keep-alive session
    # so connections are reused (no-op for websocket providers).
    session = ClientSession(
        raise_for_status=True,
        connector=TCPConnector(limit=_KEEPALIVE_CONNECTION_LIMIT),
    )
    await provider.cache_async_session(session)

    # A concurrent first call for the same w3 got here first while we awaited.
    if w3 in async_w3s:
        await session.close()
        return async_w3s[w3]

    async_w3s[w3] = async_w3  # type: ignore [assignment]
    return async_w3  # type: ignore [return-value]


def _current_loop() -> AbstractEventLoop | None:
    try:
        return _get_event_loop()
    except RuntimeError:
        return None


async def close_async_w3s() -> None:
    while async_w3s:
        w3, async_w3 = async_w3s.popitem()
        if async_w3 is not w3:
            await async_w3.provider.disconnect()


def get_event_loop() -> AbstractEventLoop:
    try:
        return get_running_loop()
    except RuntimeError:
        pass
    loop = _current_loop()
    if loop is None or loop.is_closed():
        loop = new_event_loop()
        set_event_loop(loop)
    return loop


def await_awaitable[T](awaitable: Awaitable[T]) -> T:
    return get_event_loop().run_until_complete(awaitable)


def raise_if_exception(obj: Any) -> None:
    if isinstance(obj, Exception):
        raise obj


def raise_if_exception_in(iterable: Iterable[Any]) -> None:
    for obj in iterable:
        raise_if_exception(obj)


async def gather[T](coroutines: Iterable[Awaitable[T]]) -> list[T]:
    results = await _gather(*coroutines, return_exceptions=True)
    raise_if_exception_in(results)
    return results  # type: ignore [return-value]


_state_override_supported: Final[dict[Web3, bool]] = {}


def state_override_supported(w3: Web3) -> bool:
    try:
        return _state_override_supported[w3]
    except KeyError:
        is_supported = chain_id(w3) not in NO_STATE_OVERRIDE
        _state_override_supported[w3] = is_supported
        return is_supported


_semaphores: Final[dict[AbstractEventLoop, Semaphore]] = {}


def _get_semaphore() -> Semaphore:
    """
    Returns a `Semaphore` attached to the current event loop.

    NOTE: This prevents an "attached to a different loop" edge case if the event loop is changed during your script run
    """
    loop = get_event_loop()
    try:
        return _semaphores[loop]
    except KeyError:
        semaphore = _semaphores[loop] = Semaphore(ASYNC_SEMAPHORE)
        return semaphore
