"""Check installed commands without loading secrets or making network requests."""

import importlib.metadata
import io
import json
import os
from unittest.mock import patch

import pytest

from bcn_half import bot, captcha, captchano_demo


@pytest.mark.parametrize("module", [bot, captchano_demo])
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
    assert commands["bcn-half"] == "bcn_half.bot:cli"
    assert commands["captchano-demo"] == "bcn_half.captchano_demo:cli"


def test_bot_workflow_imports_packaged_application():
    with patch("bcn_half.bot.import_module") as imported:
        bot._application("https://example.org", "REF1")
    imported.assert_called_once_with("bcn_half.application")


def test_solver_uses_captchano_endpoint():
    response = io.BytesIO(
        json.dumps({"status": "ready", "solution": {"token": "t" * 120}}).encode()
    )
    with patch.dict(os.environ, {"CAPTCHANO_API_KEY": "test-key"}, clear=True):
        with patch(
            "bcn_half.captcha.urllib.request.urlopen", return_value=response
        ) as request:
            captcha.request_solution(
                "https://example.org", "sitekey", "browser-agent", 30
            )
    assert request.call_args.args[0].full_url == "https://api.captchano.com/v1/solve"


@pytest.mark.parametrize("verified, exit_code", [(True, 0), (False, 1)])
def test_captcha_command_runs_the_captchano_demo(verified, exit_code):
    with patch("sys.argv", ["bcn-half", "--test-captcha", "--no-keep-open"]):
        with (
            patch.object(bot, "load_env"),
            patch.object(bot, "import_module") as imported,
        ):
            imported.return_value.run_demo.return_value = verified
            assert bot.main() == exit_code
    imported.assert_called_once_with("bcn_half.captchano_demo")
    imported.return_value.run_demo.assert_called_once_with(keep_open=False)
