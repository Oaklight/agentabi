"""Tests for Session and auto_detect."""

from unittest.mock import patch

from agentabi import Session, detect_agents


class TestSession:
    """Test Session initialization and task building."""

    @patch("shutil.which", return_value="/usr/bin/claude")
    def test_init_explicit_agent(self, mock_which):
        session = Session(agent="claude_code")
        assert session.agent == "claude_code"

    @patch("shutil.which", return_value="/usr/bin/claude")
    def test_init_with_model(self, mock_which):
        session = Session(agent="claude_code", model="opus")
        assert session.model == "opus"

    @patch("shutil.which", return_value="/usr/bin/claude")
    def test_build_task(self, mock_which):
        session = Session(agent="claude_code", model="sonnet")
        task = session._build_task(
            "Fix the bug",
            working_dir="/tmp",
            max_turns=3,
        )
        assert task["prompt"] == "Fix the bug"
        assert task["model"] == "sonnet"
        assert task["working_dir"] == "/tmp"
        assert task["max_turns"] == 3


class TestAutoDetect:
    """Test agent detection."""

    def test_detect_agents_returns_list(self):
        agents = detect_agents()
        assert isinstance(agents, list)
