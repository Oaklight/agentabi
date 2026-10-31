"""Vendored zerodep JSON-RPC module.

Source: https://github.com/Oaklight/zerodep/tree/master/jsonrpc
Update with: ``zerodep add acp -d src/agentabi/_vendor --nested``
Do not edit this package by hand.
"""

from .jsonrpc import *  # noqa: F401,F403
from .jsonrpc import __all__ as _jsonrpc_all

__all__ = list(_jsonrpc_all)
