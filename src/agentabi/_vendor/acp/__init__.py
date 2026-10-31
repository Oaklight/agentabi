"""Vendored zerodep ACP module.

Source: https://github.com/Oaklight/zerodep/tree/master/acp
Update with: ``zerodep add acp -d src/agentabi/_vendor --nested``
Do not edit this package by hand.
"""

from .acp import *  # noqa: F401,F403
from .acp import __all__ as _acp_all

__all__ = list(_acp_all)
