"""Permission broker for the ACP gateway.

The broker sits between a backend (which receives a native permission
request) and the ACP client (which answers ``session/request_permission``).
It owns the policy cache, the timeout, cancellation, and the per-session
pending map.

Policy values:

``ask``
    Round-trip every request to the client (default where the backend can
    ask).
``deny``
    Answer every request with ``reject_once`` without contacting the client.
    This is the default where the backend cannot round-trip.
``allow_all``
    Answer every request with ``allow_once``.  Only reachable by an explicit
    client or CLI choice; never implied by a missing capability.

``allow_always`` and ``reject_always`` are implemented here, not in the
backend.  Claude's ``updatedPermissions`` is deliberately not used: it writes
``.claude/settings.local.json`` and was measured not to suppress the next
prompt.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from agentabi._vendor.acp import PermissionOption as ACPPermissionOption
from agentabi._vendor.acp import PermissionOptionKind

from ..types.ir.events import PermissionRequestEvent
from ..types.ir.permissions import PermissionDecision

POLICIES = frozenset({"ask", "deny", "allow_all"})

_CANONICAL_OPTIONS: list[dict[str, str]] = [
    {"option_id": "allow_once", "name": "Allow once", "kind": "allow_once"},
    {"option_id": "reject_once", "name": "Reject", "kind": "reject_once"},
]

_TOOL_KINDS: dict[str, str] = {
    "Bash": "execute",
    "Read": "read",
    "Write": "edit",
    "Edit": "edit",
    "MultiEdit": "edit",
    "NotebookEdit": "edit",
    "Glob": "search",
    "Grep": "search",
    "WebFetch": "fetch",
    "WebSearch": "fetch",
    "Task": "other",
}


class PermissionRequester(Protocol):
    """Object the broker uses to send an ACP permission request."""

    async def request_permission(
        self,
        session_id: str,
        tool_call: dict[str, Any],
        options: list[ACPPermissionOption],
    ) -> Any:
        """Send ``session/request_permission`` and return its outcome."""
        ...


@dataclass
class _Rule:
    """A cached allow/deny rule for one session."""

    tool_name: str
    prefix: str | None
    allow: bool


@dataclass
class _Pending:
    """One in-flight request for a session."""

    request: PermissionRequestEvent
    task: asyncio.Task[Any] | None = None
    cancel_requested: bool = False


def _decision(option_id: str) -> PermissionDecision:
    return {"option_id": option_id, "outcome": "selected"}


_CANCELLED: PermissionDecision = {"option_id": None, "outcome": "cancelled"}
_DENY: PermissionDecision = _decision("reject_once")


class PermissionBroker:
    """Route backend permission requests to the ACP client.

    Args:
        requester: Object with an async ``request_permission`` method
            (normally the ``GatewayAgent``).
        timeout: Seconds to wait for a client answer; ``0`` waits forever.
        policy: Initial policy (``ask``, ``deny``, or ``allow_all``).
    """

    def __init__(
        self,
        requester: PermissionRequester,
        *,
        timeout: float = 0.0,
        policy: str = "ask",
    ) -> None:
        if policy not in POLICIES:
            raise ValueError(f"unknown permission policy: {policy!r}")
        self._requester = requester
        self._timeout = float(timeout or 0.0)
        self._default_policy = policy
        self._policies: dict[str, str] = {}
        self._pending: dict[str, _Pending] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._rules: dict[str, list[_Rule]] = {}

    # -- policy -----------------------------------------------------------

    @property
    def default_policy(self) -> str:
        """Policy used for sessions without an explicit override."""
        return self._default_policy

    def policy_for(self, session_id: str) -> str:
        """Return the effective policy for *session_id*."""
        return self._policies.get(session_id, self._default_policy)

    def set_policy(self, session_id: str, policy: str) -> None:
        """Set the policy for *session_id*.

        Raises:
            ValueError: If *policy* is unknown.
        """
        if policy not in POLICIES:
            raise ValueError(f"unknown permission policy: {policy!r}")
        self._policies[session_id] = policy

    def forget_session(self, session_id: str) -> None:
        """Drop cached policy, rules, and pending state for a session."""
        self._policies.pop(session_id, None)
        self._rules.pop(session_id, None)
        self._locks.pop(session_id, None)
        self._pending.pop(session_id, None)

    # -- state ------------------------------------------------------------

    def has_pending(self, session_id: str) -> bool:
        """Whether *session_id* has an unanswered request."""
        return session_id in self._pending

    def allow_always(self, session_id: str, tool_name: str, rule: str | None) -> None:
        """Cache an allow rule for later matching requests in the session."""
        self._add_rule(session_id, tool_name, rule, allow=True)

    def deny_always(self, session_id: str, tool_name: str, rule: str | None) -> None:
        """Cache a deny rule for later matching requests in the session."""
        self._add_rule(session_id, tool_name, rule, allow=False)

    def _add_rule(
        self, session_id: str, tool_name: str, rule: str | None, *, allow: bool
    ) -> None:
        self._rules.setdefault(session_id, []).append(
            _Rule(tool_name=tool_name, prefix=_prefix(rule), allow=allow)
        )

    # -- request path -----------------------------------------------------

    async def ask(
        self, session_id: str, request: PermissionRequestEvent
    ) -> PermissionDecision:
        """Resolve one permission request.

        The request is answered from the policy cache or the configured
        policy when possible.  Otherwise it is sent to the client.  At most
        one request per session is in flight; a second request waits on the
        session lock (queueing is implicit).

        Args:
            session_id: ACP session the request belongs to.
            request: IR permission request.

        Returns:
            The decision to hand back to the backend.
        """
        policy = self.policy_for(session_id)
        if policy == "deny":
            return _DENY
        if policy == "allow_all":
            return _decision("allow_once")

        cached = self._cached(session_id, request)
        if cached is not None:
            return cached

        lock = self._locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            # Another ask may have populated the cache while we waited.
            cached = self._cached(session_id, request)
            if cached is not None:
                return cached

            pending = _Pending(request=request)
            self._pending[session_id] = pending
            task = asyncio.create_task(self._request(session_id, request))
            pending.task = task
            try:
                if self._timeout > 0:
                    outcome = await asyncio.wait_for(task, self._timeout)
                else:
                    outcome = await task
            except asyncio.TimeoutError:
                return _DENY
            except asyncio.CancelledError:
                if pending.cancel_requested:
                    return _CANCELLED
                raise
            finally:
                self._pending.pop(session_id, None)

            # A cancel that raced the answer wins: never hand a late allow
            # back to the backend after the client cancelled the turn.
            if pending.cancel_requested:
                return _CANCELLED
            decision = _to_decision(outcome)
            self._remember(session_id, request, decision)
            return decision

    def cancel(self, session_id: str) -> None:
        """Resolve a pending request for *session_id* as cancelled.

        A late client answer is ignored by the ACP agent's response routing
        (the request id is already removed from its pending map).
        """
        pending = self._pending.get(session_id)
        if pending is None:
            return
        pending.cancel_requested = True
        if pending.task is not None and not pending.task.done():
            pending.task.cancel()

    async def _request(self, session_id: str, request: PermissionRequestEvent) -> Any:
        return await self._requester.request_permission(
            session_id,
            _tool_call(request),
            _acp_options(request),
        )

    # -- cache ------------------------------------------------------------

    def _cached(
        self, session_id: str, request: PermissionRequestEvent
    ) -> PermissionDecision | None:
        rules = self._rules.get(session_id)
        if not rules:
            return None
        allow = False
        deny = False
        for rule in rules:
            if not _matches(rule, request):
                continue
            if rule.allow:
                allow = True
            else:
                deny = True
        if deny:  # deny wins over allow
            return _DENY
        if allow:
            return _decision("allow_once")
        return None

    def _remember(
        self,
        session_id: str,
        request: PermissionRequestEvent,
        decision: PermissionDecision,
    ) -> None:
        if decision["outcome"] != "selected":
            return
        option_id = decision["option_id"]
        tool_name = str(request.get("tool_name", ""))
        if option_id == "allow_always":
            self.allow_always(session_id, tool_name, _rule(request))
        elif option_id == "reject_always":
            self.deny_always(session_id, tool_name, _rule(request))


def _rule(request: PermissionRequestEvent) -> str | None:
    """Extract the allow rule from an ``addRules`` suggestion, if present."""
    suggestions = request.get("suggestions") or []
    for suggestion in suggestions:
        if not isinstance(suggestion, dict):
            continue
        if (
            suggestion.get("type") != "addRules"
            or suggestion.get("behavior") != "allow"
        ):
            continue
        rules = suggestion.get("rules")
        if isinstance(rules, list):
            for rule in rules:
                if isinstance(rule, dict) and rule.get("ruleContent"):
                    return str(rule["ruleContent"])
        return None  # addRules without content: tool-wide
    return None


def _prefix(rule: str | None) -> str | None:
    """Turn ``touch marker *`` into the literal prefix ``touch marker``."""
    if rule is None:
        return None
    text = rule.strip()
    if text.endswith(" *"):
        return text[:-2]
    if text.endswith("*"):
        return text[:-1].rstrip()
    return text


def _matches(rule: _Rule, request: PermissionRequestEvent) -> bool:
    if rule.tool_name != str(request.get("tool_name", "")):
        return False
    if rule.prefix is None or rule.prefix == "":
        return True
    tool_input = request.get("tool_input") or {}
    command = tool_input.get("command")
    if isinstance(command, str):
        return command == rule.prefix or command.startswith(rule.prefix)
    # Conservative: a content rule only auto-applies to a command prefix.
    return False


def _tool_call(request: PermissionRequestEvent) -> dict[str, Any]:
    """Build the ACP ``toolCall`` payload for the client prompt."""
    tool_name = str(request.get("tool_name", ""))
    tool_call: dict[str, Any] = {
        "sessionUpdate": "tool_call_update",
        "toolCallId": request.get("tool_use_id") or request.get("request_id", ""),
        "title": tool_name or "Permission required",
        "kind": _TOOL_KINDS.get(tool_name, "other"),
        "status": "pending",
    }
    tool_input = request.get("tool_input")
    if tool_input:
        tool_call["rawInput"] = tool_input
    return tool_call


def _acp_options(
    request: PermissionRequestEvent,
) -> list[ACPPermissionOption]:
    """Convert IR options to ACP option objects, preserving the given ids."""
    raw = request.get("options") or _CANONICAL_OPTIONS
    options: list[ACPPermissionOption] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            kind = PermissionOptionKind(str(item.get("kind", "")))
        except ValueError:
            continue
        option_id = str(item.get("option_id", ""))
        if not option_id:
            continue
        options.append(
            ACPPermissionOption(
                option_id=option_id,
                name=str(item.get("name") or option_id),
                kind=kind,
            )
        )
    if not options:
        options = [
            ACPPermissionOption(
                option_id=item["option_id"],
                name=item["name"],
                kind=PermissionOptionKind(item["kind"]),
            )
            for item in _CANONICAL_OPTIONS
        ]
    return options


def _to_decision(outcome: Any) -> PermissionDecision:
    """Map an ACP ``PermissionOutcome`` (or dict) to an IR decision."""
    if isinstance(outcome, dict):
        name = outcome.get("outcome")
        option_id = outcome.get("optionId") or outcome.get("option_id")
    else:
        name = getattr(outcome, "outcome", None)
        option_id = getattr(outcome, "option_id", None)
    if name == "selected" and option_id:
        return {"option_id": str(option_id), "outcome": "selected"}
    # A selected outcome without an id is malformed; fail closed.
    return _CANCELLED


def new_request_id() -> str:
    """Mint a correlation id for a backend request without one."""
    return f"perm_{uuid.uuid4().hex}"


__all__ = [
    "POLICIES",
    "PermissionBroker",
    "PermissionRequester",
    "new_request_id",
]
