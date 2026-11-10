"""Pi settings adapter.

Pi has no endpoint flag and ignores ``OPENAI_BASE_URL``; a custom endpoint
only works through a ``models.json`` entry in Pi's agent directory.  When
that is needed the adapter materializes a temporary agent directory for the
duration of the run.
"""

from __future__ import annotations

from ..base import Adapter


class PiSettings(Adapter):
    """Settings are applied through a run-scoped agent directory."""

    name = "pi_native"


__all__ = ["PiSettings"]
