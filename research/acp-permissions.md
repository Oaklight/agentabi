# ACP Permission Forwarding: Design and PoC Plan

Status: design only. No `src/` change is included in this document's commit.

Issue: [Oaklight/agentabi#63](https://github.com/Oaklight/agentabi/issues/63) (epic [#43](https://github.com/Oaklight/agentabi/issues/43)).

Measured on 2026-11-10, Linux 6.18.59-1-lts, with:

| CLI | Version |
| --- | --- |
| `claude` | 2.1.299 |
| `codex` | codex-cli 0.162.1 |
| `agy` | 1.5.4 |
| `opencode` | 1.19.0 |

All probes call the real binary directly, not the shell wrapper functions. On
this machine `AGENT_YOLO=1` makes the `claude`/`codex`/`agy` shell functions
inject bypass flags (`--permission-mode bypassPermissions`,
`--dangerously-bypass-approvals-and-sandbox`, `--dangerously-skip-permissions`).
Any test harness must clear that variable or spawn the binary by path.

## 1. Decision summary

| Question | Decision |
| --- | --- |
| Where does the round trip live? | A `PermissionBroker` owned by the gateway. Backends call `broker.ask()` from their own task and await a decision. |
| Canonical option ids | `allow_once`, `allow_always`, `reject_once`, `reject_always`. Extra backend choices use `x-<backend>-<slug>`. |
| Correlation id | Backend id when it has one (`tool_use_id` for Claude, `itemId` for Codex), else `perm_<uuid>`. Carried in ACP as `toolCall.toolCallId`. |
| `allow_always` implementation | Gateway-side policy cache. Do **not** rely on Claude `updatedPermissions`; measured not to suppress the next prompt. |
| Default policy | `ask` where the backend can round-trip; `deny` where it cannot; `allow_all` only when the user explicitly selects it. |
| ACP capability advertisement | Do not invent one. ACP v1/v2 `agentCapabilities` has no permission field. Gate at backend construction and expose `permission_mode` as a session config option. |
| PoC backend | Claude Code (`claude_native` + `IRBackend` + `GatewayAgent`). Codex app-server is the second step; agy print mode stays deny. |

## 2. Current state

The gateway advertises `supports_permissions` per provider, but nothing emits
or answers a permission request.

| Location | Fact |
| --- | --- |
| `src/agentabi/acp/gateway.py:73-81` | `on_prompt` only forwards backend updates and then reads the prompt result. No back-channel exists. |
| `src/agentabi/acp/backends/ir.py:115-167` | `_to_acp_update` returns `None` for `permission_request`/`permission_response`. The events are silently dropped. |
| `src/agentabi/acp/backends/ir.py:256-295` | `pump()` pushes IR events into a queue and never writes to the provider. |
| `src/agentabi/acp/backends/acp_server.py:39-41` | `ACPBackend` constructs `ACPClient` but never calls `set_request_handler`. |
| `src/agentabi/_vendor/acp/acp.py:2607-2635` | Without a handler, an incoming request is answered with `METHOD_NOT_FOUND`. So `opencode acp` and `agy acp` currently receive an error for `session/request_permission`. |
| `src/agentabi/_vendor/acp/acp.py:3342-3405` | `ACPAgent.request_permission()` exists and sends the correct v1/v2 payload, but has no timeout and no cancel hook. |
| `src/agentabi/_vendor/acp/acp.py:3551-3558` | `_pending_requests` is resolved only by an incoming response. `session/cancel` does not touch it. |
| `src/agentabi/types/ir/events.py:160-178` | `PermissionRequestEvent` has no request id, options, or backend payload; `PermissionResponseEvent` only has `granted`. |
| `src/agentabi/providers/claude_native.py:104`, `codex_native.py:113`, `agy_native.py:76` | `supports_permissions: True` means "the CLI has permission flags", not "a request can be round-tripped". |

## 3. Measured backend behavior

### 3.1 Claude Code 2.1.299

**Mechanism: SDK control protocol over stdin/stdout.** This is the only path
that carries a permission request. `--permission-prompt-tool` must be `stdio`
for the control-protocol path; an MCP tool name is rejected at runtime
(`Error: tool <x> (passed via --permission-prompt-tool) must be an MCP tool`).

Working invocation:

```text
claude --print --input-format stream-json \
       --output-format stream-json --verbose --include-partial-messages \
       --permission-mode manual \
       --permission-prompt-tool stdio --permission-prompts host
```

`--permission-prompts host` is not required when `--permission-prompt-tool stdio`
is present; both forms produced a request. `--include-partial-messages` is
compatible with the control protocol.

Handshake:

1. The client may send `{"type":"control_request","request_id":"r1",
   "request":{"subtype":"initialize","hooks":null}}` and receives
   `{"type":"control_response","response":{"subtype":"success",...}}`.
   The handshake is optional: the permission flow worked without it.
2. The prompt must arrive on **stdin** as
   `{"type":"user","message":{"role":"user","content":[{"type":"text","text":"..."}]}}`.
   An argv prompt with `--input-format stream-json` does not work: the tool call
   fails with `Tool permission request failed: AbortError: Stream closed`.

Permission request (verbatim shape, abbreviated):

```json
{"type":"control_request","request_id":"...","request":{
  "subtype":"can_use_tool",
  "tool_name":"Bash",
  "display_name":"Bash",
  "input":{"command":"touch marker1","description":"Create empty file marker1"},
  "description":"Create empty file marker1",
  "permission_suggestions":[
    {"type":"addRules","rules":[{"toolName":"Bash","ruleContent":"touch marker1 *"}],
     "behavior":"allow","destination":"localSettings"},
    {"type":"addDirectories","directories":["/work"],"destination":"session"},
    {"type":"setMode","mode":"acceptEdits","destination":"session"}],
  "blocked_path":"/work/marker1",
  "decision_reason":"Claude requested permissions to edit /work/marker1 ...",
  "tool_use_id":"call_00_..."}}
```

Answer:

```json
{"type":"control_response","response":{"subtype":"success","request_id":"...",
 "response":{"behavior":"allow","updatedInput":{...}}}}
{"type":"control_response","response":{"subtype":"success","request_id":"...",
 "response":{"behavior":"deny","message":"denied by driver","interrupt":false}}}
```

Measured outcomes:

| Probe | Result |
| --- | --- |
| allow | Command ran; marker file created. |
| deny | Command did not run; tool result `denied by test harness`, `is_error: true`; `permission_denials` lists the tool. |
| deny + `interrupt: true` | Turn ends immediately: `result` subtype `error_during_execution`. |
| No answer | CLI stays alive and waits (observed 20 s). No built-in timeout. |
| `interrupt` control request while pending | CLI emits `control_cancel_request` for the pending request, then `result` subtype `error_during_execution`. |
| `permission_suggestions` forwarded as `updatedPermissions` | The suggested `addRules` rule was written to `<cwd>/.claude/settings.local.json`, but the **next identical command still produced a new request**. Do not use this for `allow_always`. |

Permission modes, in a neutral directory, with the control protocol attached:

| `--permission-mode` | Request emitted? | Command runs? |
| --- | --- | --- |
| `manual` | yes | yes, after allow |
| `default` | yes | yes, after allow |
| `acceptEdits` | no | yes (auto-approved) |
| `dontAsk` | no | no (`permission_denials` populated) |
| `bypassPermissions` | no | yes |

Print mode without a prompt tool (the current provider): the command is
auto-denied and `result.permission_denials` is populated. This is the current
behavior of `agentabi acp --agent claude` for any tool that would prompt.

### 3.2 Codex 0.162.1

**`codex exec --json` has no approval path.** `approval_policy = "untrusted"` is
rejected at startup (`no longer supported; remove this setting`). With
`approval_policy = "on-request"` and `-s read-only`, the model receives a
sandbox denial and reports it; no approval event is emitted.

**`codex app-server` does round-trip.** JSON-RPC over stdio
(`codex app-server --listen stdio://`):

1. `initialize {clientInfo, capabilities}`
2. `thread/start {cwd, approvalPolicy, sandbox}`
3. `turn/start {threadId, input:[{type:"text",text,text_elements:[]}], sandboxPolicy?}`

Server request:

```json
{"jsonrpc":"2.0","id":0,"method":"item/commandExecution/requestApproval","params":{
  "kind":"command","threadId":"...","turnId":"...","itemId":"chatcmpl-tool-...",
  "startedAtMs":1794304136464,"environmentId":"local",
  "command":"/usr/bin/bash -lc 'touch marker_codex3'","cwd":"/work",
  "commandActions":[{"type":"unknown","command":"touch marker_codex3"}],
  "proposedExecpolicyAmendment":["touch","marker_codex3"],
  "availableDecisions":["accept",{"acceptWithExecpolicyAmendment":{"execpolicy_amendment":["touch","marker_codex3"]}},"cancel"]}}
```

Answer: `{"decision":"accept" | "acceptForSession" | "decline" | "cancel"}`.
`availableDecisions` is authoritative: the server lists the decisions it will
accept for that request, and the list varies. The probe above offered
`accept`, `acceptWithExecpolicyAmendment`, and `cancel`, but not
`acceptForSession`.

Measured outcomes:

| Probe | Result |
| --- | --- |
| `approvalPolicy: on-request`, `sandbox: workspace-write`, command inside workspace | No request; command runs. |
| `approvalPolicy: on-request`, turn `sandboxPolicy: readOnly` | Request arrives with `reason: "The sandbox is read-only ... Allow?"`; `accept` runs the command outside the sandbox. |
| `decision: accept` | Command ran; marker created. |
| `decision: cancel` | Turn ends with `turn/completed` and `status: "interrupted"`. |
| `turn/interrupt` while an approval is pending | **No `turn/completed` within 40 s**; the process stays alive. The gateway must answer the approval, not just interrupt. |

`item/started` for the command carries the same `itemId` as the approval
request, so the ACP `toolCallId` is available before the prompt.

Other approval methods exist with separate schemas:
`item/fileChange/requestApproval`, `item/permissions/requestApproval`,
`item/tool/requestUserInput`. Legacy `execCommandApproval` and
`applyPatchApproval` are still in `ServerRequest`.

### 3.3 agy 1.5.4

**Print mode (`-p --output-format stream-json`) cannot ask.** Without
`--dangerously-skip-permissions`, a tool that needs approval is auto-denied:

- tool step `state: "ERROR"`, `error.type: "TOOL_ERROR"`,
  `message: "permission check failed ... user denied permission"`;
- `result.denied_actions: [{"action":"command","display_name":"RunCommand"}]`;
- stderr explains that headless mode cannot prompt.

**Native `agy acp` does round-trip.** With
`--dangerously-skip-permissions=false`:

```json
{"sessionId":"...","toolCall":{"sessionUpdate":"tool_call","toolCallId":"call_00_...",
 "name":"run_command","args":{"CommandLine":"touch marker"},"status":"pending",
 "kind":"execute","title":"touch marker","rawInput":{...}},
 "options":[{"optionId":"allow","name":"Allow","kind":"allow_once"},
            {"optionId":"deny","name":"Deny","kind":"reject_once"}]}
```

Measured outcomes:

| Client answer | Result |
| --- | --- |
| `{"outcome":{"outcome":"selected","optionId":"allow"}}` | Command ran; marker created. |
| `optionId: "allow_once"` (not the offered id) | Command did **not** run; turn ends `end_turn`. Option ids must be echoed exactly. |
| `{"outcome":{"outcome":"cancelled"}}` | Command did not run; turn ends `end_turn`. |
| `session/cancel` while pending, request left unanswered | `session/prompt` returns `stopReason: "cancelled"`; command did not run. |

### 3.4 OpenCode 1.19.0

Native ACP. Non-interactive default auto-allows. With
`OPENCODE_CONFIG_CONTENT={"permission":{"bash":"ask"}}`:

```json
{"sessionId":"...","toolCall":{"toolCallId":"call_00_...","title":"touch marker",
 "kind":"execute","status":"pending","locations":[],"rawInput":{"command":"touch marker"}},
 "options":[{"optionId":"once","kind":"allow_once","name":"Allow once"},
            {"optionId":"always","kind":"allow_always","name":"Always allow"},
            {"optionId":"reject","kind":"reject_once","name":"Reject"}]}
```

Option ids differ from agy (`once`/`always`/`reject` vs `allow`/`deny`). This is
the argument for passing the child's ids through unchanged in the ACP
pass-through backend, and for canonical ids only in IR backends.

### 3.5 Summary matrix

| Backend | Runtime request | Correlation id | Allow | Deny | Cancel |
| --- | --- | --- | --- | --- | --- |
| Claude `--print` + stdio control | `control_request` / `can_use_tool` | `tool_use_id` | `behavior: allow` | `behavior: deny` | `interrupt` control request |
| Codex `app-server` | `item/commandExecution/requestApproval` | `itemId` | `decision: accept` | `decision: decline` | `decision: cancel` |
| agy `acp` | `session/request_permission` | `toolCall.toolCallId` | echo `optionId` | echo reject id | `session/cancel`, then outcome `cancelled` |
| opencode `acp` | `session/request_permission` | `toolCall.toolCallId` | echo `optionId` | echo reject id | `session/cancel` |
| Claude print (no tool) | none | — | — | auto-deny | — |
| Codex `exec --json` | none | — | — | sandbox deny | — |
| agy print | none | — | — | auto-deny | — |
| pi | none (`supports_permissions: False`) | — | — | — | — |

## 4. IR mapping

### 4.1 Event fields

Extend the two TypedDicts in `src/agentabi/types/ir/events.py` with
`NotRequired` fields so existing providers stay valid.

`PermissionRequestEvent`:

```python
class PermissionRequestEvent(TypedDict):
    type: Required[Literal["permission_request"]]
    request_id: Required[str]          # correlation id (see 4.3)
    tool_name: Required[str]
    tool_use_id: Required[str]
    tool_input: NotRequired[dict[str, Any]]
    description: NotRequired[str]
    options: NotRequired[list[PermissionOption]]   # new
    suggestions: NotRequired[list[dict[str, Any]]] # backend-native, opaque
    raw: NotRequired[dict[str, Any]]               # backend-native request
```

`PermissionResponseEvent`:

```python
class PermissionResponseEvent(TypedDict):
    type: Required[Literal["permission_response"]]
    request_id: Required[str]          # new
    tool_use_id: Required[str]
    option_id: NotRequired[str]        # new; absent when outcome == "cancelled"
    outcome: Required[Literal["selected", "cancelled"]]  # new
    granted: Required[bool]            # kept: kind.startswith("allow")
```

New type in `src/agentabi/types/ir/permissions.py`:

```python
class PermissionOption(TypedDict):
    option_id: Required[str]
    name: Required[str]
    kind: Required[Literal["allow_once", "allow_always", "reject_once", "reject_always"]]
```

### 4.2 Option ids

Canonical ids equal the ACP `PermissionOptionKind` values:

| id | kind | meaning |
| --- | --- | --- |
| `allow_once` | `allow_once` | run this call |
| `allow_always` | `allow_always` | run this and matching later calls in the session |
| `reject_once` | `reject_once` | do not run this call; the turn continues |
| `reject_always` | `reject_always` | do not run this call, and deny matching later calls in the session |

Rules:

- IR backends emit the canonical ids. The broker maps `option_id` to the
  backend's native answer with a per-request table, so no client-visible id
  depends on backend syntax.
- A backend-specific choice (for example Codex
  `acceptWithExecpolicyAmendment`) gets `x-<backend>-<slug>` and is only offered
  when the backend lists it in `availableDecisions`.
- ACP pass-through backends keep the child's ids (`allow`/`deny`,
  `once`/`always`/`reject`). The client must echo the id it was given; the
  `kind` field carries the portable meaning.

### 4.3 Correlation id

`request_id` is the key the broker uses to match a decision to a pending
request.

- Use the backend id when it exists: Claude `tool_use_id`, Codex `itemId`.
- Otherwise mint `perm_<uuid4hex>`.
- The same value is sent to the ACP client as `toolCall.toolCallId`, so the
  prompt attaches to the tool call the client already rendered.
- Pending requests are keyed by `(session_id, request_id)`. A session may have
  at most one pending request in the PoC; a second request from the same
  backend while one is pending is queued behind the first.

### 4.4 Per-backend mapping

| Backend | Native request | IR `request_id` | IR `tool_name` | IR `tool_input` | Options |
| --- | --- | --- | --- | --- | --- |
| Claude | `can_use_tool` | `tool_use_id` | `tool_name` | `input` | `allow_once`, `allow_always` (only if an `addRules` suggestion exists), `reject_once` |
| Codex | `commandExecution/requestApproval` | `itemId` | `command_execution` | `{"command": command, "cwd": cwd}` | `allow_once`, `allow_always`, `reject_once`, `reject_always` |
| Codex | `fileChange/requestApproval` | `itemId` | `file_change` | `{"grantRoot": ...}` | `allow_once`, `allow_always`, `reject_once`, `reject_always` |
| agy / opencode (pass-through) | `session/request_permission` | `toolCall.toolCallId` | `toolCall.title`/`name` | `toolCall.rawInput` | child's `options` unchanged |

Answer mapping:

| IR `option_id` | Claude | Codex | agy/opencode |
| --- | --- | --- | --- |
| `allow_once` | `{"behavior":"allow","updatedInput":input}` | `{"decision":"accept"}` | child id with kind `allow_once` |
| `allow_always` | same as `allow_once`; broker caches the rule | `{"decision":"acceptForSession"}` when offered, else `accept` + broker cache | child id with kind `allow_always`, else broker cache |
| `reject_once` | `{"behavior":"deny","message":"..."}` | `{"decision":"decline"}` | child id with kind `reject_once` |
| `reject_always` | deny + broker caches a deny rule | `{"decision":"decline"}` + broker deny cache | child id with kind `reject_always`, else broker deny cache |
| outcome `cancelled` | send `interrupt` control request | `{"decision":"cancel"}` | `session/cancel` to child |

`reject_*` never ends the turn; only `cancelled` does. Codex `decision: cancel`
is therefore reserved for ACP `session/cancel` and is not used for
`reject_always`.

`allow_always` is implemented in the broker, not in the backend:

- Claude: cache `(session_id, tool_name, command_prefix)` from the request.
  Later matching requests are answered `allow` without a client round trip.
  The measured reason: forwarding `updatedPermissions` wrote the rule to
  `.claude/settings.local.json` and still prompted on the next identical call.
- Codex: prefer the native `acceptForSession` decision; also cache, because the
  session scope ends with the app-server process.
- agy/opencode: prefer the child's `allow_always` id; also cache.

## 5. Gateway flow

### 5.1 Components

```text
ACP client
   │  session/request_permission (request, waits)
   ▼
GatewayAgent ── PermissionBroker ── Backend
   ▲                │                  │
   │  session/update (tool_call_update, status=pending)
   └────────────────┴──────────────────┘
```

`PermissionBroker` (new module `src/agentabi/acp/permissions.py`):

```python
class PermissionBroker:
    async def ask(self, session_id: str, request: dict) -> PermissionDecision: ...
    def cancel(self, session_id: str) -> None: ...
    def allow_always(self, session_id: str, tool_name: str, rule: str | None) -> None: ...
```

`PermissionDecision` is `{"option_id": str | None, "outcome": "selected" | "cancelled"}`.

The broker is created by `serve()` and injected into the backend before
`GatewayAgent.run()`:

```python
agent = GatewayAgent(backend)
backend.set_permission_broker(agent)   # agent implements ask/cancel
await agent.run()
```

`Backend` gains two optional members. Protocols cannot carry defaults, so the
gateway calls them through `getattr` and treats a missing method as "this
backend cannot ask":

```python
def set_permission_broker(self, broker: PermissionBroker) -> None: ...
async def answer_permission(self, session_id: str, request_id: str,
                            decision: PermissionDecision) -> None: ...
```

### 5.2 Sequence

1. Backend receives a native permission request inside its prompt task.
2. Backend translates it to a `PermissionRequestEvent` (section 4) and calls
   `await broker.ask(session_id, request)`.
3. Broker:
   a. checks the session policy cache for `allow_always`/`reject_always`; if it
      hits, answers immediately and skips the client;
   b. otherwise registers `(session_id, request_id)` as pending;
   c. calls `GatewayAgent.request_permission(session_id, tool_call, options)`
      (the vendored helper, `acp.py:3342`), which sends
      `session/request_permission` and awaits the JSON-RPC response.
4. Client answers with `{"outcome":{"outcome":"selected","optionId":"allow_once"}}`
   or `{"outcome":{"outcome":"cancelled"}}`.
5. Broker maps `optionId` to a `PermissionDecision` and returns it to the
   backend.
6. Backend writes the native answer (control response, Codex decision, or ACP
   result) and continues the turn.
7. Backend emits `permission_response` for observability. The IR backend records
   it and does not emit a separate ACP update; the `tool_call_update` that
   follows carries the outcome (status `in_progress` or `failed`).

The vendored helper already handles the v1/v2 wire difference
(`acp.py:3372-3392`): v1 sends `{"sessionId","toolCall","options"}`, v2 sends
`{"sessionId","title","description","options","subject"}`. The gateway passes
the same `tool_call` dict to both.

The ACP `toolCall` payload sent to the client:

```json
{"sessionId":"s1",
 "toolCall":{"sessionUpdate":"tool_call_update","toolCallId":"call_00_...",
             "title":"Bash","kind":"execute","status":"pending",
             "rawInput":{"command":"touch marker1"}},
 "options":[{"optionId":"allow_once","name":"Allow once","kind":"allow_once"},
            {"optionId":"reject_once","name":"Reject","kind":"reject_once"}]}
```

Use `tool_call_update`, not `tool_call`: ACP v2 removed `tool_call`, and
`tool_call_update` is an upsert that creates the call on first sight (the same
rule `ir.py:140-151` already follows).

### 5.3 Pass-through backends

`ACPBackend` registers a handler once, in `start()`:

```python
self._client.set_request_handler(self._on_child_request)

async def _on_child_request(self, method, params):
    if method != "session/request_permission":
        raise JSONRPCException(JSONRPCError(code=METHOD_NOT_FOUND, ...))
    decision = await self._broker.ask(params["sessionId"], _from_acp(params))
    if decision["outcome"] == "cancelled":
        return {"outcome": {"outcome": "cancelled"}}
    return {"outcome": {"outcome": "selected", "optionId": decision["option_id"]}}
```

This is the smallest change that makes `opencode` and `agy` stop returning
`METHOD_NOT_FOUND` to their child process.

## 6. Timeout, cancel, and disconnect

| Event | Gateway action | Backend action | Client-visible result |
| --- | --- | --- | --- |
| Client does not answer | After `--permission-timeout N` seconds (default: no timeout), auto-deny once | Same as `reject_once` | Tool result is a denial; turn continues |
| `session/cancel` while pending | Resolve the ACP request locally as `cancelled`; ignore a late client answer | Claude: `interrupt`; Codex: `decision: cancel`; ACP child: `session/cancel` + outcome `cancelled` | `session/prompt` returns `stopReason: "cancelled"` |
| Client stdin EOF / disconnect | Stop all backends | Child killed | None |
| Backend exits while pending | Resolve as `reject_once` | — | Turn ends with an error event |
| Backend `task.timeout` fires | Resolve as `cancelled` | Child killed | `stopReason: "cancelled"` |

Timeout default is "no timeout" because the ACP client is the human-facing
surface, and Zed-like clients keep the prompt open. A non-zero default would
turn a slow user into a denial. Deployments that need a bound (headless CI) set
`--permission-timeout`.

Cancel ordering matters per backend:

- **Claude**: send `interrupt` first. The CLI then emits
  `control_cancel_request` for the pending request and ends the turn with
  `error_during_execution`; the provider maps that to `StopReason.CANCELLED`
  when a cancel was requested. Do not wait for the client.
- **Codex**: answer the pending approval with `{"decision":"cancel"}`.
  `turn/interrupt` alone left the turn hanging for 40 s in the probe.
- **agy/opencode**: send `session/cancel`; the child resolves its own pending
  request (verified for agy: `stopReason: cancelled`). Answer the child request
  with `{"outcome":{"outcome":"cancelled"}}` best-effort and ignore an
  "unknown request id" error.

## 7. Capability declaration

### 7.1 What the IR flag means

Redefine `supports_permissions` in `AgentCapabilities`:

> The provider can emit a permission request during a turn and accept an answer
> for it.

Truth table after this work:

| Provider | `supports_permissions` | Why |
| --- | --- | --- |
| `claude_native` | `True` | stdio control protocol (verified) |
| `claude_sdk` | `True` | `can_use_tool` callback; SDK source requires streaming input (not run in this probe) |
| `claude_tui` | `False` | unchanged; PTY has no structured channel |
| `codex_native` | `False` | `exec --json` has no approval path |
| `codex_sdk` | `False` | the SDK wraps `exec`; no approval callback in this provider |
| `agy_native` | `False` | print mode auto-denies |
| `pi_native` | `False` | unchanged |
| `opencode_native` | `False` | the `run` subcommand auto-approves; permission fidelity needs the ACP backend |
| new `codex_app_server` backend | `True` | `item/*/requestApproval` (verified) |
| `ACPBackend` (agy/opencode child) | `True` | child speaks ACP (verified) |

### 7.2 What the gateway advertises

ACP v1 and v2 `agentCapabilities` (`acp.py:817-833`, `2316-2351`) have no
permission field. Do not add a non-standard one. Instead:

1. **Gate at construction.** `IRBackend` receives `permission_handler=None`
   when the resolved provider declares `supports_permissions: False`. Such a
   backend never emits `permission_request`; it must run with a static policy.
2. **Expose a policy knob.** `session/new` returns a `ConfigOption`
   `permission_mode` with values `ask` (default), `deny`, `allow_all` for
   backends that can ask, and `deny`/`allow_all` for backends that cannot.
   `session/set_config_option` changes it for the session.
3. **Document per agent.** README and `agentabi acp --help` list which agents
   can ask.

A router that fronts several agents must advertise the intersection of what the
selected backend can do; with one endpoint per agent (current CLI shape) this is
not an issue.

### 7.3 Default policy

- Backend can ask: `ask`.
- Backend cannot ask: `deny`. The static mode passed to the CLI is the
  restrictive one:
  - Claude: `--permission-mode default` (print mode then auto-denies; verified).
  - Codex: `-s read-only` with `approval_policy = never`, **not**
    `--approve-for-me`/`--full-auto`.
  - agy: no `--dangerously-skip-permissions` (print mode then auto-denies).
- `allow_all` is reachable only by an explicit client choice
  (`session/set_config_option`) or an explicit CLI flag. It is never the
  default and never implied by a missing capability.

The current Codex provider violates this: `_auto_approval_flags()` returns
`--approve-for-me`/`--full-auto` by default (`codex_native.py:47-76`). The PoC
keeps that behavior for the plain `Session` API and changes it only for the ACP
gateway path, where the default becomes read-only.

## 8. PoC plan: Claude backend

Scope: one backend end to end, with unit tests and one opt-in CLI smoke test.
No Codex work in this PoC.

### 8.1 Files and changes

| File | Change |
| --- | --- |
| `src/agentabi/types/ir/permissions.py` | Add `PermissionOption`; add `PermissionDecision` helper type. |
| `src/agentabi/types/ir/events.py` | Extend `PermissionRequestEvent` / `PermissionResponseEvent` (section 4.1); update `__all__` if a new type is added. |
| `src/agentabi/providers/base.py` | Add a separate `PermissionAwareProvider` runtime-checkable protocol with `set_permission_handler()` and a `PermissionHandler` callable type. Do not add the method to `Provider`; existing providers must not be forced to implement it. |
| `src/agentabi/providers/claude_native.py` | Control-protocol transport (8.2). |
| `src/agentabi/acp/permissions.py` | New: `PermissionBroker`, policy cache, timeout, cancel. |
| `src/agentabi/acp/backends/base.py` | Add `set_permission_broker`, `answer_permission` to the `Backend` protocol. |
| `src/agentabi/acp/backends/ir.py` | Pass a handler into the provider; map `permission_request` to `broker.ask()`; answer the provider; keep `_to_acp_update` free of permission events. |
| `src/agentabi/acp/gateway.py` | Create the broker in `serve()`; `on_cancel` resolves pending requests; `on_initialize`/`on_new_session` add the `permission_mode` config option. |
| `src/agentabi/acp/backends/acp_server.py` | Register `set_request_handler` for `session/request_permission` (section 5.3). |
| `src/agentabi/cli.py` | Add `--permission-timeout` (default `0` = wait) and `--permission-policy ask|deny|allow_all` to `agentabi acp`. |

### 8.2 Claude provider changes

`ClaudeNativeProvider.stream()` currently uses `stdin=DEVNULL` and an argv
prompt (`claude_native.py:123-130`, `236-237`). When a permission handler is
attached:

1. Build the command with `--input-format stream-json`,
   `--permission-prompt-tool stdio`, `--permission-prompts host`, and without
   the trailing `-- <prompt>`.
2. Open `stdin=PIPE` and write the prompt as a `user` message after the process
   starts.
3. In the stdout loop, dispatch by `type` before `_parse_event`:
   - `control_request` with `request.subtype == "can_use_tool"`: build a
     `PermissionRequestEvent`, `await handler(request)`, write
     `control_response`.
   - `control_cancel_request`: resolve the pending request as `cancelled`.
   - `control_response`: ignore (the provider sends no control requests unless
     the initialize handshake is added).
4. Keep the argv-prompt path unchanged when no handler is attached, so the
   plain `Session` API is unaffected.
5. On cancel, write
   `{"type":"control_request","request_id":...,"request":{"subtype":"interrupt"}}`
   and map the resulting `error_during_execution` result to
   `stop_reason: "cancelled"` when a cancel was requested.

Option construction from `permission_suggestions`:

- Always offer `allow_once` and `reject_once`.
- Offer `allow_always` only when at least one suggestion has
  `type == "addRules"` and `behavior == "allow"`; use the rule as the cache key.
- Never forward `updatedPermissions` (measured not to suppress the next
  prompt, and it writes settings files as a side effect).

### 8.3 Tests

New `tests/acp/test_permissions.py` (unit, no CLI):

| Test | Setup | Assertion |
| --- | --- | --- |
| request reaches the client | `FakeBackend` emits a permission request and awaits the broker; `FakeTransport` captures `session/request_permission` | method, `sessionId`, `toolCall.toolCallId`, option ids/kinds |
| allow | transport answers `allow_once` | backend receives `selected/allow_once`; turn continues |
| reject | transport answers `reject_once` | backend receives `selected/reject_once`; tool result is a denial |
| cancel | `on_cancel` while pending | broker resolves `cancelled`; the late client answer is ignored |
| timeout | `permission_timeout=0.1`, no answer | decision is `reject_once`; no hang |
| allow_always cache | second matching request | no second `session/request_permission` is sent |
| unsupported backend | provider with `supports_permissions: False` | backend never calls `broker.ask` |

New `tests/providers/test_claude_permissions.py` (unit, scripted child):

- A Python script that speaks the control protocol on stdin/stdout
  (initialize optional, `can_use_tool`, `control_response`), used as the
  `claude` binary through a patched command builder.
- Cases: allow, deny, no answer then interrupt, `control_cancel_request`.

New `tests/integration/test_claude_permissions.py` (opt-in, skipped without
`claude`):

- `pytest.mark.skipif(shutil.which("claude") is None)`.
- Prompt: `touch <tmp>/marker`. Allow → file exists. Deny → file absent and
  `permission_denials` non-empty.
- Spawn the binary with an environment that does not contain `AGENT_YOLO`. The
  shell function is not involved when the binary is spawned directly, but the
  variable is documented as a hazard for any test that goes through a shell.

Existing tests to update:

- `tests/acp/test_ir_backend.py`: add a permission event to the scripted
  stream; assert it does not become a `session/update` payload by itself.
- `tests/acp/test_gateway.py`: `FakeBackend` gains `set_permission_broker`;
  `on_cancel` test asserts pending resolution.
- `tests/acp/test_external_backend.py`: add a fake ACP server that sends
  `session/request_permission`; assert the child gets a `selected` outcome.

### 8.4 Acceptance

1. An ACP client receives `session/request_permission` for a Bash call from
   `agentabi acp --agent claude`; selecting `allow_once` creates the file.
2. Selecting `reject_once` leaves the file absent and the turn continues.
3. `session/cancel` during a pending request returns
   `stopReason: "cancelled"` within 20 s.
4. `--permission-timeout 1` with no client answer returns a denial, not a hang.
5. `agentabi acp --agent codex` still advertises the static policy and never
   sends `session/request_permission`.
6. `make test` and `make lint` pass; the integration test skips when `claude`
   is absent.

### 8.5 Risks

| Risk | Mitigation |
| --- | --- |
| Claude control protocol is undocumented and version-specific | Pin the tested version in the test skip message; fail closed (deny) if the handshake shape changes. |
| `--input-format stream-json` changes output timing (`--include-partial-messages` verified compatible) | Keep the argv path for non-permission runs; only the gateway path uses stdin. |
| A second permission request arrives while one is pending | Queue per session; document the serialization. |
| Client answers after cancel | Ignore unknown `request_id`; never send two responses. |
| Claude `updatedPermissions` writes user settings | Never forward suggestions as permissions; only use them as cache keys. |
| Codex interrupt hang | Answer the approval with `decision: cancel`; do not rely on `turn/interrupt`. |
| Pass-through child id mismatch | Echo the exact `optionId`; never synthesize ids for ACP children. |

## 9. Follow-ups

1. Codex app-server backend (`item/commandExecution/requestApproval`,
   `item/fileChange/requestApproval`, `item/permissions/requestApproval`).
   This also fixes `supports_permissions` for Codex in the gateway.
2. agy native ACP backend: replace `IRBackend("agy")` with `ACPBackend` when
   permission fidelity matters; print mode stays deny.
3. `permission_mode` config option wired to `session/set_config_option`.
4. Audit log for permission decisions (epic phase 6, "Audit/observability").
5. Multi-session pending map with per-session ordering guarantees.

## 10. Evidence appendix

Probe commands (abbreviated; all run from a scratch directory, not the repo):

```bash
# Claude: control protocol, allow
python3 drive.py    # sends initialize (optional) + user prompt; answers can_use_tool

# Claude: no answer, then interrupt
python3 drive_timeout.py

# Claude: deny with interrupt
python3 deny_interrupt.py

# Claude: permission modes in a neutral directory
for m in manual default acceptEdits dontAsk; do PMODE=$m python3 perm_mode.py; done

# Codex: app-server approval
python3 drive.py    # initialize -> thread/start -> turn/start -> answer requestApproval

# Codex: interrupt with a pending approval
python3 interrupt.py

# Codex: exec has no approval path
codex exec --json --skip-git-repo-check -c approval_policy='"untrusted"' -s read-only "touch /tmp/x"
codex exec --json --skip-git-repo-check -c approval_policy='"on-request"' -s read-only "touch /tmp/x"

# agy: print mode auto-denies
agy --output-format stream-json -p "Run exactly: touch marker"

# agy: native ACP permission round trip
python3 agy_drive.py   # answers optionId "allow"

# opencode: native ACP permission round trip
OPENCODE_CONFIG_CONTENT='{"permission":{"bash":"ask"}}' python3 oc_drive.py
```

Observed payloads are quoted in sections 3.1 to 3.4. Raw probe scripts and
logs were kept outside the repository (job scratch directory) because they
contain local paths and model output.
