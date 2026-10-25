import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from aiohttp import ClientSession
from eth_abi import decode, encode
from eth_utils import to_checksum_address
from web3 import AsyncHTTPProvider, AsyncWeb3, Web3

from multicall import Call, Multicall, Signature
from multicall.utils import chainids, close_async_w3s

MULTICALL3 = to_checksum_address("0xcA11bde05977b3631167028862bE2a173976CA11")
TOKEN = to_checksum_address("0x" + "11" * 20)
UNKNOWN = to_checksum_address("0x" + "22" * 20)
ALICE = to_checksum_address("0x" + "aa" * 20)
BOB = to_checksum_address("0x" + "bb" * 20)
BALANCES = {ALICE: 1000, BOB: 2500}
BLOCK = 19_000_000

BALANCE_OF = Signature("balanceOf(address)(uint256)")
AGGREGATE = Signature("aggregate((address,bytes)[])(uint256,bytes[])")
TRY_AGGREGATE = Signature(
    "tryBlockAndAggregate(bool,(address,bytes)[])(uint256,uint256,(bool,bytes)[])"
)


def _direct(target, data):
    if target != TOKEN or data[:4] != BALANCE_OF.fourbyte:
        return None
    (account,) = decode(["address"], data[4:])
    account = to_checksum_address(account)
    return encode(["uint256"], [BALANCES[account]]) if account in BALANCES else b""


def _answer(tx):
    data = bytes.fromhex(tx["data"][2:])
    selector, body = data[:4], data[4:]
    if selector == AGGREGATE.fourbyte:
        (calls,) = decode(["(address,bytes)[]"], body)
        outputs = [_direct(to_checksum_address(t), d) or b"" for t, d in calls]
        return encode(["uint256", "bytes[]"], [BLOCK, outputs])
    if selector == TRY_AGGREGATE.fourbyte:
        _, calls = decode(["bool", "(address,bytes)[]"], body)
        results = []
        for t, d in calls:
            out = _direct(to_checksum_address(t), d)
            results.append((out is not None and out != b"", out or b""))
        return encode(["uint256", "uint256", "(bool,bytes)[]"], [BLOCK, 0, results])
    if tx["to"].lower() == TOKEN.lower():
        out = _direct(TOKEN, data)
        if out is None:
            raise AssertionError("unexpected direct call")
        return out
    raise AssertionError(f"unexpected eth_call selector {selector.hex()}")


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.methods.append(req["method"])
        self.server.params.append(req["params"])
        try:
            if req["method"] == "eth_chainId":
                result = "0x1"
            elif req["method"] == "eth_call":
                result = "0x" + _answer(req["params"][0]).hex()
            else:
                raise AssertionError(f"unexpected method {req['method']}")
            body = {"jsonrpc": "2.0", "id": req["id"], "result": result}
        except AssertionError as e:
            body = {"jsonrpc": "2.0", "id": req["id"], "error": {"code": -32000, "message": str(e)}}
        payload = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture
def rpc():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.methods, server.params = [], []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def test_chain_id_of_async_w3_is_read_over_http(rpc):
    server, url = rpc
    from multicall.utils import chain_id

    async_w3 = AsyncWeb3(AsyncHTTPProvider(url))
    try:
        assert chain_id(async_w3) == 1
        assert chainids[async_w3] == 1
        assert server.methods == ["eth_chainId"]
    finally:
        chainids.pop(async_w3, None)


@pytest.mark.parametrize("async_w3_input", [False, True], ids=["sync_w3", "async_w3"])
def test_multicall_aggregate_end_to_end(rpc, async_w3_input):
    server, url = rpc
    sync_w3 = Web3(Web3.HTTPProvider(url))
    calls = [
        Call(TOKEN, ["balanceOf(address)(uint256)", ALICE], [("alice", None)]),
        Call(TOKEN, ["balanceOf(address)(uint256)", BOB], [("bob", None)]),
        Call(UNKNOWN, ["balanceOf(address)(uint256)", ALICE], [("ghost", None)]),
    ]

    async def main():
        session = None
        if async_w3_input:
            provider = AsyncHTTPProvider(url, {"timeout": 30})
            session = await provider.cache_async_session(ClientSession())
            _w3 = AsyncWeb3(provider)
        else:
            _w3 = sync_w3
        try:
            return await Multicall(calls, _w3=_w3).coroutine()
        finally:
            await close_async_w3s()
            if session is not None:
                await session.close()

    result = asyncio.run(main())
    assert result == {"alice": 1000, "bob": 2500, "ghost": None}

    eth_calls = [p for m, p in zip(server.methods, server.params) if m == "eth_call"]
    assert len(eth_calls) == 1
    tx, _block, overrides = eth_calls[0]
    assert tx["to"].lower() == MULTICALL3.lower()
    assert bytes.fromhex(tx["data"][2:])[:4] == AGGREGATE.fourbyte
    assert any(k.lower() == MULTICALL3.lower() for k in overrides)


def test_multicall_try_aggregate_end_to_end(rpc):
    _, url = rpc
    sync_w3 = Web3(Web3.HTTPProvider(url))
    calls = [
        Call(TOKEN, ["balanceOf(address)(uint256)", ALICE], [("alice", None)]),
        Call(UNKNOWN, ["balanceOf(address)(uint256)", ALICE], [("ghost", None)]),
    ]

    async def main():
        try:
            return await Multicall(calls, require_success=False, _w3=sync_w3).coroutine()
        finally:
            await close_async_w3s()

    assert asyncio.run(main()) == {"alice": 1000, "ghost": None}


def test_sync_and_async_single_call(rpc):
    _, url = rpc
    sync_w3 = Web3(Web3.HTTPProvider(url))
    call = Call(TOKEN, ["balanceOf(address)(uint256)", BOB], None, _w3=sync_w3)
    assert call() == 2500

    async def main():
        try:
            return await Call(TOKEN, ["balanceOf(address)(uint256)", ALICE], None, _w3=sync_w3)
        finally:
            await close_async_w3s()

    assert asyncio.run(main()) == 1000
