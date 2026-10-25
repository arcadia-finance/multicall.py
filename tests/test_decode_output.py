import pytest

from multicall import Call
from multicall.signature import Signature

STRING = Signature("f()(string)")
OFFSET = (32).to_bytes(32, "big")


@pytest.mark.parametrize(
    "output",
    [
        b"",
        OFFSET + (2).to_bytes(32, "big") + b"\xff\xfe" + b"\x00" * 30,
        OFFSET + (10**30).to_bytes(32, "big"),
    ],
    ids=["empty", "invalid-utf8", "oversized-length"],
)
def test_undecodable_output_is_a_failed_call(output):
    returns = [("name", lambda success, value: (success, value))]
    assert Call.decode_output(output, STRING, returns, success=True) == {"name": (False, None)}


def test_valid_output_decodes():
    output = OFFSET + (3).to_bytes(32, "big") + b"abc" + b"\x00" * 29
    assert Call.decode_output(output, STRING, [("name", None)]) == {"name": "abc"}
