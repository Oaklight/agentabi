# TUI dialogs: detection and safe response

> Status: research + design. No provider code changed.
> Issue: [#57](https://github.com/Oaklight/agentabi/issues/57) (epic #54).
> Target binary: Claude Code **2.1.299** (native build).
> Captures: real TUI on a PTY, isolated `HOME` + `CLAUDE_CONFIG_DIR`.

## 0. Decisions

1. **Detect dialogs from the PTY screen, not from the transcript alone.**
   The transcript gets the `tool_use` row before the dialog opens, but it
   never says "a dialog is on screen". The screen does.
2. **Anchor detection on the status bar.** A dialog footer that appears
   *after* the last TUI status bar is a live dialog. Anything above the
   status bar is scrollback, including assistant text that quotes a
   dialog verbatim. This removes the obvious false positive.
3. **Corroborate with the transcript.** The last assistant row must carry
   a `tool_use` with no matching `tool_result`. `AskUserQuestion` and
   `ExitPlanMode` always block; `Bash`/`Edit`/`Write` may block.
4. **Answer from `agent_extensions.tui_answers`.** Verified key sequences
   are in §4.1. The transcript's `tool_use.input` supplies the question,
   options, command, file, and plan text; do not scrape long prose from
   the screen.
5. **Fail fast when the answer is not configured.** Emit
   `ErrorEvent(is_fatal=True)` whose message starts with a stable
   `tui_dialog_unanswered: <type>:` prefix, then close the PTY. Never
   wait for the 600 s turn timeout.
6. **Keep `supports_permissions=False`.** The IR stream is one-way; it
   has no channel for a permission decision. Emitting
   `PermissionRequestEvent` now would promise a response path that does
   not exist. See §5.

## 1. Method

Environment for every run:

- `HOME` and `CLAUDE_CONFIG_DIR` point into a fresh scratch root.
- `HOME/.claude.json` and `CLAUDE_CONFIG_DIR/.claude.json` are seeded
  with `hasCompletedOnboarding=true`, `theme=dark`, and
  `projects[<work>].hasTrustDialogAccepted=true`. Without this, the first
  screen is the theme picker, whose `❯` marker already fools
  `_wait_ready`.
- `settings.json` carries only the gateway `env` block and an
  `apiKeyHelper` that resolves the key at runtime; no hooks, no
  `permissions.deny`, so Edit/Bash can prompt.
- The child runs `claude --no-chrome --session-id <uuid>` with
  `--permission-mode {default,plan}` when needed, on a 200x50 PTY.
- Prompt submission uses the provider's own bracketed paste:
  `\x1b[200~` + text + `\x1b[201~`, a short settle, then `\r`.
- Raw bytes and `strip_ansi()` text are captured per run. Screen stages
  are captured after each injected key.
- The model in these runs was `deepseek-v4.1-flash` through the gateway
  (`ANTHROPIC_BASE_URL`). The model decides whether to call
  `AskUserQuestion` / `ExitPlanMode`; the dialog rendering is Claude
  Code's and does not depend on the model. The gateway also produced an
  auto-mode billing notice (§2.6) that a direct Anthropic account may
  not show.

Prompts that reproduced each dialog:

| Dialog | Command | Prompt |
| --- | --- | --- |
| AskUserQuestion | default mode | "Call the AskUserQuestion tool right now ... options Red and Blue. Do not answer in text; use the tool and wait." |
| AskUserQuestion (multi) | default mode | same, with two questions (`Color`, `Size`) and `multiSelect: false` |
| Bash permission | `--permission-mode default` | "Use the Bash tool to run exactly: `rm -rf ./victim.txt`. Then stop." |
| Edit permission | `--permission-mode default` | "Use the Edit tool to replace ... `line one` with `line two` ..." |
| Plan approval | `--permission-mode plan` | "Plan ... Call ExitPlanMode right away; do not ask questions." |

`echo` and other low-risk Bash commands ran without a prompt in `default`
mode on this model, so the Bash capture uses a destructive command.

## 2. Captured screens

Text below is `strip_ansi()` output with runs of spaces and blank lines
collapsed. The TUI's cursor-position escapes become single spaces, so
words are separated; a few words are still clipped by overlapping
redraws (for example `ins ructed`). Detection must not depend on long
prose.

### 2.1 AskUserQuestion, one question

```
────────────────────────────────────────────────────────────────────────────
  ☐ Color
   Which color do you prefer?

  ❯   1.   Red
      Prefer red.

  2.  Blue
      Prefer blue.

  3. Type something.

────────────────────────────────────────────────────────────────────────────
 4. Chat about this

 Enter to select · ↑/↓ to navigate · Esc to cancel
```

### 2.2 AskUserQuestion, multiple questions

First question (header shows one checkbox per question and a submit arrow):

```
 ←    ☐ Color    ☐ Size ✔ Submit →
   Which color do you prefer?

  ❯   1.   Red
      Red

  2.  Blue
      Blue

  3. Type something.

────────────────────────────────────────────────────────────────────────────
 4. Chat about this

 Enter to select · Tab/Arrow keys to navigate · Esc to cancel
```

Review page after the last answer:

```
 ← ☒ Color ☒ Size    ✔ Submit    →

   Review your answers

  ● Which color do you prefer?
    → Red
  ● Which size do you prefer?
    → Small

  Ready to submit your answers?
  ❯   1.  Submit answers

  2.  Cancel
```

The transcript's `AskUserQuestion` input carries the full question list,
option labels, and `multiSelect`, for example:

```json
{"questions": [
  {"question": "Which color do you prefer?", "header": "Color",
   "options": [{"label": "Red"}, {"label": "Blue"}], "multiSelect": false},
  {"question": "Which size do you prefer?", "header": "Size",
   "options": [{"label": "Small"}, {"label": "Large"}], "multiSelect": false}
]}
```

### 2.3 Bash permission

```
  Bash command
   Tip: auto mode handles these prompts for you — choose "switch to auto mode" below
   Remove victim.txt as ins ructed
 ╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌
 rm -rf ./victim.txt
 ╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌

  │  Claude requested permissions to edit /.../victim.txt which is a sensitive file.

 Do you want to proceed?

  ❯  1.  Yes

  2.  Yes, and always allow access to /.../work from this project

  3.  Yes, and switch to auto mode  · auto mode handles these prompts for you

  4.  No

  Esc to cancel · Tab to amend
```

Option labels and their count vary with risk and history. The first capture
of the same command offered a "don't ask again for `rm -rf ./victim.txt`"
variant instead of "always allow access to directory". Resolve by semantic
label, not by fixed index (§4.2).

### 2.4 Edit permission

```
  Edit file
   notes.txt
 ╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌
  …
 ╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌

 Do you want to make this edit to  notes.txt ?

  ❯  1.  Yes

  2.  Yes, and switch to  accept edits (auto-approve file edits and common file commands)  for this session  (shift+tab)

  3.  No

  Esc to cancel · Tab to amend

   1 - line  one
   1 + line  two
```

### 2.5 Plan approval (`ExitPlanMode`)

```
────────────────────────────────────────────────────────────────────────────
    Ready to code?

    Here is Claude's plan:
  ╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌
      Plan: Append a line to notes.txt
      ...
  ╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌
  ──────────────────────────────────────────────────────────────────────────

  Claude has written up a plan and is ready to execute. Would you like to proceed?

  ❯  1.  Yes, and use auto mode

  2.  Yes, manually approve edits

  3. Tell Claude what to change

  shift+tab to approve with this feedback

  ctrl+g to edit in nano · /.../config/plans/<slug>.md
```

The transcript's `ExitPlanMode` input carries `plan` and `planFilePath`.

### 2.6 Other modals

An auto-mode billing notice appeared between the plan approval and the
next turn. It is a modal of the same shape and also blocks:

```
  We're changing auto mode to no longer charge for classifier requests ...
  Enter to continue · Esc to cancel
```

`--permission-mode bypassPermissions` opened a security confirmation
instead of a status bar:

```
  ❯ No, exit
    Yes, I accept
  Enter to confirm · Esc to cancel
```

Treat these as `notice` (safe to dismiss with Enter) or
`security_notice` (do **not** auto-accept; fail fast or dismiss with Esc).
A notice can appear on top of the plan dialog after approval. Resolve the
topmost modal first; do not send a plan answer while a notice is on top.

### 2.7 Normal output that quotes a dialog (false-positive fixture)

Prompt: print the four dialog-looking lines verbatim. The TUI renders
them as assistant text, above the input box and status bar:

```
  ●  Do you want to proceed?
     Enter to select · ↑/↓ to navigate · Esc to cancel
     ❯  1.  Yes
     ☐ Color
  ✶ Swooping…  (4s · ↓ 23 tokens)

 ──────────────────────────────────────────────────────────────────────────
 ❯
 ──────────────────────────────────────────────────────────────────────────
  ⏵⏵ auto mode on  (shift+tab to cycle) · esc to interrupt · ← for agents
```

Every marker is present, but every marker occurs **before** the last
status bar. A detector that searches for the strings alone reports a
dialog here; the anchor rule does not.

### 2.8 Raw escape sequences that matter

Observed on 2.1.299; these are the bytes the detector and responder must
tolerate.

- **No alternate screen.** `\x1b[?1049h` never appears. Frames are drawn
  over the normal buffer with absolute cursor moves. The accumulated
  stream therefore contains many stale frames; use the latest status bar
  as the frame boundary.
- **Words are positioned, not concatenated.** The AUQ footer is written
  as:

  ```
  Enter\x1b[7Gto\x1b[10Gselect\x1b[17G·\x1b[19G↑/↓\x1b[23Gto
  \x1b[26Gnavigate\x1b[35G·\x1b[37GEsc\x1b[41Gto\x1b[44Gcancel
  \x1b[39m\r\r\n\x1b[9A
  ```

  `strip_ansi()` turns each `\x1b[<n>G` into a space, so the stripped
  form is `Enter to select · ↑/↓ to navigate · Esc to cancel`. Matching
  the stripped form is correct; matching the raw bytes is not.
- **The dialog is redrawn, then the status bar returns.** After an
  answer the TUI rewrites the status bar below the old footer, which is
  why the footer index falls behind `last_status` and the same screen
  text no longer reports a dialog.
- **Prompt submission.** The provider's bracketed paste is
  `\x1b[200~<text>\x1b[201~` followed by `\r`; the TUI enables
  bracketed paste with `\x1b[?2004h` at startup. Answer keys are single
  printable bytes (`1`..`4`) or `\r` / `\x1b` / `\x03`; no bracketed
  paste is needed for them.
- **The status bar is a single redrawn line.** It carries
  `⏵⏵ auto mode on`, `⏸ manual mode on`, `⏸ plan mode on`,
  `⏵⏵ accept edits on`, `⏵⏵ don't ask on`, or
  `⏵⏵ bypass permissions on`, followed by hints such as
  `← for agents`, `? for shortcuts`, and `/effort`. Match the mode
  phrase, not the whole line.

## 3. Detection

### 3.1 Screen model

The TUI does **not** use the alternate screen. Each frame is redrawn in
place with absolute cursor moves (`\x1b[<col>G`, `\x1b[<n>A/B`), so the
accumulated byte stream contains stale frames. The reliable anchor is
the most recently drawn status bar.

```python
STATUS_RE = re.compile(
    r"(?:auto|manual|plan) mode on"
    r"|accept edits on"
    r"|don['’]t ask on"
    r"|bypass permissions on"
)

FOOTERS = {
    "ask_user_question": ("Enter to select",),
    "review":            ("Ready to submit your answers?",),
    "permission":        ("Esc to cancel · Tab to amend",),
    "plan":              ("ctrl+g to edit in nano",
                          "shift+tab to approve with this feedback"),
    "notice":            ("Enter to continue · Esc to cancel",),
    "security_notice":   ("Enter to confirm · Esc to cancel",),
}
```

Algorithm on the rolling stripped screen (`last 32 KiB`):

1. `last_status = max((m.start() for m in STATUS_RE.finditer(screen)),
   default=-1)`. If `last_status < 0`:
   - if a `security_notice` footer is present, return `security_notice`
     (startup modal such as the bypass-permissions warning; fail fast);
   - otherwise return `None` (startup or a frame captured before the
     first status bar).
2. `footer = max(screen.rfind(f) for f in all footer phrases, default=-1)`.
3. If `footer < 0` or `footer < last_status`, return `None`: the marker
   is scrollback.
4. Classify by the highest-index footer. Refine with headers in the
   window between `last_status` and `footer`:
   - `AskUserQuestion`: `☐` checkbox header, or `Enter to select`.
   - `review`: `Review your answers` + `Ready to submit your answers?`.
   - `permission`: `Bash command`, `Edit file`, `Write file`, or
     `Do you want to proceed?` / `Do you want to make this edit`.
   - `plan`: `Ready to code?` / `Here is Claude's plan:` / `Would you
     like to proceed?`.
   - `notice` / `security_notice` by footer.
5. Require the same classification on two consecutive polls
   (`dialog_poll`, default 100 ms) before acting.
6. Once acted on, latch the dialog id until the footer disappears
   (status bar becomes the last chrome again) or a different dialog is
   detected. Do not re-send the same key.

### 3.2 Transcript corroboration

`_turn_events` already reads the transcript. Track the last assistant
`tool_use` and its `tool_result`:

- `AskUserQuestion` / `ExitPlanMode`: the tool always opens a modal.
  If the screen detector misses it (clipped render), a pending tool_use
  older than `dialog_grace` is still a dialog.
- `Bash` / `Edit` / `Write` / others: pending tool_use is necessary but
  not sufficient (a slow tool has no result yet). Require the screen
  footer for these.
- If a `tool_result` for the pending `tool_use_id` exists, the dialog is
  already closed; ignore stale screen text.

The transcript also supplies the payload for answers and for
`PermissionRequestEvent` mapping: `tool_use.input` has `command`,
`file_path`, `old_string`/`new_string`, `questions`, `plan`.

### 3.3 Prototype validation

The rule above was run over every capture (active dialogs, answered
dialogs, notices, and the false-positive fixture):

| Capture | Screen state | Detected |
| --- | --- | --- |
| AskUserQuestion single | open | `ask_user_question` |
| AskUserQuestion multi | open | `ask_user_question` |
| AskUserQuestion review | open | `review` |
| Bash permission (`rm -rf`) | open | `permission` |
| Edit permission | open | `permission` |
| Plan approval | open | `plan` |
| Auto-mode billing notice | open | `notice` |
| Bypass-permissions warning (no status bar) | open | `security_notice` |
| Answered AUQ / permission / review | closed | `None` |
| Normal output quoting every marker (§2.7) | closed | `None` |
| Plain turn (no markers) | closed | `None` |

Post-answer captures return `None` because the TUI redraws the status bar
below the old footer. That is the property tests should pin.

### 3.4 Residual risks

- A very long dialog may scroll the status bar out of the 32 KiB
  window. Keep the window large enough (32 KiB is ~100+ frames at this
  size) and use the transcript fallback for `AskUserQuestion` /
  `ExitPlanMode`.
- Text rendered with overlapping redraws can drop spaces (`ins ructed`).
  Match short footer phrases and headers, not sentences.
- The status bar string changes with mode
  (`auto`, `manual`, `plan`, `accept edits`, `don't ask`, `bypass`).
  Keep the regex, and re-capture on a Claude Code upgrade.
- A model can print a line matching the status regex. The transcript
  veto (pending tool_use) is the backstop.

## 4. Response

### 4.1 Verified key behavior

All keys were sent to the real TUI; the result is from the transcript
and the post-key screen.

| Dialog | Action | Keys | Result |
| --- | --- | --- | --- |
| AskUserQuestion (1 question) | choose option 1 | `1` | answered `Red`; turn continues |
| AskUserQuestion (1 question) | choose option 1 | `1\r` | same |
| AskUserQuestion (2 questions) | answer Q1=1, Q2=default | `1\r` | Red + Small; review page |
| AskUserQuestion review | submit | `\r` | `User answered ...` |
| AskUserQuestion | cancel | `Esc` | "User declined to answer questions"; rejection tool_result; turn ends |
| Permission Bash | allow once | `1` | command ran |
| Permission Bash | allow once | `1\r` | command ran |
| Permission Bash | deny | `4` | command did not run; turn interrupted |
| Permission Bash | cancel | `Esc` | rejection tool_result; turn ends |
| Permission Edit | allow once | `1` | edit applied |
| Plan approval | approve auto | `1` | plan approved; execution starts |
| Plan approval | approve auto | `1\r` | bundled keys did not approve (cause unconfirmed) |
| Plan approval | reject with feedback | `3`, then text + `\r` | plan rejected; "Stopped. No changes were made" |
| Plan approval | cancel | `Esc`, `Ctrl+C` | dialog stayed open |

Key selection rules:

- **Send the digit alone.** Digit selection submits for AskUserQuestion,
  permission, and plan. Bundling `\r` after the digit was unreliable for
  the plan dialog: every run that sent `1\r` in one write left the dialog
  open, while every run that sent `1` alone approved it. The cause was
  not isolated (focus timing vs. an intervening modal), so the safe rule
  is one digit per write.
- **Send `\r` only for the review page and notices.** The multi-question
  review page needs Enter to submit. The `notice` needs Enter to
  continue.
- **Esc does not close the plan dialog.** For fail-fast, close the PTY
  instead (§4.3–§4.4). Do not block on Esc.
- After a key, wait for the footer to disappear (status bar last) with
  `answer_settle` (default 2 s). If it does not, send `\r` once as a
  fallback, then fail fast.

### 4.2 `agent_extensions.tui_answers`

`agent_extensions` is already a free-form `dict[str, Any]`, so this needs
no IR type change.

```python
"agent_extensions": {
    "dialog_poll": 0.1,        # screen poll interval, seconds
    "dialog_grace": 1.0,       # wait after detection before acting
    "answer_settle": 2.0,      # wait for a dialog to close after a key
    "tui_answers": {
        "ask_user_question": {
            # question text (case-insensitive substring) -> answer
            "which color do you prefer?": "Red",
            "which size do you prefer?": "Small",
            # optional catch-all; omit unless you want an automatic answer
            # for every unmatched question
            # "*": {"option": 1},
            # free text answer for "Type something"
            # "describe the change": {"text": "rename the field"},
            # "multi": true  -> not supported yet, fail fast
        },
        "permission": {
            "default": "deny",            # allow_once | allow_always | deny
            "by_tool": {
                "Bash": "allow_once",
                "Edit": "deny",
            },
        },
        "plan": {
            "default": "reject",          # approve_auto | approve_manual | reject
            "feedback": "Do not proceed; stop here.",
        },
        "notice": {"default": "dismiss"},  # Enter
        # security_notice has no answer: always fail fast
    },
}
```

Resolution rules:

- **AskUserQuestion.** Read the questions from the transcript
  `tool_use.input.questions`. For each question in order, match
  `question` against the config keys (case-insensitive substring,
  longest match wins). Answer by option **label** (normalized, exact or
  prefix) or `{"option": <1-based index>}`. Unknown question, no match,
  `multiSelect: true`, or more questions than configured answers →
  fail fast. After the last question, send `\r` on the review page.
  Free text: select the "Type something" option, type the text, `\r`.
- **Permission.** Read the pending tool name and input from the
  transcript. Choose `by_tool[tool]` or `default`. Map the semantic
  choice to the *currently rendered* option list, not a fixed index
  (the list varies between `rm -rf` captures):
  - `allow_once`: first option whose label starts with `Yes` and
    contains none of `always`, `don't ask again`, `switch to`.
  - `allow_always`: option containing `always allow`, `always allow
    access`, or `don't ask again`.
  - `allow_session`: option containing `switch to accept edits` or
    `switch to auto mode`.
  - `deny`: option whose label is exactly `No`; fall back to `Esc`.
  If the option list cannot be parsed, fail fast.
- **Plan.** `approve_auto` → `1`; `approve_manual` → `2`;
  `reject` → `3`, then `feedback` + `\r`. No Esc fallback.
- **Notice.** `\r`. **security_notice**: fail fast; do not accept.
- **Unknown dialog type.** Fail fast; never send a guessed key.

Defaults are intentionally conservative: no config means no
auto-approval. `allow_always` and `allow_session` must be explicit.

### 4.3 Fail-fast `ErrorEvent`

`ErrorEvent` has no dialog field. Use a stable prefix so a consumer can
parse it without a type change:

```
tui_dialog_unanswered: ask_user_question: "Which color do you prefer?"
  (options: Red, Blue); set agent_extensions.tui_answers.ask_user_question

tui_dialog_unanswered: permission: Bash "rm -rf ./victim.txt";
  set agent_extensions.tui_answers.permission

tui_dialog_unanswered: plan: "Append a line to notes.txt";
  set agent_extensions.tui_answers.plan
```

`is_fatal=True`. Then `break` out of `_turn_events`; `stream()`'s
`finally` closes the PTY. This is the whole fix for the 600 s hang: no
answer needed, no key needed, no wait.

Recommended follow-up (not in #57): add optional
`dialog: {type, question, options, tool_name, tool_use_id}` to
`ErrorEvent` and `PermissionRequestEvent`, so consumers do not parse
English.

### 4.4 State machine inside `_turn_events`

```
loop:
  chunk = read PTY (<=100 ms)
  screen = strip_ansi(buffer + chunk)[-32768:]
  rows = read transcript
  update pending_tool_use / results
  dialog = detect(screen)            # §3.1
  if dialog and debounced and not latched:
      if dialog is notice:          send \r; latch
      elif dialog is security:      yield ErrorEvent; break
      elif answer = resolve(dialog): send answer; latch; wait close
      else:                          yield ErrorEvent; break
  if turn_done: break
```

`detect()` must be a pure function over the screen text so it is
unit-testable with the §2 fixtures. `resolve()` must consult the
transcript payload, not the parsed prose.

## 5. ACP mapping

### 5.1 Current state

- `IRBackend._to_acp_update` maps only `message_delta`,
  `reasoning_delta`, `tool_use`, and `tool_result`. It returns `None`
  for `permission_request` and `permission_response`.
- No provider consumes `PermissionResponseEvent`; `Session` has no
  `respond()` method. The IR stream is one-way.
- `ACPBackend` does not call `set_request_handler`, so an external ACP
  agent's `session/request_permission` is not answered either. That is a
  separate gateway gap.

Therefore, emitting `PermissionRequestEvent` from the TUI provider today
would be a promise the stack cannot keep.

### 5.2 Decision

**Keep `supports_permissions=False`.** Map every unanswered dialog to
`ErrorEvent` (§4.3). Do not emit `PermissionRequestEvent` in #57.

`AskUserQuestion` and plan approval have no ACP request method at all
(the vendored ACP has `ElicitationCapabilities` but no
`session/request_elicitation`). There is nothing to map them to.

### 5.3 Future mapping (separate issue)

If a bidirectional channel is added:

1. Provider protocol: `async def respond(request_id, decision)` or a
   `permission_handler` callback in `agent_extensions`.
2. TUI provider: on a detected permission dialog, emit
   `PermissionRequestEvent` with `tool_name`, `tool_use_id` (from the
   pending transcript `tool_use`), `tool_input`, `description` (question
   + command/file). Wait for `respond()` up to `dialog_grace`.
3. Map the decision to the TUI option digit (§4.2) and emit
   `PermissionResponseEvent(granted=...)`.
4. `IRBackend`: on `permission_request`, call
   `ACPAgent.request_permission` and feed the outcome back through
   `respond()`. Map options to `PermissionOptionKind`:
   - `Yes` → `allow_once`
   - `Yes, and always allow ...` → `allow_always`
   - `Yes, and switch to accept edits / auto mode` → `allow_always`
     (ACP has no mode-switch kind; document the session effect)
   - `No` → `reject_once`
   - `Esc` / dialog closed → `outcome="cancelled"`
5. Plan approval: no ACP equivalent. Keep `ErrorEvent` or define an
   agentabi ACP extension. Do not overload `request_permission`.
6. AskUserQuestion: keep `ErrorEvent` or define an extension. Do not
   overload `request_permission` with one option per answer; it loses
   the question text and multi-question structure.

## 6. Tests to add

Unit tests over captured screen text (fixtures copied from §2):

1. `test_detect_ask_user_question` → `ask_user_question`, question text,
   options.
2. `test_detect_ask_user_question_multi` → `ask_user_question`, two
   questions, `multiSelect=false`.
3. `test_detect_ask_user_question_review` → `review`.
4. `test_detect_permission_bash` → `permission`, `Bash`,
   `rm -rf ./victim.txt`.
5. `test_detect_permission_edit` → `permission`, `Edit`, `notes.txt`.
6. `test_detect_plan` → `plan`.
7. `test_detect_notice` and `test_detect_security_notice`.
8. `test_no_false_positive_on_assistant_text` — §2.7 fixture → `None`.
9. `test_no_false_positive_after_answer` — dialog fixture with a later
   status bar → `None`.
10. `test_no_detection_without_status_bar` — mid-redraw frame → `None`.

Behavior tests with a fake TUI script (the existing `_FAKE_TUI` pattern
in `tests/providers/test_claude_tui.py`):

11. `test_unanswered_dialog_fails_fast` — fake prints the AUQ screen and
    blocks; assert one fatal `ErrorEvent` with
    `tui_dialog_unanswered: ask_user_question`, elapsed <
    `dialog_grace + answer_settle + 2 s`, and no 600 s wait.
12. `test_configured_ask_user_question_sends_answer` — assert the digit
    bytes reached the PTY and a follow-up transcript row was read.
13. `test_configured_permission_allow` — fake writes a pending Bash
    `tool_use`; assert the parsed option index for `allow_once`.
14. `test_configured_plan_reject` — assert `3` then feedback + `\r`.
15. `test_security_notice_is_not_auto_accepted`.
16. `test_no_dialog_no_keys` — normal turn; assert no bytes other than
    the prompt were written.

The full-screen fixtures are small enough to commit as `.txt` under
`tests/fixtures/tui_dialogs/` (one file per §2 block). Keep the raw
byte captures out of the repo; they contain machine-specific paths.

## 7. Implementation plan (when #57 moves to code)

`src/agentabi/providers/claude_tui.py` only:

1. Add `_STATUS_RE`, `_FOOTERS`, `_HEADERS`, and
   `detect_dialog(screen) -> Dialog | None` as module-level pure
   functions. `Dialog` is a small dataclass
   (`type`, `question`, `options`, `tool_name`, `tool_use_id`).
2. In `_turn_events`, keep a bounded `screen` buffer from the chunks it
   already reads. Do not add a second PTY reader; `_read_chunk` is
   already the 100 ms loop.
3. Track `pending_tool_use` and `tool_results` while iterating rows.
   `_turn_row` already yields the tool events; add a side table keyed by
   `tool_use_id`.
4. Add `_resolve_dialog(dialog, pending, extensions)` and
   `_send_answer(proc, keys)`.
5. On unresolved dialog: yield the fatal `ErrorEvent`, `break`. The
   existing `finally: await proc.close()` handles teardown.
6. Keep `capabilities()["supports_permissions"] = False`.
7. Add the §6 tests and fixtures.

No change to `src/agentabi/types/` in this step. The `tui_answers`
schema is an `agent_extensions` payload, not an IR type.

## 8. Open questions

- **`multiSelect: true`** was not reproduced. The input shape has the
  flag, but the key flow (Space to toggle? Enter to submit?) is
  unverified. Until captured, fail fast for `multiSelect: true`.
- **Write/WebFetch/WebSearch permission screens** were not captured.
  They likely reuse the `Edit file` / `Bash command` frame with a
  different header. Mark `未验证`; add captures before enabling answers
  for them.
- **Plan dialog cancellation.** Esc and one Ctrl+C did not close it.
  Test double Ctrl+C before relying on it; the design does not need it
  because fail-fast closes the PTY.
- **Status-bar drift.** A future Claude Code release can rename modes or
  move the bar. Pin a version in the capture test and re-capture on
  upgrade (the same drift risk already exists in `_wait_ready`).
- **Startup false positive.** `_wait_ready` treats the theme picker's
  `❯` as the input prompt. That is issue #59 territory, but the fix is
  the same idea: anchor on the status bar / input-box chrome, not on a
  single glyph.
