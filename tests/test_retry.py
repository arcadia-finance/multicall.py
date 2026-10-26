import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from web3 import Web3

from multicall import Call, Multicall
from multicall.utils import chainids, close_async_w3s
from tests.test_stub_rpc import ALICE, BOB, TOKEN, _answer

RATE_LIMIT = {"code": 429, "message": "Too many requests"}
REVERT = {"code": 3, "message": "execution reverted"}


class _ScriptedHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.methods.append(req["method"])
        queue = self.server.script.get(req["method"], [])
        step = queue.pop(0) if queue else None
        if step == "http429":
            self.send_response(429)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if isinstance(step, dict):
            body = {"jsonrpc": "2.0", "id": req["id"], "error": step}
        elif req["method"] == "eth_chainId":
            body = {"jsonrpc": "2.0", "id": req["id"], "result": "0x1"}
        else:
            body = {
                "jsonrpc": "2.0",
                "id": req["id"],
                "result": "0x" + _answer(req["params"][0]).hex(),
            }
        payload = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture
def no_retry_sleep(monkeypatch):
    module = sys.modules["eth_retry.eth_retry"]

    async def aiosleep(_seconds):
        return None

    monkeypatch.setattr(module, "aiosleep", aiosleep)
    monkeypatch.setattr(module, "timesleep", lambda _seconds: None)


@pytest.fixture
def scripted_rpc(no_retry_sleep):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ScriptedHandler)
    server.methods, server.script = [], {}
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    w3 = Web3(Web3.HTTPProvider(f"http://127.0.0.1:{server.server_address[1]}"))
    chainids[w3] = 1
    try:
        yield server, w3
    finally:
        chainids.pop(w3, None)
        server.shutdown()
        server.server_close()


def _balances():
    return [
        Call(TOKEN, ["balanceOf(address)(uint256)", ALICE], [("alice", None)]),
        Call(TOKEN, ["balanceOf(address)(uint256)", BOB], [("bob", None)]),
    ]


def _run_multicall(w3):
    async def main():
        try:
            return await Multicall(_balances(), _w3=w3, _chain_id=1).coroutine()
        finally:
            await close_async_w3s()

    return asyncio.run(main())


def test_multicall_retries_a_rate_limit_inside_http_200(scripted_rpc):
    server, w3 = scripted_rpc
    server.script = {"eth_call": [RATE_LIMIT]}
    assert _run_multicall(w3) == {"alice": 1000, "bob": 2500}
    assert server.methods.count("eth_call") == 2


def test_multicall_survives_a_burst_of_http_429(scripted_rpc):
    server, w3 = scripted_rpc
    server.script = {"eth_call": ["http429"] * 7}
    assert _run_multicall(w3) == {"alice": 1000, "bob": 2500}


def test_sync_call_retries_a_rate_limit_inside_http_200(scripted_rpc):
    server, w3 = scripted_rpc
    server.script = {"eth_call": [RATE_LIMIT]}
    assert Call(TOKEN, ["balanceOf(address)(uint256)", BOB], None, _w3=w3)() == 2500
    assert server.methods.count("eth_call") == 2


def test_chain_id_retries_a_rate_limit_inside_http_200(scripted_rpc):
    server, w3 = scripted_rpc
    server.script = {"eth_chainId": [RATE_LIMIT]}
    from multicall.utils import chain_id

    chainids.pop(w3)
    assert chain_id(w3) == 1
    assert server.methods == ["eth_chainId", "eth_chainId"]


def test_a_revert_is_not_retried(scripted_rpc):
    server, w3 = scripted_rpc
    server.script = {"eth_call": [REVERT]}
    with pytest.raises(Exception, match="execution reverted"):
        Call(TOKEN, ["balanceOf(address)(uint256)", BOB], None, _w3=w3)()
    assert server.methods.count("eth_call") == 1
