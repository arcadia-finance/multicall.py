from typing import final

from web3.exceptions import Web3RPCError


@final
class StateOverrideNotSupported(Exception):
    pass


@final
class EVMTimeout(Web3RPCError):
    pass
