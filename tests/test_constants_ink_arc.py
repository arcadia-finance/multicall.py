import pytest
from eth_utils import to_checksum_address

from multicall.constants import (
    MULTICALL3_ADDRESSES,
    NO_STATE_OVERRIDE,
    Network,
)

CANONICAL_MULTICALL3 = "0xcA11bde05977b3631167028862bE2a173976CA11"

CHAINS = [(Network.Arc, 5042), (Network.Ink, 57073)]


@pytest.mark.parametrize(("network", "chain_id"), CHAINS)
def test_network_member_exists(network, chain_id):
    assert network == chain_id


@pytest.mark.parametrize(("network", "chain_id"), CHAINS)
def test_has_checksummed_multicall3_address(network, chain_id):
    # Multicall3 is a genesis predeploy on both chains, with the canonical runtime code
    # (verified on chain 2026-10-28: same keccak as Base, aggregate3([]) answers).
    stored = MULTICALL3_ADDRESSES[chain_id]
    assert stored == CANONICAL_MULTICALL3
    assert stored == to_checksum_address(stored)


@pytest.mark.parametrize(("network", "chain_id"), CHAINS)
def test_supports_state_override(network, chain_id):
    # Verified live 2026-10-28: an eth_call with a `code` override returns the overridden
    # result on both chains, so neither belongs in the deny-set.
    assert network not in NO_STATE_OVERRIDE
