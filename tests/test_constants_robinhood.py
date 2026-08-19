from multicall.constants import (
    MULTICALL3_ADDRESSES,
    NO_STATE_OVERRIDE,
    Network,
)

CANONICAL_MULTICALL3 = "0xcA11bde05977b3631167028862bE2a173976CA11"


def test_robinhood_network_member_exists():
    assert Network.Robinhood == 4663


def test_robinhood_has_multicall3_address():
    assert MULTICALL3_ADDRESSES[Network.Robinhood] == CANONICAL_MULTICALL3


def test_robinhood_multicall3_address_is_checksummed():
    # EIP-55: the constant must be stored checksummed, never lowercased.
    from eth_utils import to_checksum_address

    stored = MULTICALL3_ADDRESSES[Network.Robinhood]
    assert stored == to_checksum_address(stored)


def test_robinhood_supports_state_override():
    # Verified live 2026-08-19: an eth_call with a `code` override returns the
    # overridden result, so Robinhood must NOT be in the deny-set.
    assert Network.Robinhood not in NO_STATE_OVERRIDE


def test_multicall_address_lookup_resolves_for_robinhood():
    # This is the lookup that raises KeyError today (multicall.py, multicall_map[chainid]).
    chainid = 4663
    multicall_map = MULTICALL3_ADDRESSES if chainid in MULTICALL3_ADDRESSES else None
    assert multicall_map is not None
    assert multicall_map[chainid] == CANONICAL_MULTICALL3
