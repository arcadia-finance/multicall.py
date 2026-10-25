import os
import subprocess
import sys

import pytest


def _run_subprocess(code, args=(), env_overrides=None, drop=()):
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env.update(env_overrides or {})
    return subprocess.run(
        [sys.executable, "-c", code, *args], check=True, capture_output=True, text=True, env=env
    ).stdout.strip()


_W3_PROBE = """
import sys, types
mode = sys.argv[1]
if mode == "no_brownie":
    sys.modules["brownie"] = None
else:
    brownie = types.ModuleType("brownie")
    brownie.network = types.SimpleNamespace(is_connected=lambda: mode == "connected")
    brownie.web3 = object()
    sys.modules["brownie"] = brownie
import web3.auto
from multicall import constants
if constants.w3 is web3.auto.w3:
    print("auto")
elif mode != "no_brownie" and constants.w3 is brownie.web3:
    print("brownie")
else:
    print("other")
"""


@pytest.mark.parametrize(
    ("mode", "expected"),
    [("no_brownie", "auto"), ("disconnected", "auto"), ("connected", "brownie")],
)
def test_constants_w3_selection(mode, expected):
    assert _run_subprocess(_W3_PROBE, args=[mode]) == expected


_ENV_PROBE = """
import logging, sys
sys.modules["brownie"] = None
from multicall import constants
print(constants.GAS_LIMIT, int(constants.AIOHTTP_TIMEOUT.total), constants.ASYNC_SEMAPHORE,
      bool(logging.getLogger("multicall.call").handlers))
"""

_ENV_VARS = ("GAS_LIMIT", "AIOHTTP_TIMEOUT", "MULTICALL_CALL_SEMAPHORE", "MULTICALL_DEBUG")


def test_env_defaults():
    assert _run_subprocess(_ENV_PROBE, drop=_ENV_VARS) == "50000000 30 1000 False"


def test_env_overrides_are_parsed_as_ints():
    out = _run_subprocess(
        _ENV_PROBE,
        env_overrides={
            "GAS_LIMIT": "123",
            "AIOHTTP_TIMEOUT": "7",
            "MULTICALL_CALL_SEMAPHORE": "5",
            "MULTICALL_DEBUG": "1",
        },
    )
    assert out == "123 7 5 True"


def test_empty_multicall_debug_disables_logging():
    out = _run_subprocess(_ENV_PROBE, env_overrides={"MULTICALL_DEBUG": ""})
    assert out == "50000000 30 1000 False"
