"""Minimal audit log for the ACP gateway.

Each record answers: which method, which agent, which ACP session, how long
it took, and whether it succeeded.  Records are kept in a bounded deque for
tests and status inspection, and written to the ``agentabi.acp.audit``
logger as one structured line.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from agentabi._vendor.acp import JSONRPCException

logger = logging.getLogger("agentabi.acp.audit")


@dataclass(frozen=True)
class AuditRecord:
    """One audited ACP method call.

    Attributes:
        method: ACP method name (for example ``session/prompt``).
        agent: Selected agent, or ``"router"`` for gateway-wide calls.
        session: ACP session id, when the method targets a session.
        duration_ms: Wall-clock duration in milliseconds.
        outcome: ``"ok"``, ``"error"``, or ``"cancelled"``.
        detail: Optional error or selection detail.
    """

    method: str
    agent: str | None
    session: str | None
    duration_ms: float
    outcome: str
    detail: str | None = None


class AuditLog:
    """Bounded in-memory audit log backed by a logger."""

    def __init__(self, maxlen: int = 200) -> None:
        """Create an audit log.

        Args:
            maxlen: Maximum number of records kept in memory.
        """
        self.records: deque[AuditRecord] = deque(maxlen=maxlen)

    def record(self, record: AuditRecord) -> None:
        """Store *record* and write it to the audit logger."""
        self.records.append(record)
        logger.info(
            "method=%s agent=%s session=%s duration_ms=%.1f outcome=%s detail=%s",
            record.method,
            record.agent,
            record.session,
            record.duration_ms,
            record.outcome,
            record.detail,
        )

    @contextmanager
    def span(
        self,
        method: str,
        *,
        agent: str | None = None,
        session: str | None = None,
    ) -> Iterator[None]:
        """Time a block and record its outcome.

        Args:
            method: ACP method name.
            agent: Selected agent.
            session: ACP session id.
        """
        started = time.perf_counter()
        outcome = "ok"
        detail: str | None = None
        try:
            yield
        except GeneratorExit:
            outcome = "cancelled"
            raise
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except JSONRPCException as exc:
            outcome = "error"
            detail = f"{exc.error.code}: {exc.error.message}"
            raise
        except Exception as exc:  # noqa: BLE001 - recorded then re-raised
            outcome = "error"
            detail = str(exc)
            raise
        finally:
            self.record(
                AuditRecord(
                    method=method,
                    agent=agent,
                    session=session,
                    duration_ms=(time.perf_counter() - started) * 1000.0,
                    outcome=outcome,
                    detail=detail,
                )
            )


__all__ = ["AuditLog", "AuditRecord"]
