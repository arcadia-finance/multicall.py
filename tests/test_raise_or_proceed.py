"""Unit tests for _raise_or_proceed error classification.

These tests are self-contained (no network, no brownie); run with:
    pytest --noconftest tests/test_raise_or_proceed.py
"""

import pytest
from web3.exceptions import Web3RPCError

from multicall.multicall import _raise_or_proceed

OUT_OF_GAS_MSG = (
    "{'message': 'out of gas: gas exhausted during memory expansion: 50000000', 'code': -32003}"
)
BODY_LIMIT_MSG = (
    "{'message': 'Request body size limit reached. To remove restrictions, "
    "order a dedicated full node here: https://www.allnodes.com/op/host', 'code': 0}"
)


def test_web3rpcerror_out_of_gas_is_rebatchable():
    # Must not raise: the batcher should get a chance to split the calls.
    _raise_or_proceed(Web3RPCError(OUT_OF_GAS_MSG), ct_calls=100, ConnErr_retries=0)


def test_web3rpcerror_out_of_gas_single_call_raises():
    # A single call that runs out of gas cannot be split any further.
    with pytest.raises(Web3RPCError):
        _raise_or_proceed(Web3RPCError(OUT_OF_GAS_MSG), ct_calls=1, ConnErr_retries=0)


def test_web3rpcerror_body_size_limit_is_rebatchable():
    _raise_or_proceed(Web3RPCError(BODY_LIMIT_MSG), ct_calls=100, ConnErr_retries=0)


def test_web3rpcerror_unrelated_raises():
    with pytest.raises(Web3RPCError):
        _raise_or_proceed(Web3RPCError("execution reverted"), ct_calls=100, ConnErr_retries=0)


def test_valueerror_out_of_gas_still_rebatchable():
    # web3 < 7 surfaces RPC errors as ValueError; behavior must be preserved.
    _raise_or_proceed(ValueError(OUT_OF_GAS_MSG), ct_calls=100, ConnErr_retries=0)
