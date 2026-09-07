"""Tests for AgyNativeProvider command building and event parsing."""

from typing import Any, cast

from agentabi.providers.agy_native import AgyNativeProvider
from agentabi.types.ir.task import TaskConfig


class TestBuildCommand:
    @staticmethod
    def _task(d: dict[str, Any]) -> TaskConfig:
        return cast(TaskConfig, d)

    def test_basic_command(self):
        task = self._task({"prompt": "Hello", "agent": "agy"})
        cmd = AgyNativeProvider._build_command(task)
        assert cmd == ["agy", "--output-format", "stream-json", "-p", "Hello"]

    def test_with_model(self):
        task = self._task({"prompt": "Hi", "agent": "agy", "model": "gemini-2.5-pro"})
        cmd = AgyNativeProvider._build_command(task)
        assert "--model" in cmd
        assert "gemini-2.5-pro" in cmd

    def test_permission_full_auto(self):
        task = self._task(
            {
                "prompt": "Hi",
                "agent": "agy",
                "permissions": {"level": "full_auto"},
            }
        )
        cmd = AgyNativeProvider._build_command(task)
        assert "--dangerously-skip-permissions" in cmd
        assert "--mode" not in cmd

    def test_permission_accept_edits(self):
        task = self._task(
            {
                "prompt": "Hi",
                "agent": "agy",
                "permissions": {"level": "accept_edits"},
            }
        )
        cmd = AgyNativeProvider._build_command(task)
        assert "--mode" in cmd
        idx = cmd.index("--mode")
        assert cmd[idx + 1] == "accept-edits"

    def test_permission_plan(self):
        task = self._task(
            {
                "prompt": "Hi",
                "agent": "agy",
                "permissions": {"level": "plan"},
            }
        )
        cmd = AgyNativeProvider._build_command(task)
        assert "--mode" in cmd
        idx = cmd.index("--mode")
        assert cmd[idx + 1] == "plan"

    def test_permission_default_omitted(self):
        task = self._task(
            {
                "prompt": "Hi",
                "agent": "agy",
                "permissions": {"level": "default"},
            }
        )
        cmd = AgyNativeProvider._build_command(task)
        assert "--mode" not in cmd
        assert "--dangerously-skip-permissions" not in cmd

    def test_session_resume_with_id(self):
        """session_id + resume → --conversation <id>."""
        task = self._task(
            {
                "prompt": "Continue",
                "agent": "agy",
                "resume": True,
                "session_id": "conv-abc-123",
            }
        )
        cmd = AgyNativeProvider._build_command(task)
        assert "--conversation" in cmd
        assert "conv-abc-123" in cmd

    def test_resume_without_session_id(self):
        """resume without session_id → --continue."""
        task = self._task({"prompt": "Hi", "agent": "agy", "resume": True})
        cmd = AgyNativeProvider._build_command(task)
        assert "--continue" in cmd
        assert "--conversation" not in cmd

    def test_session_id_without_resume_ignored(self):
        """session_id without resume → no session flags."""
        task = self._task(
            {"prompt": "Hi", "agent": "agy", "session_id": "conv-abc-123"}
        )
        cmd = AgyNativeProvider._build_command(task)
        assert "--conversation" not in cmd
        assert "--continue" not in cmd

    def test_timeout_maps_to_print_timeout(self):
        task = self._task({"prompt": "Hi", "agent": "agy", "timeout": 30.0})
        cmd = AgyNativeProvider._build_command(task)
        assert "--print-timeout" in cmd
        idx = cmd.index("--print-timeout")
        assert cmd[idx + 1] == "30s"

    def test_timeout_zero_omitted(self):
        task = self._task({"prompt": "Hi", "agent": "agy", "timeout": 0})
        cmd = AgyNativeProvider._build_command(task)
        assert "--print-timeout" not in cmd

    def test_effort_extension(self):
        task = self._task(
            {
                "prompt": "Hi",
                "agent": "agy",
                "agent_extensions": {"effort": "high"},
            }
        )
        cmd = AgyNativeProvider._build_command(task)
        assert "--effort" in cmd
        assert "high" in cmd

    def test_sandbox_extension(self):
        task = self._task(
            {
                "prompt": "Hi",
                "agent": "agy",
                "agent_extensions": {"sandbox": True},
            }
        )
        cmd = AgyNativeProvider._build_command(task)
        assert "--sandbox" in cmd

    def test_disable_slash_commands(self):
        task = self._task(
            {
                "prompt": "Hi",
                "agent": "agy",
                "agent_extensions": {"disable_slash_commands": True},
            }
        )
        cmd = AgyNativeProvider._build_command(task)
        assert "--disable-slash-commands" in cmd

    def test_agent_extension(self):
        task = self._task(
            {
                "prompt": "Hi",
                "agent": "agy",
                "agent_extensions": {"agent": "code-agent"},
            }
        )
        cmd = AgyNativeProvider._build_command(task)
        assert "--agent" in cmd
        assert "code-agent" in cmd

    def test_json_schema_extension(self):
        task = self._task(
            {
                "prompt": "Hi",
                "agent": "agy",
                "agent_extensions": {"json_schema": '{"type":"object"}'},
            }
        )
        cmd = AgyNativeProvider._build_command(task)
        assert "--json-schema" in cmd
        assert '{"type":"object"}' in cmd

    def test_add_dirs_extension(self):
        task = self._task(
            {
                "prompt": "Hi",
                "agent": "agy",
                "agent_extensions": {
                    "add_dirs": ["/path/to/dir1", "/path/to/dir2"],
                },
            }
        )
        cmd = AgyNativeProvider._build_command(task)
        assert cmd.count("--add-dir") == 2
        assert "/path/to/dir1" in cmd
        assert "/path/to/dir2" in cmd

    def test_prompt_comes_after_p_flag(self):
        task = self._task({"prompt": "What is 2+2?", "agent": "agy"})
        cmd = AgyNativeProvider._build_command(task)
        assert cmd[-2] == "-p"
        assert cmd[-1] == "What is 2+2?"

    def test_combined_flags(self):
        """Typical pipeline invocation with multiple flags."""
        task = self._task(
            {
                "prompt": "implement feature X",
                "agent": "agy",
                "model": "gemini-2.5-pro",
                "resume": True,
                "session_id": "conv-123",
                "timeout": 120,
                "permissions": {"level": "full_auto"},
                "agent_extensions": {
                    "effort": "high",
                    "sandbox": True,
                    "add_dirs": ["/extra"],
                },
            }
        )
        cmd = AgyNativeProvider._build_command(task)
        assert "--model" in cmd
        assert "gemini-2.5-pro" in cmd
        assert "--dangerously-skip-permissions" in cmd
        assert "--conversation" in cmd
        assert "conv-123" in cmd
        assert "--print-timeout" in cmd
        assert "120s" in cmd
        assert "--effort" in cmd
        assert "high" in cmd
        assert "--sandbox" in cmd
        assert "--add-dir" in cmd
        assert "/extra" in cmd
        # prompt is last
        assert cmd[-2] == "-p"
        assert cmd[-1] == "implement feature X"

    def test_output_format_before_prompt(self):
        """--output-format stream-json must come before -p."""
        task = self._task({"prompt": "Hi", "agent": "agy"})
        cmd = AgyNativeProvider._build_command(task)
        fmt_idx = cmd.index("--output-format")
        p_idx = cmd.index("-p")
        assert fmt_idx < p_idx


class TestBuildEnv:
    @staticmethod
    def _task(d: dict[str, Any]) -> TaskConfig:
        return cast(TaskConfig, d)

    def test_openai_base_url_bridged(self):
        task = self._task(
            {
                "prompt": "Hi",
                "agent": "agy",
                "env": {"OPENAI_BASE_URL": "https://proxy.example.com/v1"},
            }
        )
        env = AgyNativeProvider._build_env(task)
        assert env["GOOGLE_GEMINI_BASE_URL"] == "https://proxy.example.com"

    def test_openai_base_url_no_v1(self):
        task = self._task(
            {
                "prompt": "Hi",
                "agent": "agy",
                "env": {"OPENAI_BASE_URL": "https://proxy.example.com"},
            }
        )
        env = AgyNativeProvider._build_env(task)
        assert env["GOOGLE_GEMINI_BASE_URL"] == "https://proxy.example.com"

    def test_openai_api_key_bridged(self):
        task = self._task(
            {
                "prompt": "Hi",
                "agent": "agy",
                "env": {"OPENAI_API_KEY": "sk-test-key"},
            }
        )
        env = AgyNativeProvider._build_env(task)
        assert env["GEMINI_API_KEY"] == "sk-test-key"

    def test_native_vars_not_overridden(self):
        """agy-native vars take precedence — bridging should not override."""
        task = self._task(
            {
                "prompt": "Hi",
                "agent": "agy",
                "env": {
                    "GOOGLE_GEMINI_BASE_URL": "https://native.example.com",
                    "GEMINI_API_KEY": "native-key",
                    "OPENAI_BASE_URL": "https://openai.example.com/v1",
                    "OPENAI_API_KEY": "openai-key",
                },
            }
        )
        env = AgyNativeProvider._build_env(task)
        assert env["GOOGLE_GEMINI_BASE_URL"] == "https://native.example.com"
        assert env["GEMINI_API_KEY"] == "native-key"

    def test_no_env_overrides(self):
        """No env → no bridging vars added."""
        task = self._task({"prompt": "Hi", "agent": "agy"})
        env = AgyNativeProvider._build_env(task)
        # Should not crash, just return merged os.environ
        assert isinstance(env, dict)


class TestParseEvent:
    def test_init_event(self):
        raw = {
            "event": "init",
            "conversation_id": "conv-abc-123",
            "init": {
                "cwd": "/home/user/project",
                "tools": ["bash", "read", "write"],
                "permission_mode": "full_auto",
            },
        }
        events = AgyNativeProvider._parse_event(raw)
        assert len(events) == 1
        assert events[0]["type"] == "session_start"
        assert events[0]["session_id"] == "conv-abc-123"
        assert events[0]["agent"] == "agy"
        assert events[0]["working_dir"] == "/home/user/project"
        assert events[0]["tools"] == ["bash", "read", "write"]

    def test_init_event_minimal(self):
        raw = {
            "event": "init",
            "conversation_id": "conv-123",
            "init": {},
        }
        events = AgyNativeProvider._parse_event(raw)
        assert len(events) == 1
        assert events[0]["type"] == "session_start"
        assert events[0]["session_id"] == "conv-123"
        assert "working_dir" not in events[0]
        assert "tools" not in events[0]

    def test_step_update_agent_response_with_text(self):
        raw = {
            "event": "step_update",
            "step_update": {
                "conversation_id": "conv-123",
                "step_index": 1,
                "state": "DONE",
                "step_type": "agent_response",
                "text_delta": "HELLO\n",
                "duration_seconds": 2.3,
                "usage": {
                    "input_tokens": 14158,
                    "output_tokens": 356,
                    "thinking_tokens": 354,
                    "cache_read_tokens": 0,
                    "total_tokens": 14514,
                },
            },
        }
        events = AgyNativeProvider._parse_event(raw)
        assert len(events) == 3
        assert events[0]["type"] == "message_start"
        assert events[0]["role"] == "assistant"
        assert events[1]["type"] == "message_delta"
        assert events[1]["text"] == "HELLO\n"
        assert events[2]["type"] == "message_end"
        assert events[2]["text"] == "HELLO\n"

    def test_step_update_agent_response_no_text(self):
        raw = {
            "event": "step_update",
            "step_update": {
                "conversation_id": "conv-123",
                "step_index": 1,
                "state": "DONE",
                "step_type": "agent_response",
            },
        }
        events = AgyNativeProvider._parse_event(raw)
        assert len(events) == 2  # message_start + message_end, no delta
        assert events[0]["type"] == "message_start"
        assert events[1]["type"] == "message_end"
        assert "text" not in events[1]

    def test_step_update_user_input_skipped(self):
        raw = {
            "event": "step_update",
            "step_update": {
                "conversation_id": "conv-123",
                "step_index": 0,
                "state": "DONE",
                "step_type": "user_input",
            },
        }
        events = AgyNativeProvider._parse_event(raw)
        assert events == []

    def test_step_update_checkpoint_skipped(self):
        raw = {
            "event": "step_update",
            "step_update": {
                "conversation_id": "conv-123",
                "step_index": 2,
                "state": "DONE",
                "step_type": "checkpoint",
            },
        }
        events = AgyNativeProvider._parse_event(raw)
        assert events == []

    def test_step_update_tool_use(self):
        raw = {
            "event": "step_update",
            "step_update": {
                "conversation_id": "conv-123",
                "step_index": 1,
                "state": "DONE",
                "step_type": "tool_use",
                "tool_use_id": "tool_call_1",
                "tool_name": "bash",
                "tool_input": {"command": "ls -la"},
            },
        }
        events = AgyNativeProvider._parse_event(raw)
        assert len(events) == 1
        assert events[0]["type"] == "tool_use"
        assert events[0]["tool_use_id"] == "tool_call_1"
        assert events[0]["tool_name"] == "bash"
        assert events[0]["tool_input"] == {"command": "ls -la"}

    def test_step_update_tool_use_with_result(self):
        raw = {
            "event": "step_update",
            "step_update": {
                "conversation_id": "conv-123",
                "step_index": 1,
                "state": "DONE",
                "step_type": "tool_use",
                "tool_use_id": "tool_call_1",
                "tool_name": "bash",
                "tool_input": {"command": "echo hello"},
                "tool_result": "hello\n",
            },
        }
        events = AgyNativeProvider._parse_event(raw)
        assert len(events) == 2
        assert events[0]["type"] == "tool_use"
        assert events[1]["type"] == "tool_result"
        assert events[1]["tool_use_id"] == "tool_call_1"
        assert events[1]["content"] == "hello\n"

    def test_step_update_tool_use_with_error(self):
        raw = {
            "event": "step_update",
            "step_update": {
                "conversation_id": "conv-123",
                "step_index": 1,
                "state": "DONE",
                "step_type": "tool_use",
                "tool_use_id": "tool_call_2",
                "tool_name": "bash",
                "tool_input": {"command": "bad_cmd"},
                "tool_result": "command not found",
                "is_error": True,
            },
        }
        events = AgyNativeProvider._parse_event(raw)
        assert len(events) == 2
        assert events[1]["type"] == "tool_result"
        assert events[1]["is_error"] is True

    def test_result_success(self):
        raw = {
            "event": "result",
            "result": {
                "conversation_id": "conv-abc-123",
                "status": "SUCCESS",
                "response": "HELLO\n",
                "duration_seconds": 2.6,
                "num_turns": 1,
                "usage": {
                    "input_tokens": 14158,
                    "output_tokens": 356,
                    "thinking_tokens": 354,
                    "cache_read_tokens": 0,
                    "total_tokens": 14514,
                },
            },
        }
        events = AgyNativeProvider._parse_event(raw)
        assert len(events) == 2  # usage + session_end
        assert events[0]["type"] == "usage"
        assert events[0]["usage"]["input_tokens"] == 14158
        assert events[0]["usage"]["output_tokens"] == 356
        assert events[0]["usage"]["total_tokens"] == 14514
        assert events[1]["type"] == "session_end"
        assert events[1]["session_id"] == "conv-abc-123"

    def test_result_failure(self):
        raw = {
            "event": "result",
            "result": {
                "conversation_id": "conv-abc-123",
                "status": "FAILURE",
                "error": "API rate limit exceeded",
                "usage": {},
            },
        }
        events = AgyNativeProvider._parse_event(raw)
        assert len(events) == 3  # usage + error + session_end
        assert events[0]["type"] == "usage"
        assert events[1]["type"] == "error"
        assert events[1]["error"] == "API rate limit exceeded"
        assert events[1]["is_fatal"] is True
        assert events[2]["type"] == "session_end"

    def test_result_failure_without_error_message(self):
        raw = {
            "event": "result",
            "result": {
                "conversation_id": "conv-123",
                "status": "TIMEOUT",
                "usage": {},
            },
        }
        events = AgyNativeProvider._parse_event(raw)
        error_events = [e for e in events if e["type"] == "error"]
        assert len(error_events) == 1
        assert "TIMEOUT" in error_events[0]["error"]

    def test_result_cache_read_tokens(self):
        raw = {
            "event": "result",
            "result": {
                "conversation_id": "conv-123",
                "status": "SUCCESS",
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cache_read_tokens": 80,
                    "total_tokens": 150,
                },
            },
        }
        events = AgyNativeProvider._parse_event(raw)
        usage = events[0]["usage"]
        assert usage["cache_read_tokens"] == 80

    def test_unknown_event_type(self):
        raw = {"event": "unknown_type", "data": "whatever"}
        events = AgyNativeProvider._parse_event(raw)
        assert events == []

    def test_missing_event_key(self):
        raw = {"some_other_key": "value"}
        events = AgyNativeProvider._parse_event(raw)
        assert events == []


class TestResultTextAggregation:
    """End-to-end: full event flow through _RunState produces result_text."""

    def test_agy_simple_flow(self):
        from agentabi.providers.base import _RunState

        state = _RunState()

        raw_events = [
            {
                "event": "init",
                "conversation_id": "conv-abc",
                "init": {"cwd": "/tmp", "tools": ["bash"]},
            },
            {
                "event": "step_update",
                "step_update": {
                    "conversation_id": "conv-abc",
                    "step_index": 0,
                    "state": "DONE",
                    "step_type": "user_input",
                },
            },
            {
                "event": "step_update",
                "step_update": {
                    "conversation_id": "conv-abc",
                    "step_index": 1,
                    "state": "DONE",
                    "step_type": "agent_response",
                    "text_delta": "HELLO\n",
                    "duration_seconds": 2.3,
                    "usage": {
                        "input_tokens": 14158,
                        "output_tokens": 356,
                        "total_tokens": 14514,
                    },
                },
            },
            {
                "event": "step_update",
                "step_update": {
                    "conversation_id": "conv-abc",
                    "step_index": 2,
                    "state": "DONE",
                    "step_type": "checkpoint",
                },
            },
            {
                "event": "result",
                "result": {
                    "conversation_id": "conv-abc",
                    "status": "SUCCESS",
                    "response": "HELLO\n",
                    "duration_seconds": 2.6,
                    "num_turns": 1,
                    "usage": {
                        "input_tokens": 14158,
                        "output_tokens": 356,
                        "total_tokens": 14514,
                    },
                },
            },
        ]

        for raw in raw_events:
            for event in AgyNativeProvider._parse_event(raw):
                state.handle(event)

        result = state.build()
        assert result["status"] == "success"
        assert result["result_text"] == "HELLO\n"
        assert result["session_id"] == "conv-abc"

    def test_agy_error_flow(self):
        from agentabi.providers.base import _RunState

        state = _RunState()

        raw_events = [
            {
                "event": "init",
                "conversation_id": "conv-err",
                "init": {"cwd": "/tmp"},
            },
            {
                "event": "result",
                "result": {
                    "conversation_id": "conv-err",
                    "status": "FAILURE",
                    "error": "Something went wrong",
                    "usage": {},
                },
            },
        ]

        for raw in raw_events:
            for event in AgyNativeProvider._parse_event(raw):
                state.handle(event)

        result = state.build()
        assert result["status"] == "error"
        assert "Something went wrong" in result["errors"]


class TestCapabilities:
    def test_capabilities(self):
        caps = AgyNativeProvider().capabilities()
        assert caps["name"] == "Antigravity"
        assert caps["agent_type"] == "agy"
        assert caps["supports_streaming"] is True
        assert caps["supports_mcp"] is True
        assert caps["supports_session_resume"] is True
        assert caps["supports_system_prompt"] is False
        assert caps["supports_tool_filtering"] is False
        assert caps["supports_permissions"] is True
        assert caps["supports_multi_turn"] is True
        assert caps["transport"] == "subprocess"
