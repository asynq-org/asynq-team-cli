from typer.testing import CliRunner

from asynq_team_cli.main import app


def test_cli_prints_version() -> None:
    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip() == "0.1.0"

