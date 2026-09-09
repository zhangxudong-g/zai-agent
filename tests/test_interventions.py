"""Tests for safety interventions."""

from unittest.mock import MagicMock

from zai.interventions import DangerousCommandIntervention, SensitiveFileIntervention


def make_tool_use(name: str, args: dict):
    """Create a mock tool use event."""
    event = MagicMock()
    event.tool_use = {"name": name, "input": args}
    return event


def test_dangerous_command_blocks_rm_rf():
    """Test that rm -rf is blocked."""
    intervention = DangerousCommandIntervention()
    event = make_tool_use("shell", {"command": "rm -rf /tmp/test"})
    result = intervention.before_tool_call(event)

    # Should return Confirm (not Deny)
    from strands.interventions import Confirm

    assert isinstance(result, Confirm)


def test_dangerous_command_blocks_shutdown():
    """Test that shutdown is denied."""
    intervention = DangerousCommandIntervention()
    event = make_tool_use("shell", {"command": "shutdown -h now"})
    result = intervention.before_tool_call(event)

    from strands.interventions import Deny

    assert isinstance(result, Deny)


def test_dangerous_command_blocks_dd():
    """Test that dd requires confirmation."""
    intervention = DangerousCommandIntervention()
    event = make_tool_use("shell", {"command": "dd if=/dev/zero of=/dev/sda"})
    result = intervention.before_tool_call(event)

    # dd requires confirmation (not auto-denied)
    from strands.interventions import Confirm

    assert isinstance(result, Confirm)


def test_dangerous_command_allows_safe():
    """Test that safe commands are allowed."""
    intervention = DangerousCommandIntervention()
    event = make_tool_use("shell", {"command": "ls -la"})
    result = intervention.before_tool_call(event)

    from strands.interventions import Proceed

    assert isinstance(result, Proceed)


def test_dangerous_command_ignores_non_shell():
    """Test that non-shell tools are ignored."""
    intervention = DangerousCommandIntervention()
    event = make_tool_use("read", {"file_path": "test.py"})
    result = intervention.before_tool_call(event)

    from strands.interventions import Proceed

    assert isinstance(result, Proceed)


def test_dangerous_command_auto_approve():
    """Test auto_approve mode."""
    intervention = DangerousCommandIntervention(auto_approve=True)
    event = make_tool_use("shell", {"command": "rm -rf /tmp/test"})
    result = intervention.before_tool_call(event)

    from strands.interventions import Proceed

    assert isinstance(result, Proceed)


def test_sensitive_file_blocks_env():
    """Test that .env files are blocked."""
    intervention = SensitiveFileIntervention()
    event = make_tool_use("read", {"file_path": ".env"})
    result = intervention.before_tool_call(event)

    from strands.interventions import Deny

    assert isinstance(result, Deny)


def test_sensitive_file_blocks_env_write():
    """Test that writing to .env is blocked."""
    intervention = SensitiveFileIntervention()
    event = make_tool_use("write", {"file_path": ".env", "content": "SECRET=foo"})
    result = intervention.before_tool_call(event)

    from strands.interventions import Deny

    assert isinstance(result, Deny)


def test_sensitive_file_blocks_pem():
    """Test that .pem files are blocked."""
    intervention = SensitiveFileIntervention()
    event = make_tool_use("read", {"file_path": "private.pem"})
    result = intervention.before_tool_call(event)

    from strands.interventions import Deny

    assert isinstance(result, Deny)


def test_sensitive_file_allows_safe_files():
    """Test that safe files are allowed."""
    intervention = SensitiveFileIntervention()
    event = make_tool_use("read", {"file_path": "README.md"})
    result = intervention.before_tool_call(event)

    from strands.interventions import Proceed

    assert isinstance(result, Proceed)


def test_sensitive_file_allow_read():
    """Test that allow_read mode works."""
    intervention = SensitiveFileIntervention(allow_read=True)
    event = make_tool_use("read", {"file_path": ".env"})
    result = intervention.before_tool_call(event)

    from strands.interventions import Proceed

    assert isinstance(result, Proceed)


def test_intervention_names():
    """Test intervention names are set."""
    assert DangerousCommandIntervention.name == "zai-dangerous-commands"
    assert SensitiveFileIntervention.name == "zai-sensitive-files"
