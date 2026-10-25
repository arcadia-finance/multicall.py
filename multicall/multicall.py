from asyncio import TimeoutError
from collections.abc import Generator, Sequence
from time import time
from typing import (
    Any,
    Final,
    final,
)

import aiohttp
import requests
from eth_typing import AnyAddress
from eth_utils import toolz
from web3 import Web3
from web3.exceptions import Web3RPCError

from multicall.call import Call
from multicall.constants import (
    GAS_LIMIT,
    MULTICALL2_ADDRESSES,
    MULTICALL3_ADDRESSES,
    MULTICALL3_BYTECODE,
    MULTICALL4_BYTECODE,
    w3,
)
from multicall.loggers import setup_logger
from multicall.utils import (
    _get_semaphore,
    await_awaitable,
    chain_id,
    chainids,
    gather,
    state_override_supported,
    to_checksum_address,
)

logger: Final = setup_logger(__name__)
log_warning: Final = logger.warning
log_debug: Final = logger.debug

CallResponse = tuple[None | bool, bytes]

concat: Final = toolz.concat  # type: ignore [attr-defined]
mapcat: Final = toolz.mapcat  # type: ignore [attr-defined]


def get_args(calls: list[Call], require_success: bool = True) -> list[bool | list[list[Any]]]:
    if any(call.gas is not None for call in calls):
        return [
            [
                [call.target, call.allow_failure, call.value, call.gas or 0, call.data]
                for call in calls
            ]
        ]
    if require_success is True:
        return [[[call.target, call.data] for call in calls]]
    return [require_success, [[call.target, call.data] for call in calls]]


def unpack_aggregate_outputs(outputs: Any) -> tuple[CallResponse, ...]:
    return tuple((None, output) for output in outputs)


@final
class Multicall:
    __slots__ = (
        "block_id",
        "calls",
        "chainid",
        "gas_limit",
        "gas_limited",
        "multicall_address",
        "origin",
        "require_success",
        "w3",
    )

    def __init__(
        self,
        calls: list[Call],
        block_id: int | None = None,
        require_success: bool = True,
        gas_limit: int = GAS_LIMIT,
        _w3: Web3 = w3,
        origin: AnyAddress | None = None,
        _chain_id: int | None = None,
    ) -> None:
        self.calls: Final = calls
        self.block_id = block_id
        self.require_success: Final = require_success
        self.gas_limit: Final = gas_limit
        # A per-call gas limit routes the batch through Multicall4.aggregate4.
        self.gas_limited: Final = any(call.gas is not None for call in calls)
        self.w3: Final = _w3
        self.origin: Final = to_checksum_address(origin) if origin else None
        chainid: int = _chain_id if _chain_id is not None else chain_id(_w3)  # type: ignore [assignment]
        self.chainid: Final = chainid
        # Populate the chain_id cache so Call.coroutine() doesn't call chain_id()
        # with an AsyncWeb3 instance (which would return an unawaited coroutine)
        if _w3 not in chainids:
            chainids[_w3] = chainid
        # aggregate4 only exists in the injected Multicall4, so a gas limit needs a
        # chain that supports state override.
        if self.gas_limited and not state_override_supported(_w3):
            raise ValueError("a per-call gas limit requires a chain that supports state override")
        multicall_map = (
            MULTICALL3_ADDRESSES if chainid in MULTICALL3_ADDRESSES else MULTICALL2_ADDRESSES
        )
        self.multicall_address: Final = multicall_map[chainid]

    def __call__(self) -> dict[str, Any]:
        start = time()
        response = await_awaitable(self)
        log_debug("Multicall took %ss", time() - start)
        return response

    def __await__(self) -> Generator[Any, Any, dict[str, Any]]:
        return self.coroutine().__await__()

    @property
    def multicall_sig(self) -> str:
        if self.gas_limited:
            return "aggregate4((address,bool,uint256,uint256,bytes)[])((bool,bytes)[])"
        return (
            "aggregate((address,bytes)[])(uint256,bytes[])"
            if self.require_success
            else "tryBlockAndAggregate(bool,(address,bytes)[])(uint256,uint256,(bool,bytes)[])"
        )

    async def coroutine(self) -> dict[str, Any]:
        batches = await gather(
            [
                self.fetch_outputs(batch, id=str(i))
                for i, batch in enumerate(batcher.batch_calls(self.calls, batcher.step))
            ]
        )
        return dict(mapcat(dict.items, concat(batches)))

    def _contract_method(self, request_signature: list, return_signature: tuple) -> None:
        self.calls.append(Call(self.multicall_address, request_signature, [return_signature]))

    def add_base_fee(self, return_signature: tuple = ("base_fee", None)) -> None:
        signature = ["getBasefee()(uint256)"]
        self._contract_method(signature, return_signature)

    def add_block_hash(
        self, block_number: int, return_signature: tuple = ("block_hash", None)
    ) -> None:
        signature = ["getBlockHash(uint256)(bytes32)", block_number]
        self._contract_method(signature, return_signature)

    def add_block_number(self, return_signature: tuple = ("block_number", None)) -> None:
        signature = ["getBlockNumber()(uint256)"]
        self._contract_method(signature, return_signature)

    def add_chain_id(self, return_signature: tuple = ("chain_id", None)) -> None:
        signature = ["getChainId()(uint256)"]
        self._contract_method(signature, return_signature)

    def add_block_coinbase(self, return_signature: tuple = ("coinbase", None)) -> None:
        signature = ["getCurrentBlockCoinbase()(address)"]
        self._contract_method(signature, return_signature)

    def add_block_difficulty(self, return_signature: tuple = ("difficulty", None)) -> None:
        signature = ["getCurrentBlockDifficulty()(address)"]
        self._contract_method(signature, return_signature)

    def add_block_gas_limit(self, return_signature: tuple = ("gas_limit", None)) -> None:
        signature = ["getCurrentBlockGasLimit()(uint256)"]
        self._contract_method(signature, return_signature)

    def add_block_timestamp(self, return_signature: tuple = ("timestamp", None)) -> None:
        signature = ["getCurrentBlockTimestamp()(uint256)"]
        self._contract_method(signature, return_signature)

    def add_eth_balance(
        self, address: AnyAddress, return_signature: tuple = ("eth_balance", None)
    ) -> None:
        signature = ["getEthBalance(address)(uint256)", address]
        self._contract_method(signature, return_signature)

    def add_last_block_hash(self, return_signature: tuple = ("last_block_hash", None)) -> None:
        signature = ["getLastBlockHash()(bytes32)"]
        self._contract_method(signature, return_signature)

    async def fetch_outputs(
        self, calls: list[Call], ConnErr_retries: int = 0, id: str = ""
    ) -> list[CallResponse]:
        log_debug("coroutine %s started", id)

        if calls is None:
            calls = self.calls

        async with _get_semaphore():
            try:
                args = get_args(calls, self.require_success)
                if self.gas_limited:
                    # aggregate4 returns Result[(success, bytes)] (like aggregate3Value).
                    outputs = await self.aggregate.coroutine(args)
                elif self.require_success is True:
                    self.block_id, outputs = await self.aggregate.coroutine(args)
                    outputs = unpack_aggregate_outputs(outputs)
                else:
                    self.block_id, _, outputs = await self.aggregate.coroutine(args)
                outputs = [
                    Call.decode_output(output, call.signature, call.returns, success)
                    for call, (success, output) in zip(calls, outputs)
                ]
                log_debug("coroutine %s finished", id)
                return outputs
            except Exception as e:
                _raise_or_proceed(e, len(calls), ConnErr_retries=ConnErr_retries)
                # A single call cannot be split any further, so rebatching it would retry
                # the same request forever: surface the error to the caller instead.
                if len(calls) <= 1:
                    raise

        # Failed, we need to rebatch the calls and try again.
        batch_results = await gather(
            [
                self.fetch_outputs(chunk, ConnErr_retries + 1, f"{id}_{i}")
                for i, chunk in enumerate(batcher.rebatch(calls))
            ]
        )

        log_debug("coroutine %s finished", id)
        return [result for chunk in batch_results for result in chunk]

    @property
    def aggregate(self) -> Call:
        if state_override_supported(self.w3):
            # Multicall4 is a superset of Multicall3 (same selectors); inject it only
            # when aggregate4 is needed so the stock path stays byte-for-byte unchanged.
            override_code = MULTICALL4_BYTECODE if self.gas_limited else MULTICALL3_BYTECODE
            return Call(
                self.multicall_address,
                self.multicall_sig,
                returns=None,
                _w3=self.w3,
                block_id=self.block_id,
                origin=self.origin,
                gas_limit=self.gas_limit,
                state_override_code=override_code,
            )

        # If state override is not supported, we simply skip it.
        # This will mean you're unable to access full historical data on chains without state override support.
        return Call(
            self.multicall_address,
            self.multicall_sig,
            returns=None,
            _w3=self.w3,
            origin=self.origin,
            block_id=self.block_id,
            gas_limit=self.gas_limit,
        )


@final
class NotSoBrightBatcher:
    """
    This class helps with processing a large volume of large multicalls.
    It's not so bright, but should quickly bring the batch size down to something reasonable for your node.
    """

    __slots__ = ("step",)

    def __init__(self) -> None:
        self.step = 10000

    def batch_calls(self, calls: list[Call], step: int) -> list[list[Call]]:
        """
        Batch calls into chunks of at most `step` calls (at least 1), covering every call.
        An empty list yields one empty batch.
        """
        step = max(1, step)
        return [calls[start : start + step] for start in range(0, len(calls), step)] or [calls]

    def split_calls(
        self, calls: list[Call], unused: int | None = None
    ) -> tuple[list[Call], list[Call]]:
        """
        Split calls into 2 batches in case request is too large.
        We do this to help us find optimal `self.step` value.
        """
        center = len(calls) // 2
        chunk_1 = calls[:center]
        chunk_2 = calls[center:]
        return chunk_1, chunk_2

    def rebatch(self, calls: list[Call]) -> Sequence[list[Call]]:
        # If a separate coroutine changed `step` after calls were last batched, we will use the new `step` for rebatching.
        if self.step <= len(calls) // 2:
            return self.batch_calls(calls, self.step)

        # Otherwise we will split calls in half.
        if self.step >= len(calls):
            new_step = max(1, round(len(calls) * 0.99) if len(calls) >= 100 else len(calls) - 1)
            log_warning(
                f"Multicall batch size reduced from {self.step} to {new_step}. The failed batch had {len(calls)} calls."
            )
            self.step = new_step
        return self.split_calls(calls, self.step)


batcher: Final = NotSoBrightBatcher()


def _raise_or_proceed(e: Exception, ct_calls: int, ConnErr_retries: int) -> None:
    """Depending on the exception, either raises or ignores and allows `batcher` to rebatch."""
    strings: tuple[str, ...]
    if isinstance(e, aiohttp.ClientOSError):
        if "broken pipe" not in str(e).lower():
            raise e
        log_warning(e)
    elif isinstance(e, aiohttp.ClientResponseError):
        strings = "request entity too large", "connection reset by peer"
        if all(string not in str(e).lower() for string in strings):
            raise e
        log_warning(e)
    elif isinstance(e, requests.ConnectionError):
        if (
            "('Connection aborted.', ConnectionResetError(104, 'Connection reset by peer'))"
            not in str(e)
            or ConnErr_retries > 5
        ):
            raise e
    elif isinstance(e, requests.HTTPError):
        strings = (
            "request entity too large",
            "payload too large",
            "time-out",
            "520 server error",
        )
        if not any(map(str(e).lower().__contains__, strings)):
            raise e
        log_warning(e)
    elif isinstance(e, TimeoutError):
        pass
    elif isinstance(e, Web3RPCError):
        strings = (
            "out of gas",
            "execution aborted (timeout",
            "request body size limit",
            "request entity too large",
            "payload too large",
        )
        if not any(map(str(e).lower().__contains__, strings)):
            raise e
        if ct_calls == 1:
            raise e
        log_warning(e)
    else:
        raise e
