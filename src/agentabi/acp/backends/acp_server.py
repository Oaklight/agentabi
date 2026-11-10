"""Backend for an external ACP server process.

Any CLI that implements ACP on stdio can be served by the gateway without
an agentabi provider. The gateway speaks ACP to the process and forwards
its ``session/update`` payloads to the client.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, cast

from agentabi._vendor.acp import (
    ACPClient,
    InitializeParams,
    InitializeResult,
    JSONRPCError,
    JSONRPCException,
    LoadSessionParams,
    NewSessionParams,
    NewSessionResult,
    PromptParams,
    PromptResult,
)
from agentabi._vendor.jsonrpc import METHOD_NOT_FOUND

from ...types.ir.events import PermissionRequestEvent
from ...types.ir.permissions import (
    PermissionDecision,
    PermissionOption,
    PermissionOptionKind,
)
from ..permissions import PermissionBroker, new_request_id
from .base import split_prompt_blocks


def _permission_request(params: dict[str, Any]) -> PermissionRequestEvent:
    """Translate a child ``session/request_permission`` to an IR request.

    The child's option ids are kept unchanged: a client must echo exactly
    what it was offered (measured with agy and opencode).
    """
    subject = params.get("subject")
    tool_call = params.get("toolCall")
    if not isinstance(tool_call, dict) and isinstance(subject, dict):
        nested = subject.get("toolCall")
        if isinstance(nested, dict):
            tool_call = nested
    if not isinstance(tool_call, dict):
        tool_call = {}

    tool_call_id = str(
        tool_call.get("toolCallId") or tool_call.get("tool_call_id") or ""
    )
    request_id = tool_call_id or new_request_id()
    tool_name = str(
        params.get("title") or tool_call.get("title") or tool_call.get("name") or "tool"
    )

    options: list[PermissionOption] = []
    raw_options = params.get("options")
    if isinstance(raw_options, list):
        for option in raw_options:
            if not isinstance(option, dict):
                continue
            option_id = option.get("optionId") or option.get("option_id")
            kind = option.get("kind")
            if not option_id or kind not in (
                "allow_once",
                "allow_always",
                "reject_once",
                "reject_always",
            ):
                continue
            options.append(
                {
                    "option_id": str(option_id),
                    "name": str(option.get("name") or option_id),
                    "kind": cast(PermissionOptionKind, str(kind)),
                }
            )

    request: PermissionRequestEvent = {
        "type": "permission_request",
        "request_id": request_id,
        "tool_name": tool_name,
        "tool_use_id": tool_call_id or request_id,
    }
    tool_input = tool_call.get("rawInput") or tool_call.get("raw_input")
    if isinstance(tool_input, dict):
        request["tool_input"] = tool_input
    description = params.get("description") or tool_call.get("title")
    if description:
        request["description"] = str(description)
    if options:
        request["options"] = options
    request["raw"] = params
    return request


class ACPBackend:
    """Backend that delegates to an external ACP server process."""

    #: The gateway answers downstream permission requests, so this backend
    #: does not silently inherit the downstream's policy.
    mediates_permissions = True

    #: The child speaks ACP, so it can round-trip a permission request.
    permission_capable = True

    def __init__(self, command: list[str], *, name: str = "acp") -> None:
        """Create a backend for *command*.

        Args:
            command: argv of the ACP server process.
            name: Backend name exposed by the registry.
        """
        self.name = name
        self._command = list(command)
        self._client = ACPClient(self._command)
        self._broker: PermissionBroker | None = None
        self._started = False
        self._prompt_results: dict[str, PromptResult] = {}

    def set_permission_broker(self, broker: PermissionBroker) -> None:
        """Attach the gateway's permission broker."""
        self._broker = broker

    async def answer_permission(
        self,
        session_id: str,
        request_id: str,
        decision: PermissionDecision,
    ) -> None:
        """No-op: the child request is answered inside its handler."""

    async def _handle_client_request(
        self, method: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Answer downstream agent requests.

        ``session/request_permission`` is routed through the broker when one
        is attached.  Without a broker it is denied explicitly (never
        allowed), keeping the outcome unambiguous for agents that treat an
        error as a transport failure.  Other methods are not proxied.
        """
        if method == "session/request_permission":
            broker = self._broker
            if broker is None:
                return {"outcome": {"outcome": "cancelled"}}
            decision = await broker.ask(
                str(params.get("sessionId", "")), _permission_request(params)
            )
            if decision["outcome"] == "cancelled" or not decision.get("option_id"):
                return {"outcome": {"outcome": "cancelled"}}
            return {
                "outcome": {
                    "outcome": "selected",
                    "optionId": decision["option_id"],
                }
            }
        raise JSONRPCException(
            JSONRPCError(
                code=METHOD_NOT_FOUND,
                message=f"the agentabi gateway does not proxy {method}",
            )
        )

    async def start(self) -> None:
        if not self._started:
            self._client.set_request_handler(self._handle_client_request)
            await self._client.start()
            self._started = True

    async def stop(self) -> None:
        if self._started:
            await self._client.stop()
            self._started = False

    async def initialize(self, params: InitializeParams) -> InitializeResult:
        await self.start()
        result = await self._client.initialize(params)
        # The gateway does not replay sessions yet, so do not advertise it.
        caps = result.agent_capabilities
        if caps is not None:
            caps.load_session = False
            if caps.session_capabilities is not None:
                caps.session_capabilities.resume = None
                caps.session_capabilities.delete = None
        return result

    async def new_session(self, params: NewSessionParams) -> NewSessionResult:
        await self.start()
        return await self._client.new_session(
            params.cwd,
            params.mcp_servers,
            params.additional_directories,
        )

    async def load_session(
        self, params: LoadSessionParams
    ) -> AsyncIterator[dict[str, Any]]:
        raise NotImplementedError("session replay is not implemented")
        yield {}  # pragma: no cover

    async def prompt(self, params: PromptParams) -> AsyncIterator[dict[str, Any]]:
        await self.start()
        self._prompt_results.pop(params.session_id, None)
        text, extra = split_prompt_blocks(params.prompt)
        async for raw in self._client.prompt(params.session_id, text, extra):
            yield raw.get("update", raw)
        result = self._client.prompt_result(params.session_id)
        if result is not None:
            self._prompt_results[params.session_id] = result

    def prompt_result(self, session_id: str) -> PromptResult | None:
        return self._prompt_results.get(session_id)

    def prompt_error(self, session_id: str) -> str | None:
        return None

    async def cancel(self, session_id: str) -> None:
        await self._client.cancel(session_id)

    async def close_session(self, session_id: str) -> None:
        self._prompt_results.pop(session_id, None)
        await self._client.close_session(session_id)


__all__ = ["ACPBackend"]
