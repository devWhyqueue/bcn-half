"""Check installed commands without loading secrets or making network requests."""

import importlib.metadata
from unittest.mock import patch

import pytest

from bcn_half import bot


@pytest.mark.parametrize("module", [bot])
def test_cli_help_does_not_load_env(module):
    with (
        patch("sys.argv", ["command", "--help"]),
        patch.object(module, "load_env") as load,
    ):
        with pytest.raises(SystemExit) as stopped:
            module.cli()
    assert stopped.value.code == 0
    load.assert_not_called()


def test_console_commands_are_installed():
    commands = {
        entry.name: entry.value
        for entry in importlib.metadata.distribution("bcn-half").entry_points
    }
    assert commands == {"bcn-half": "bcn_half.bot:cli"}


def test_bot_workflow_imports_packaged_application():
    with patch("bcn_half.bot.import_module") as imported:
        bot._application("https://example.org", "REF1", "a@example.org")
    imported.assert_called_once_with("bcn_half.application")
