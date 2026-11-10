# Claude Code TUI 传输

Claude 提供者可以驱动真正的交互式 Claude Code TUI，而不是 `claude --print`。当账号按订阅计费时使用它：Anthropic 把 `-p` 请求归类为 `sdk-cli`，计入额度约为交互式 `cli` 的两倍。

```python
from agentabi import Session

session = Session(agent="claude_code", prefer="pty")
result = await session.run(prompt="What is 2+2?")
```

通过 ACP 网关：

```bash
agentabi acp --agent claude --prefer pty
```

## 工作方式

提供者在伪终端上启动 `claude`，等待输入提示符，粘贴消息，确认 TUI 已渲染，按 Enter 提交，然后读取会话 transcript JSONL。事件来自 transcript，因此按整条消息输出，而不是 token 增量。

提示词不能包含控制字符。括号粘贴在 `ESC[201~` 处结束，嵌入的转义序列会把提示词剩余部分变成按键，因此这类提示词在任何字节写入前就会被拒绝。

## 对话框

TUI 会因对话框阻塞：提问、权限请求、计划审批和通知。在 `agent_extensions.tui_answers` 中配置回答；否则回合以 `tui_dialog_unanswered:` 前缀的 `ErrorEvent` 失败，而不是一直等待。

```python
task = {
    "prompt": "Ask me which color I prefer, then continue.",
    "agent_extensions": {
        "tui_answers": {
            # 问题文本子串 -> 选项标签、从 1 开始的序号，或文本。
            "ask_user_question": {
                "Which color": "Blue",
                "Which size": 2,
                "Anything else": {"text": "No preference"},
            },
            # 权限对话框：先按工具名匹配，再使用默认值。
            "permission": {
                "by_tool": {"Bash": "allow_once", "Write": "deny"},
                "default": "deny",
            },
            # 计划审批。
            "plan": {"default": "approve_manual"},
        }
    },
}
```

- `ask_user_question`：键按大小写不敏感的问题子串匹配，最长匹配优先，`*` 为兜底。值可以是选项标签、从 1 开始的序号，或 `{"text": ...}`。多问题对话框按 Enter 提交复核页。`multiSelect` 问题不支持，会快速失败。
- `permission`：取值为 `allow_once`、`allow_always`、`allow_session` 或 `deny`，映射到 TUI 实际渲染的标签。
- `plan`：`approve_auto`、`approve_manual`，或带 `feedback` 的 `reject`。
- `notice`：按 Enter 回答。
- 安全确认永不自动接受。请手动运行一次 CLI 并接受它。

回答以 transcript 中的工具调用作为闩锁，过期屏幕帧不会重复发送按键。

## 回合结束

回合在 `system/turn_duration` 或 `result` 结束，之后提供者最多继续读取 1.5 秒（静默 0.3 秒提前结束），以免截断 `last-prompt` 与 `cost-state` 行。可用 `agent_extensions.settle_timeout` 调整上限。

## 启动回退

如果目录未受信任或账号未登录导致 CLI 无法启动，提供者会回退到 native 子进程传输，再回退到 SDK，并发出说明原因的非致命 `ErrorEvent`。把 `agent_extensions.tui_fallback` 设为 `false` 可让该失败变为致命。

## 限制

- 按整条消息输出，没有 token 增量。
- 提供者读取终端渲染结果，因此比 `--print` 更脆弱。对话框检测以状态栏为锚点；Claude Code 版本更新若改变对话框形状，会失败关闭。
- 会话 id 必须是 UUID。新会话传入其他值会换用新的 UUID；恢复会话传入其他值会在启动子进程前失败。
- Windows 上子进程运行在 ConPTY 下；回合结束后关闭 PTY 时，提供者先发送 Ctrl+C，等待，然后终止进程。
