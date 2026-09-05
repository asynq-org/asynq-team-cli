import subprocess
import sys
from pathlib import Path

import yaml
from asynq_team_core.approvals import request_approval
from asynq_team_core.database import connect_database
from asynq_team_core.paths import get_project_layout
from typer.testing import CliRunner

from asynq_team_cli import main as cli_main
from asynq_team_cli.main import app
from asynq_team_cli.workspace_context import CONFIG_DIR_ENV


def test_cli_prints_version() -> None:
    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip() == "0.1.44"


def test_init_creates_runtime_state(tmp_path) -> None:
    result = CliRunner().invoke(app, ["init", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert (tmp_path / ".team" / "config.yaml").is_file()
    assert (tmp_path / ".team" / "team.db").is_file()
    assert (tmp_path / ".team" / "rules" / "engineering.md").is_file()
    assert (tmp_path / ".team" / "policy" / "capabilities.yaml").is_file()
    assert (tmp_path / ".team" / "agents" / "george.yaml").is_file()
    assert "Initialized Asynq Team" in result.output
    assert "Created default files:" in result.output


def test_init_preserves_existing_config(tmp_path) -> None:
    config_path = tmp_path / ".team" / "config.yaml"
    config_path.parent.mkdir()
    config_path.write_text("project:\n  name: Existing\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["init", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert config_path.read_text(encoding="utf-8") == "project:\n  name: Existing\n"
    assert "Kept existing config" in result.output


def test_init_writes_git_backup_config(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "init",
            "--workspace",
            str(tmp_path),
            "--no-git-backup",
            "--git-remote",
            "git@github.com:example/team-state.git",
        ],
    )
    config = yaml.safe_load((tmp_path / ".team" / "config.yaml").read_text(encoding="utf-8"))

    assert result.exit_code == 0
    assert config["git"]["enabled"] is False
    assert config["git"]["remote"] == "git@github.com:example/team-state.git"
    assert "Git backup: disabled" in result.output
    assert "Git remote: git@github.com:example/team-state.git" in result.output


def test_init_preserves_existing_default_files(tmp_path) -> None:
    policy_path = tmp_path / ".team" / "policy" / "capabilities.yaml"
    policy_path.parent.mkdir(parents=True)
    policy_path.write_text("custom: true\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["init", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert policy_path.read_text(encoding="utf-8") == "custom: true\n"


def test_init_can_overwrite_existing_default_files(tmp_path) -> None:
    policy_path = tmp_path / ".team" / "policy" / "capabilities.yaml"
    policy_path.parent.mkdir(parents=True)
    policy_path.write_text("custom: true\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["init", "--workspace", str(tmp_path), "--overwrite-defaults"],
    )

    assert result.exit_code == 0
    assert "roles:" in policy_path.read_text(encoding="utf-8")


def test_onboard_accepts_non_interactive_options(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "onboard",
            "--workspace",
            str(tmp_path),
            "--project-name",
            "Smoke Company",
            "--git-remote",
            "git@github.com:example/team-state.git",
            "--default-model",
            "gpt-5-codex-large",
            "--engineer-name",
            "Ada",
            "--supervisor-name",
            "Grace",
            "--ea-name",
            "Mina",
            "--yes",
        ],
    )

    config = yaml.safe_load((tmp_path / ".team" / "config.yaml").read_text(encoding="utf-8"))
    george = yaml.safe_load((tmp_path / ".team" / "agents" / "george.yaml").read_text())
    supervisor = yaml.safe_load(
        (tmp_path / ".team" / "agents" / "supervisor.yaml").read_text()
    )
    ea = yaml.safe_load((tmp_path / ".team" / "agents" / "ea.yaml").read_text())
    runners = yaml.safe_load((tmp_path / ".team" / "policy" / "runners.yaml").read_text())

    assert result.exit_code == 0
    assert "Onboarded Asynq Team" in result.output
    assert config["project"]["name"] == "Smoke Company"
    assert config["git"]["remote"] == "git@github.com:example/team-state.git"
    assert george["display_name"] == "Ada"
    assert george["runner"]["default_model"] == "gpt-5-codex-large"
    assert "gpt-5-codex-large" in george["runner"]["allowed_models"]
    assert supervisor["display_name"] == "Grace"
    assert ea["display_name"] == "Mina"
    assert "gpt-5-codex-large" in runners["runners"]["codex"]["allowed_models"]


def test_onboard_prompts_for_missing_values(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        ["onboard", "--workspace", str(tmp_path)],
        input="Prompt Company\n\ngpt-5-codex\nBuilder\nReviewer\nAssistant\n",
    )

    config = yaml.safe_load((tmp_path / ".team" / "config.yaml").read_text(encoding="utf-8"))
    george = yaml.safe_load((tmp_path / ".team" / "agents" / "george.yaml").read_text())

    assert result.exit_code == 0
    assert config["project"]["name"] == "Prompt Company"
    assert george["display_name"] == "Builder"


def test_workspace_context_supports_commands_without_workspace_option(tmp_path) -> None:
    runner = CliRunner()
    workspace = tmp_path / "workspace"
    config_dir = tmp_path / "config"
    workspace.mkdir()
    env = {CONFIG_DIR_ENV: str(config_dir)}

    init_result = runner.invoke(app, ["init", "--workspace", str(workspace)], env=env)
    use_result = runner.invoke(app, ["workspace", "use", str(workspace)], env=env)
    current_result = runner.invoke(app, ["workspace", "current"], env=env)
    task_result = runner.invoke(
        app,
        ["task", "create", "Smoke task", "--brief", "Verify context."],
        env=env,
    )

    assert init_result.exit_code == 0
    assert use_result.exit_code == 0
    assert f"Workspace context: {workspace.resolve()}" in use_result.output
    assert current_result.exit_code == 0
    assert current_result.output.strip() == str(workspace.resolve())
    assert task_result.exit_code == 0
    assert "TASK-0001 Smoke task" in task_result.output
    assert (workspace / ".team" / "tasks" / "TASK-0001" / "brief.md").is_file()


def test_status_reports_initialized_workspace(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0

    result = runner.invoke(app, ["status", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert "Team dir: ok" in result.output
    assert "Config: ok" in result.output
    assert "Database: ok" in result.output
    assert "Project: Asynq Team" in result.output


def test_agent_show_prints_runner_model_settings(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0

    result = runner.invoke(app, ["agent", "show", "george", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert "ID: george" in result.output
    assert "Role: engineer" in result.output
    assert "Runner: codex" in result.output
    assert "Default model: gpt-5-codex" in result.output
    assert "Allowed models: gpt-5-codex" in result.output


def test_doctor_reports_initialized_workspace_with_warnings(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0

    result = runner.invoke(app, ["doctor", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert "pass  config" in result.output
    assert "pass  migrations" in result.output
    assert "warn  git_backup" in result.output


def test_doctor_fails_for_uninitialized_workspace(tmp_path) -> None:
    result = CliRunner().invoke(app, ["doctor", "--workspace", str(tmp_path)])

    assert result.exit_code == 1
    assert "fail  config" in result.output
    assert "fail  database" in result.output


def test_config_show_prints_runtime_config(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0

    result = runner.invoke(app, ["config", "show", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert "project:" in result.output
    assert "name: Asynq Team" in result.output
    assert "storage:" in result.output
    assert "adapter: sqlite" in result.output


def test_config_show_reports_missing_config(tmp_path) -> None:
    result = CliRunner().invoke(app, ["config", "show", "--workspace", str(tmp_path)])

    assert result.exit_code == 1
    assert "Config not found:" in result.output


def test_backup_run_writes_database_backup(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0

    result = runner.invoke(app, ["backup", "run", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert ".team/backups/team-" in result.output
    assert "bytes" in result.output
    assert len(list((tmp_path / ".team" / "backups").glob("team-*.db"))) == 1


def test_backup_list_shows_database_backups(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0
    assert runner.invoke(app, ["backup", "run", "--workspace", str(tmp_path)]).exit_code == 0

    result = runner.invoke(app, ["backup", "list", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert ".team/backups/team-" in result.output
    assert "bytes" in result.output


def test_backup_run_reports_missing_database(tmp_path) -> None:
    result = CliRunner().invoke(app, ["backup", "run", "--workspace", str(tmp_path)])

    assert result.exit_code == 1
    assert "Database is missing:" in result.output


def test_audit_show_lists_task_scoped_events(tmp_path) -> None:
    runner = CliRunner()
    _create_cli_run(runner, tmp_path)
    assert (
        runner.invoke(
            app,
            [
                "task",
                "comment",
                "TASK-0001",
                "Please review.",
                "--workspace",
                str(tmp_path),
            ],
        ).exit_code
        == 0
    )

    result = runner.invoke(app, ["audit", "show", "TASK-0001", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert "task.created" in result.output
    assert "run.created" in result.output
    assert "comment.created" in result.output


def test_audit_show_reports_missing_task(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0

    result = runner.invoke(app, ["audit", "show", "TASK-9999", "--workspace", str(tmp_path)])

    assert result.exit_code == 1
    assert "Task not found: TASK-9999" in result.output


def test_policy_check_reports_allowed_capability(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0

    result = runner.invoke(
        app,
        ["policy", "check", "george", "repo.read", "--workspace", str(tmp_path)],
    )

    assert result.exit_code == 0
    assert "george  engineer  repo.read  allow" in result.output
    assert "Capability is allowed for role: engineer" in result.output


def test_policy_check_reports_approval_requirement(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0

    result = runner.invoke(
        app,
        ["policy", "check", "george", "main.merge", "--workspace", str(tmp_path)],
    )

    assert result.exit_code == 0
    assert "george  engineer  main.merge  require_approval" in result.output


def test_policy_check_reports_missing_agent(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0

    result = runner.invoke(
        app,
        ["policy", "check", "missing", "repo.read", "--workspace", str(tmp_path)],
    )

    assert result.exit_code == 1
    assert "Agent manifest not found" in result.output


def test_policy_authorize_allows_without_approval(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0

    result = runner.invoke(
        app,
        [
            "policy",
            "authorize",
            "george",
            "repo.read",
            "Read project context.",
            "--workspace",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert "george  engineer  repo.read  allow" in result.output
    assert "Approval: not required" in result.output


def test_policy_authorize_requests_approval_for_gated_capability(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0

    result = runner.invoke(
        app,
        [
            "policy",
            "authorize",
            "george",
            "main.merge",
            "Merge reviewed changes.",
            "--workspace",
            str(tmp_path),
            "--subject-type",
            "run",
            "--subject-id",
            "RUN-0001",
        ],
    )

    assert result.exit_code == 0
    assert "george  engineer  main.merge  require_approval" in result.output
    assert "Approval: APR-0001 -> founder" in result.output
    assert "Inbox: INBOX-0001 -> founder" in result.output


def test_policy_authorize_rejects_denied_capability(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0

    result = runner.invoke(
        app,
        [
            "policy",
            "authorize",
            "supervisor",
            "repo.write",
            "Write implementation changes.",
            "--workspace",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 1
    assert "Capability is denied for role: supervisor" in result.output


def test_runner_check_allows_default_tool(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0

    result = runner.invoke(
        app,
        ["runner", "check", "shell.test", "--workspace", str(tmp_path)],
    )

    assert result.exit_code == 0
    assert "shell.test  allow" in result.output
    assert "Runner tool is allowed: shell.test" in result.output


def test_runner_check_denies_default_denied_tool(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0

    result = runner.invoke(
        app,
        ["runner", "check", "shell.destructive", "--workspace", str(tmp_path)],
    )

    assert result.exit_code == 0
    assert "shell.destructive  deny" in result.output
    assert "Runner tool is denied: shell.destructive" in result.output


def test_runner_exec_runs_allowed_command_and_records_audit(tmp_path) -> None:
    runner = CliRunner()
    _create_cli_run(runner, tmp_path)

    result = runner.invoke(
        app,
        [
            "runner",
            "exec",
            "RUN-0001",
            "shell.test",
            "--workspace",
            str(tmp_path),
            "--",
            sys.executable,
            "-c",
            "print('ok')",
        ],
    )
    audit_result = runner.invoke(app, ["audit", "show", "TASK-0001", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert "RUN-0001  shell.test  exit_code=0" in result.output
    assert "Stdout:\nok" in result.output
    assert "run.command_executed" in audit_result.output
    assert "tool=shell.test" in audit_result.output


def test_runner_exec_rejects_denied_tool(tmp_path) -> None:
    runner = CliRunner()
    _create_cli_run(runner, tmp_path)

    result = runner.invoke(
        app,
        [
            "runner",
            "exec",
            "RUN-0001",
            "shell.destructive",
            "--workspace",
            str(tmp_path),
            "--",
            sys.executable,
            "-c",
            "print('unsafe')",
        ],
    )

    assert result.exit_code == 1
    assert "Runner tool is denied: shell.destructive" in result.output


def test_runner_exec_returns_command_exit_code(tmp_path) -> None:
    runner = CliRunner()
    _create_cli_run(runner, tmp_path)

    result = runner.invoke(
        app,
        [
            "runner",
            "exec",
            "RUN-0001",
            "shell.test",
            "--workspace",
            str(tmp_path),
            "--",
            sys.executable,
            "-c",
            "raise SystemExit(3)",
        ],
    )

    assert result.exit_code == 3
    assert "RUN-0001  shell.test  exit_code=3" in result.output


def test_inbox_lists_attention_items(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0
    _request_merge_approval(tmp_path)

    result = runner.invoke(app, ["inbox", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert "INBOX-0001" in result.output
    assert "approval" in result.output
    assert "Approval required: main.merge" in result.output


def test_approvals_lists_pending_approvals(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0
    _request_merge_approval(tmp_path)

    result = runner.invoke(app, ["approvals", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert "APR-0001" in result.output
    assert "pending" in result.output
    assert "main.merge" in result.output


def test_approval_show_prints_approval_detail(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0
    _request_merge_approval(tmp_path)

    result = runner.invoke(app, ["approval", "show", "APR-0001", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert "ID: APR-0001" in result.output
    assert "Action: main.merge" in result.output
    assert "Status: pending" in result.output
    assert "Requester: agent:george" in result.output


def test_approval_show_reports_missing_approval(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0

    result = runner.invoke(app, ["approval", "show", "APR-9999", "--workspace", str(tmp_path)])

    assert result.exit_code == 1
    assert "Approval not found: APR-9999" in result.output


def test_approvals_approve_marks_approval_done(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0
    _request_merge_approval(tmp_path)

    approve_result = runner.invoke(
        app,
        [
            "approvals",
            "approve",
            "APR-0001",
            "--workspace",
            str(tmp_path),
            "--reason",
            "Reviewed.",
        ],
    )
    approvals_result = runner.invoke(
        app,
        ["approvals", "--workspace", str(tmp_path), "--status", "granted"],
    )
    inbox_result = runner.invoke(app, ["inbox", "--workspace", str(tmp_path)])

    assert approve_result.exit_code == 0
    assert "Approved APR-0001" in approve_result.output
    assert approvals_result.exit_code == 0
    assert "granted" in approvals_result.output
    assert inbox_result.output.strip() == "No inbox items."


def test_approve_marks_approval_done(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0
    _request_merge_approval(tmp_path)

    approve_result = runner.invoke(
        app,
        ["approve", "APR-0001", "--workspace", str(tmp_path), "--reason", "Reviewed."],
    )
    approval_result = runner.invoke(
        app,
        ["approval", "show", "APR-0001", "--workspace", str(tmp_path)],
    )

    assert approve_result.exit_code == 0
    assert "Approved APR-0001" in approve_result.output
    assert approval_result.exit_code == 0
    assert "Status: granted" in approval_result.output
    assert "Decision reason: Reviewed." in approval_result.output


def test_approvals_deny_marks_approval_denied(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0
    _request_merge_approval(tmp_path)

    deny_result = runner.invoke(
        app,
        ["approvals", "deny", "APR-0001", "--workspace", str(tmp_path)],
    )
    approvals_result = runner.invoke(
        app,
        ["approvals", "--workspace", str(tmp_path), "--status", "denied"],
    )

    assert deny_result.exit_code == 0
    assert "Denied APR-0001" in deny_result.output
    assert approvals_result.exit_code == 0
    assert "denied" in approvals_result.output


def test_deny_marks_approval_denied(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0
    _request_merge_approval(tmp_path)

    deny_result = runner.invoke(app, ["deny", "APR-0001", "--workspace", str(tmp_path)])
    approval_result = runner.invoke(
        app,
        ["approval", "show", "APR-0001", "--workspace", str(tmp_path)],
    )

    assert deny_result.exit_code == 0
    assert "Denied APR-0001" in deny_result.output
    assert approval_result.exit_code == 0
    assert "Status: denied" in approval_result.output


def test_task_create_writes_task_and_brief(tmp_path) -> None:
    init_result = CliRunner().invoke(app, ["init", "--workspace", str(tmp_path)])
    assert init_result.exit_code == 0

    result = CliRunner().invoke(
        app,
        [
            "task",
            "create",
            "First task",
            "--brief",
            "Build the first task.",
            "--workspace",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert "TASK-0001 First task" in result.output
    assert (tmp_path / ".team" / "tasks" / "TASK-0001" / "brief.md").read_text(
        encoding="utf-8"
    ) == "Build the first task.\n"


def test_task_create_agent_requests_approval_when_gated(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0
    _replace_engineer_capability_policy(tmp_path, "task.create", "require_approval")

    result = runner.invoke(
        app,
        [
            "task",
            "create",
            "Agent task",
            "--workspace",
            str(tmp_path),
            "--actor-type",
            "agent",
            "--actor-id",
            "george",
        ],
    )
    list_result = runner.invoke(app, ["task", "list", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert "george  task.create  require_approval" in result.output
    assert "Approval: APR-0001 -> founder" in result.output
    assert "Inbox: INBOX-0001 -> founder" in result.output
    assert list_result.output.strip() == "No tasks."


def test_task_create_agent_rejects_denied_capability(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0
    _replace_engineer_capability_policy(tmp_path, "task.create", "deny")

    result = runner.invoke(
        app,
        [
            "task",
            "create",
            "Agent task",
            "--workspace",
            str(tmp_path),
            "--actor-type",
            "agent",
            "--actor-id",
            "george",
        ],
    )

    assert result.exit_code == 1
    assert "Capability is denied for role: engineer" in result.output


def test_task_follow_up_creates_linked_task_and_brief(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0
    assert (
        runner.invoke(app, ["task", "create", "First task", "--workspace", str(tmp_path)])
        .exit_code
        == 0
    )

    result = runner.invoke(
        app,
        [
            "task",
            "follow-up",
            "TASK-0001",
            "Refine review checklist",
            "--brief",
            "Capture a review checklist.",
            "--workspace",
            str(tmp_path),
        ],
    )
    show_parent = runner.invoke(app, ["task", "show", "TASK-0001", "--workspace", str(tmp_path)])
    show_follow_up = runner.invoke(
        app,
        ["task", "show", "TASK-0002", "--workspace", str(tmp_path)],
    )

    assert result.exit_code == 0
    assert "TASK-0002 Refine review checklist" in result.output
    assert "Parent: TASK-0001" in result.output
    assert "Brief: .team/tasks/TASK-0002/brief.md" in result.output
    assert "Follow-ups:" in show_parent.output
    assert "- TASK-0002  created  Refine review checklist" in show_parent.output
    assert "Parent: TASK-0001" in show_follow_up.output
    assert (tmp_path / ".team" / "tasks" / "TASK-0002" / "brief.md").read_text(
        encoding="utf-8"
    ) == "Capture a review checklist.\n"


def test_task_follow_up_requests_approval_when_gated(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0
    assert (
        runner.invoke(app, ["task", "create", "First task", "--workspace", str(tmp_path)])
        .exit_code
        == 0
    )
    _replace_engineer_capability_policy(tmp_path, "task.create", "require_approval")

    result = runner.invoke(
        app,
        [
            "task",
            "follow-up",
            "TASK-0001",
            "Refine review checklist",
            "--workspace",
            str(tmp_path),
        ],
    )
    list_result = runner.invoke(app, ["task", "list", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert "george  task.create  require_approval" in result.output
    assert "Approval: APR-0001 -> founder" in result.output
    assert "TASK-0002" not in list_result.output


def test_task_follow_up_reports_missing_parent(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0

    result = runner.invoke(
        app,
        [
            "task",
            "follow-up",
            "TASK-9999",
            "Refine review checklist",
            "--workspace",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 1
    assert "Parent task not found: TASK-9999" in result.output


def test_task_list_shows_created_tasks(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0
    assert (
        runner.invoke(app, ["task", "create", "First task", "--workspace", str(tmp_path)])
        .exit_code
        == 0
    )

    result = runner.invoke(app, ["task", "list", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert "TASK-0001" in result.output
    assert "First task" in result.output


def test_task_show_reports_missing_task(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0

    result = runner.invoke(app, ["task", "show", "TASK-9999", "--workspace", str(tmp_path)])

    assert result.exit_code == 1
    assert "Task not found: TASK-9999" in result.output


def test_task_status_updates_task_status(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0
    assert (
        runner.invoke(app, ["task", "create", "First task", "--workspace", str(tmp_path)])
        .exit_code
        == 0
    )

    status_result = runner.invoke(
        app,
        ["task", "status", "TASK-0001", "in_progress", "--workspace", str(tmp_path)],
    )
    show_result = runner.invoke(app, ["task", "show", "TASK-0001", "--workspace", str(tmp_path)])

    assert status_result.exit_code == 0
    assert "TASK-0001  in_progress" in status_result.output
    assert show_result.exit_code == 0
    assert "Status: in_progress" in show_result.output


def test_task_status_reports_missing_task(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0

    result = runner.invoke(
        app,
        ["task", "status", "TASK-9999", "in_progress", "--workspace", str(tmp_path)],
    )

    assert result.exit_code == 1
    assert "Task not found: TASK-9999" in result.output


def test_task_comment_creates_comment_and_mentions(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0
    assert (
        runner.invoke(app, ["task", "create", "First task", "--workspace", str(tmp_path)])
        .exit_code
        == 0
    )

    result = runner.invoke(
        app,
        [
            "task",
            "comment",
            "TASK-0001",
            "Please review this.",
            "--mention",
            "supervisor",
            "--workspace",
            str(tmp_path),
        ],
    )
    inbox_result = runner.invoke(
        app,
        ["inbox", "--workspace", str(tmp_path), "--recipient-id", "supervisor"],
    )

    assert result.exit_code == 0
    assert "CMT-0001 TASK-0001" in result.output
    assert "Mentions: 1" in result.output
    assert "INBOX-0001" in inbox_result.output
    assert "mention" in inbox_result.output


def test_task_comment_agent_requests_approval_when_gated(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0
    assert (
        runner.invoke(app, ["task", "create", "First task", "--workspace", str(tmp_path)])
        .exit_code
        == 0
    )
    _replace_engineer_capability_policy(tmp_path, "comment.create", "require_approval")

    result = runner.invoke(
        app,
        [
            "task",
            "comment",
            "TASK-0001",
            "Please review this.",
            "--workspace",
            str(tmp_path),
            "--actor-type",
            "agent",
            "--actor-id",
            "george",
        ],
    )
    comments_result = runner.invoke(app, ["task", "comments", "TASK-0001", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert "george  comment.create  require_approval" in result.output
    assert "Approval: APR-0001 -> founder" in result.output
    assert "Inbox: INBOX-0001 -> founder" in result.output
    assert comments_result.output.strip() == "No comments."


def test_task_comment_agent_rejects_denied_capability(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0
    assert (
        runner.invoke(app, ["task", "create", "First task", "--workspace", str(tmp_path)])
        .exit_code
        == 0
    )
    _replace_engineer_capability_policy(tmp_path, "comment.create", "deny")

    result = runner.invoke(
        app,
        [
            "task",
            "comment",
            "TASK-0001",
            "Please review this.",
            "--workspace",
            str(tmp_path),
            "--actor-type",
            "agent",
            "--actor-id",
            "george",
        ],
    )

    assert result.exit_code == 1
    assert "Capability is denied for role: engineer" in result.output


def test_task_comments_lists_task_comments(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0
    assert (
        runner.invoke(app, ["task", "create", "First task", "--workspace", str(tmp_path)])
        .exit_code
        == 0
    )
    assert (
        runner.invoke(
            app,
            [
                "task",
                "comment",
                "TASK-0001",
                "First comment.",
                "--workspace",
                str(tmp_path),
            ],
        ).exit_code
        == 0
    )

    result = runner.invoke(app, ["task", "comments", "TASK-0001", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert "CMT-0001" in result.output
    assert "First comment." in result.output


def test_run_create_writes_run_and_artifact_dir(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0
    assert (
        runner.invoke(app, ["task", "create", "First task", "--workspace", str(tmp_path)])
        .exit_code
        == 0
    )

    result = runner.invoke(
        app,
        [
            "run",
            "create",
            "TASK-0001",
            "--agent-id",
            "george",
            "--workspace",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert "RUN-0001  TASK-0001  george  created" in result.output
    assert "Runner: codex" in result.output
    assert "Model: gpt-5-codex" in result.output
    assert "Artifacts: .team/runs/george/RUN-0001" in result.output
    assert (tmp_path / ".team" / "runs" / "george" / "RUN-0001").is_dir()


def test_run_task_creates_run_and_work_packet(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0
    assert (
        runner.invoke(app, ["task", "create", "First task", "--workspace", str(tmp_path)])
        .exit_code
        == 0
    )

    result = runner.invoke(
        app,
        [
            "run",
            "task",
            "TASK-0001",
            "--agent-id",
            "george",
            "--workspace",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert "RUN-0001  TASK-0001  george  working" in result.output
    assert "Model: gpt-5-codex" in result.output
    assert "Artifacts: .team/runs/george/RUN-0001" in result.output
    assert "Work packet: .team/runs/george/RUN-0001/work.md" in result.output
    work_packet = tmp_path / ".team" / "runs" / "george" / "RUN-0001" / "work.md"
    assert work_packet.is_file()
    work_packet_body = work_packet.read_text(encoding="utf-8")
    assert "First task" in work_packet_body
    assert "- Model: gpt-5-codex" in work_packet_body


def test_run_task_requests_artifact_approval_when_gated(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0
    assert (
        runner.invoke(app, ["task", "create", "First task", "--workspace", str(tmp_path)])
        .exit_code
        == 0
    )
    _replace_engineer_capability_policy(tmp_path, "artifact.create", "require_approval")

    result = runner.invoke(
        app,
        [
            "run",
            "task",
            "TASK-0001",
            "--agent-id",
            "george",
            "--workspace",
            str(tmp_path),
        ],
    )
    list_result = runner.invoke(app, ["run", "list", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert "george  artifact.create  require_approval" in result.output
    assert "Approval: APR-0001 -> founder" in result.output
    assert "No runs." in list_result.output
    assert not (tmp_path / ".team" / "runs" / "george" / "RUN-0001" / "work.md").exists()


def test_worker_run_once_starts_next_task(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0
    assert (
        runner.invoke(app, ["task", "create", "First task", "--workspace", str(tmp_path)])
        .exit_code
        == 0
    )

    result = runner.invoke(app, ["worker", "run-once", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert "ea: routed TASK-0001 -> george" in result.output
    assert "george: task TASK-0001" in result.output
    assert "Task: TASK-0001  in_progress  First task" in result.output
    assert "Run: RUN-0001  working  george" in result.output
    assert "Runner: codex" in result.output
    assert "Model: gpt-5-codex" in result.output
    assert "Work packet: .team/runs/george/RUN-0001/work.md" in result.output


def test_worker_run_once_can_be_limited_to_one_agent(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0
    assert (
        runner.invoke(app, ["task", "create", "First task", "--workspace", str(tmp_path)])
        .exit_code
        == 0
    )

    result = runner.invoke(
        app,
        ["worker", "run-once", "--agent", "george", "--workspace", str(tmp_path)],
    )

    assert result.exit_code == 0
    assert "george: no available tasks." in result.output
    assert "ea: routed" not in result.output


def test_worker_run_once_reports_empty_queue(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0

    result = runner.invoke(app, ["worker", "run-once", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert "ea: no available tasks." in result.output
    assert "george: no available tasks." in result.output
    assert "supervisor: no available tasks." in result.output


def test_worker_start_supports_bounded_iterations(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0

    result = runner.invoke(
        app,
        [
            "worker",
            "start",
            "--iterations",
            "1",
            "--workspace",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert "Worker started for agents: ea, george, supervisor." in result.output
    assert "ea: no available tasks." in result.output


def test_worker_status_reports_no_daemon(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0

    result = runner.invoke(app, ["worker", "status", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert "No worker daemon." in result.output


def test_worker_stop_removes_stale_pid(tmp_path, monkeypatch) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0
    worker_dir = tmp_path / ".team" / "worker"
    worker_dir.mkdir()
    (worker_dir / "worker.pid").write_text("424242\n", encoding="utf-8")
    monkeypatch.setattr(cli_main, "_pid_is_running", lambda pid: False)

    result = runner.invoke(app, ["worker", "stop", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert "Removed stale worker daemon pid: 424242" in result.output
    assert not (worker_dir / "worker.pid").exists()


def test_worker_start_daemon_writes_pid_and_command(tmp_path, monkeypatch) -> None:
    runner = CliRunner()
    captured = {}

    class FakeProcess:
        pid = 4242

        def __init__(self, command, **kwargs):
            captured["command"] = command
            captured["kwargs"] = kwargs

    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0
    monkeypatch.setattr(cli_main.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(cli_main, "_pid_is_running", lambda pid: False)

    result = runner.invoke(
        app,
        [
            "worker",
            "start",
            "--daemon",
            "--agent",
            "george",
            "--iterations",
            "1",
            "--poll-seconds",
            "0.1",
            "--workspace",
            str(tmp_path),
        ],
    )

    command = captured["command"]
    assert result.exit_code == 0
    assert "Worker daemon started: 4242" in result.output
    assert (tmp_path / ".team" / "worker" / "worker.pid").read_text() == "4242\n"
    assert command[:3] == [sys.executable, "-m", "asynq_team_cli.main"]
    assert "--daemon" not in command
    assert "--agent-id" in command
    assert "george" in command
    assert captured["kwargs"]["start_new_session"] is True


def test_run_task_reports_missing_task(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0

    result = runner.invoke(
        app,
        [
            "run",
            "task",
            "TASK-9999",
            "--agent-id",
            "george",
            "--workspace",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 1
    assert "Task not found: TASK-9999" in result.output


def test_run_list_shows_created_runs(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0
    assert (
        runner.invoke(app, ["task", "create", "First task", "--workspace", str(tmp_path)])
        .exit_code
        == 0
    )
    assert (
        runner.invoke(
            app,
            [
                "run",
                "create",
                "TASK-0001",
                "--agent-id",
                "george",
                "--workspace",
                str(tmp_path),
            ],
        ).exit_code
        == 0
    )

    result = runner.invoke(app, ["run", "list", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert "RUN-0001  created  TASK-0001  george" in result.output


def test_run_next_shows_next_actionable_agent_run(tmp_path) -> None:
    runner = CliRunner()
    _create_cli_run(runner, tmp_path)

    result = runner.invoke(
        app,
        [
            "run",
            "next",
            "--agent",
            "george",
            "--workspace",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert "RUN-0001  created  TASK-0001  First task" in result.output
    assert "Artifacts: .team/runs/george/RUN-0001" in result.output


def test_run_next_reports_empty_queue(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0

    result = runner.invoke(
        app,
        [
            "run",
            "next",
            "--agent",
            "george",
            "--workspace",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert "No actionable runs." in result.output


def test_run_status_updates_run(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0
    assert (
        runner.invoke(app, ["task", "create", "First task", "--workspace", str(tmp_path)])
        .exit_code
        == 0
    )
    assert (
        runner.invoke(
            app,
            [
                "run",
                "create",
                "TASK-0001",
                "--agent-id",
                "george",
                "--workspace",
                str(tmp_path),
            ],
        ).exit_code
        == 0
    )

    status_result = runner.invoke(
        app,
        [
            "run",
            "status",
            "RUN-0001",
            "planning",
            "--workspace",
            str(tmp_path),
            "--actor-type",
            "agent",
            "--actor-id",
            "george",
        ],
    )
    show_result = runner.invoke(app, ["run", "show", "RUN-0001", "--workspace", str(tmp_path)])

    assert status_result.exit_code == 0
    assert "RUN-0001  planning" in status_result.output
    assert show_result.exit_code == 0
    assert "Status: planning" in show_result.output


def test_run_show_reports_missing_run(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0

    result = runner.invoke(app, ["run", "show", "RUN-9999", "--workspace", str(tmp_path)])

    assert result.exit_code == 1
    assert "Run not found: RUN-9999" in result.output


def test_run_work_writes_packet_and_marks_run_working(tmp_path) -> None:
    runner = CliRunner()
    _create_cli_run(runner, tmp_path)

    result = runner.invoke(app, ["run", "work", "RUN-0001", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert "RUN-0001  working" in result.output
    assert "Work packet: .team/runs/george/RUN-0001/work.md" in result.output
    work_packet = tmp_path / ".team" / "runs" / "george" / "RUN-0001" / "work.md"
    assert work_packet.is_file()
    assert "Build the first task." in work_packet.read_text(encoding="utf-8")


def test_run_work_requests_artifact_approval_when_gated(tmp_path) -> None:
    runner = CliRunner()
    _create_cli_run(runner, tmp_path)
    _replace_engineer_capability_policy(tmp_path, "artifact.create", "require_approval")

    result = runner.invoke(app, ["run", "work", "RUN-0001", "--workspace", str(tmp_path)])
    show_result = runner.invoke(app, ["run", "show", "RUN-0001", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert "george  artifact.create  require_approval" in result.output
    assert "Approval: APR-0001 -> founder" in result.output
    assert "Status: created" in show_result.output
    assert not (tmp_path / ".team" / "runs" / "george" / "RUN-0001" / "work.md").exists()


def test_run_work_resumes_after_granted_artifact_approval(tmp_path) -> None:
    runner = CliRunner()
    _create_cli_run(runner, tmp_path)
    _replace_engineer_capability_policy(tmp_path, "artifact.create", "require_approval")

    blocked_result = runner.invoke(app, ["run", "work", "RUN-0001", "--workspace", str(tmp_path)])
    approve_result = runner.invoke(app, ["approve", "APR-0001", "--workspace", str(tmp_path)])
    resumed_result = runner.invoke(app, ["run", "work", "RUN-0001", "--workspace", str(tmp_path)])
    approvals_result = runner.invoke(
        app,
        ["approvals", "--workspace", str(tmp_path), "--status", "granted"],
    )

    assert blocked_result.exit_code == 0
    assert "Approval: APR-0001 -> founder" in blocked_result.output
    assert approve_result.exit_code == 0
    assert resumed_result.exit_code == 0
    assert "RUN-0001  working" in resumed_result.output
    assert "Approval: APR-0002" not in resumed_result.output
    assert approvals_result.output.count("APR-") == 1


def test_run_work_preserves_existing_packet_by_default(tmp_path) -> None:
    runner = CliRunner()
    _create_cli_run(runner, tmp_path)
    assert (
        runner.invoke(app, ["run", "work", "RUN-0001", "--workspace", str(tmp_path)]).exit_code
        == 0
    )

    result = runner.invoke(app, ["run", "work", "RUN-0001", "--workspace", str(tmp_path)])

    assert result.exit_code == 1
    assert "Run work packet already exists" in result.output


def test_run_command_records_command_audit_event(tmp_path) -> None:
    runner = CliRunner()
    _create_cli_run(runner, tmp_path)

    result = runner.invoke(
        app,
        [
            "run",
            "command",
            "RUN-0001",
            "poetry run pytest",
            "--exit-code",
            "0",
            "--cwd",
            "repos/core",
            "--duration-ms",
            "1250",
            "--workspace",
            str(tmp_path),
        ],
    )
    audit_result = runner.invoke(app, ["audit", "show", "TASK-0001", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert "RUN-0001  command  0" in result.output
    assert "Event: EVT-" in result.output
    assert "run.command_executed" in audit_result.output
    assert "command=poetry run pytest exit_code=0" in audit_result.output


def test_run_command_reports_missing_run(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0

    result = runner.invoke(
        app,
        [
            "run",
            "command",
            "RUN-9999",
            "poetry run pytest",
            "--workspace",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 1
    assert "Run not found: RUN-9999" in result.output


def test_run_file_records_file_change_audit_event(tmp_path) -> None:
    runner = CliRunner()
    _create_cli_run(runner, tmp_path)

    result = runner.invoke(
        app,
        [
            "run",
            "file",
            "RUN-0001",
            "repos/core/src/example.py",
            "--change",
            "modified",
            "--additions",
            "12",
            "--deletions",
            "3",
            "--workspace",
            str(tmp_path),
        ],
    )
    audit_result = runner.invoke(app, ["audit", "show", "TASK-0001", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert "RUN-0001  file  modified" in result.output
    assert "Path: repos/core/src/example.py" in result.output
    assert "Event: EVT-" in result.output
    assert "run.file_changed" in audit_result.output
    assert "modified repos/core/src/example.py" in audit_result.output


def test_run_file_rejects_path_outside_workspace(tmp_path) -> None:
    runner = CliRunner()
    _create_cli_run(runner, tmp_path)

    result = runner.invoke(
        app,
        [
            "run",
            "file",
            "RUN-0001",
            "../outside.py",
            "--workspace",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 1
    assert "relative_path escapes the workspace" in result.output


def test_run_audit_git_records_git_diff_file_changes(tmp_path) -> None:
    runner = CliRunner()
    _create_cli_run(runner, tmp_path)
    repo = tmp_path / "repos" / "core"
    source = repo / "src" / "example.py"
    source.parent.mkdir(parents=True)
    _run_git(repo, "init")
    _run_git(repo, "config", "user.email", "founder@example.local")
    _run_git(repo, "config", "user.name", "Founder")
    source.write_text("print('before')\n", encoding="utf-8")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "Initial")
    source.write_text("print('after')\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "run",
            "audit-git",
            "RUN-0001",
            "--repo",
            "repos/core",
            "--workspace",
            str(tmp_path),
        ],
    )
    audit_result = runner.invoke(app, ["audit", "show", "TASK-0001", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert "Recorded 1 file change(s)." in result.output
    assert "modified  repos/core/src/example.py" in result.output
    assert "run.file_changed" in audit_result.output
    assert "modified repos/core/src/example.py" in audit_result.output


def test_run_audit_git_records_untracked_files_without_head(tmp_path) -> None:
    runner = CliRunner()
    _create_cli_run(runner, tmp_path)
    repo = tmp_path / "repos" / "core"
    source = repo / "src" / "example.py"
    source.parent.mkdir(parents=True)
    _run_git(repo, "init")
    source.write_text("print('new')\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "run",
            "audit-git",
            "RUN-0001",
            "--repo",
            "repos/core",
            "--workspace",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert "Recorded 1 file change(s)." in result.output
    assert "added  repos/core/src/example.py" in result.output


def test_run_submit_writes_result_and_mentions_reviewer(tmp_path) -> None:
    runner = CliRunner()
    _create_cli_run(runner, tmp_path)
    assert (
        runner.invoke(app, ["run", "work", "RUN-0001", "--workspace", str(tmp_path)]).exit_code
        == 0
    )

    result = runner.invoke(
        app,
        [
            "run",
            "submit",
            "RUN-0001",
            "Implemented the first pass.",
            "--checks",
            "- poetry run pytest",
            "--workspace",
            str(tmp_path),
        ],
    )
    inbox_result = runner.invoke(
        app,
        ["inbox", "--workspace", str(tmp_path), "--recipient-id", "supervisor"],
    )

    assert result.exit_code == 0
    assert "RUN-0001  waiting_for_review" in result.output
    assert "Result: .team/runs/george/RUN-0001/result.md" in result.output
    assert "Review request: CMT-0001 -> supervisor" in result.output
    result_artifact = tmp_path / ".team" / "runs" / "george" / "RUN-0001" / "result.md"
    assert result_artifact.is_file()
    assert "- poetry run pytest" in result_artifact.read_text(encoding="utf-8")
    assert "Mention on TASK-0001" in inbox_result.output


def test_run_submit_requests_comment_approval_when_gated(tmp_path) -> None:
    runner = CliRunner()
    _create_cli_run(runner, tmp_path)
    assert (
        runner.invoke(app, ["run", "work", "RUN-0001", "--workspace", str(tmp_path)]).exit_code
        == 0
    )
    _replace_engineer_capability_policy(tmp_path, "comment.create", "require_approval")

    result = runner.invoke(
        app,
        [
            "run",
            "submit",
            "RUN-0001",
            "Implemented the first pass.",
            "--workspace",
            str(tmp_path),
        ],
    )
    show_result = runner.invoke(app, ["run", "show", "RUN-0001", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert "george  comment.create  require_approval" in result.output
    assert "Approval: APR-0001 -> founder" in result.output
    assert "Status: working" in show_result.output
    assert not (tmp_path / ".team" / "runs" / "george" / "RUN-0001" / "result.md").exists()


def test_run_submit_requests_artifact_approval_when_gated(tmp_path) -> None:
    runner = CliRunner()
    _create_cli_run(runner, tmp_path)
    assert (
        runner.invoke(app, ["run", "work", "RUN-0001", "--workspace", str(tmp_path)]).exit_code
        == 0
    )
    _replace_engineer_capability_policy(tmp_path, "artifact.create", "require_approval")

    result = runner.invoke(
        app,
        [
            "run",
            "submit",
            "RUN-0001",
            "Implemented the first pass.",
            "--workspace",
            str(tmp_path),
        ],
    )
    show_result = runner.invoke(app, ["run", "show", "RUN-0001", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert "george  artifact.create  require_approval" in result.output
    assert "Approval: APR-0001 -> founder" in result.output
    assert "Status: working" in show_result.output
    assert not (tmp_path / ".team" / "runs" / "george" / "RUN-0001" / "result.md").exists()


def test_run_submit_rejects_unstarted_run(tmp_path) -> None:
    runner = CliRunner()
    _create_cli_run(runner, tmp_path)

    result = runner.invoke(
        app,
        [
            "run",
            "submit",
            "RUN-0001",
            "Not ready.",
            "--workspace",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 1
    assert "Run cannot be submitted from status: created" in result.output


def test_review_approves_submitted_run_and_mentions_agent(tmp_path) -> None:
    runner = CliRunner()
    _create_submitted_cli_run(runner, tmp_path)

    result = runner.invoke(
        app,
        [
            "review",
            "RUN-0001",
            "approve",
            "Looks ready.",
            "--workspace",
            str(tmp_path),
        ],
    )
    inbox_result = runner.invoke(
        app,
        ["inbox", "--workspace", str(tmp_path), "--recipient-id", "george"],
    )
    task_result = runner.invoke(app, ["task", "show", "TASK-0001", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert "RUN-0001  approved" in result.output
    assert "Review: .team/runs/george/RUN-0001/review.md" in result.output
    assert "Agent notice: CMT-0002 -> george" in result.output
    assert task_result.exit_code == 0
    assert "Status: approved" in task_result.output
    review_artifact = tmp_path / ".team" / "runs" / "george" / "RUN-0001" / "review.md"
    assert review_artifact.is_file()
    assert "Looks ready." in review_artifact.read_text(encoding="utf-8")
    assert "Mention on TASK-0001" in inbox_result.output


def test_review_requests_comment_approval_when_gated(tmp_path) -> None:
    runner = CliRunner()
    _create_submitted_cli_run(runner, tmp_path)
    _replace_role_capability_policy(tmp_path, "supervisor", "comment.create", "require_approval")

    result = runner.invoke(
        app,
        [
            "review",
            "RUN-0001",
            "approve",
            "Looks ready.",
            "--workspace",
            str(tmp_path),
        ],
    )
    show_result = runner.invoke(app, ["run", "show", "RUN-0001", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert "supervisor  comment.create  require_approval" in result.output
    assert "Approval: APR-0001 -> founder" in result.output
    assert "Status: waiting_for_review" in show_result.output
    assert not (tmp_path / ".team" / "runs" / "george" / "RUN-0001" / "review.md").exists()


def test_review_requests_review_approval_when_gated(tmp_path) -> None:
    runner = CliRunner()
    _create_submitted_cli_run(runner, tmp_path)
    _replace_role_capability_policy(tmp_path, "supervisor", "review.create", "require_approval")

    result = runner.invoke(
        app,
        [
            "review",
            "RUN-0001",
            "approve",
            "Looks ready.",
            "--workspace",
            str(tmp_path),
        ],
    )
    show_result = runner.invoke(app, ["run", "show", "RUN-0001", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert "supervisor  review.create  require_approval" in result.output
    assert "Approval: APR-0001 -> founder" in result.output
    assert "Status: waiting_for_review" in show_result.output
    assert not (tmp_path / ".team" / "runs" / "george" / "RUN-0001" / "review.md").exists()


def test_review_requests_artifact_approval_when_gated(tmp_path) -> None:
    runner = CliRunner()
    _create_submitted_cli_run(runner, tmp_path)
    _replace_role_capability_policy(tmp_path, "supervisor", "artifact.create", "require_approval")

    result = runner.invoke(
        app,
        [
            "review",
            "RUN-0001",
            "approve",
            "Looks ready.",
            "--workspace",
            str(tmp_path),
        ],
    )
    show_result = runner.invoke(app, ["run", "show", "RUN-0001", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert "supervisor  artifact.create  require_approval" in result.output
    assert "Approval: APR-0001 -> founder" in result.output
    assert "Status: waiting_for_review" in show_result.output
    assert not (tmp_path / ".team" / "runs" / "george" / "RUN-0001" / "review.md").exists()


def test_review_rejects_unsubmitted_run(tmp_path) -> None:
    runner = CliRunner()
    _create_cli_run(runner, tmp_path)

    result = runner.invoke(
        app,
        [
            "review",
            "RUN-0001",
            "return",
            "Please submit first.",
            "--workspace",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 1
    assert "Run cannot be reviewed from status: created" in result.output


def _request_merge_approval(workspace) -> None:
    layout = get_project_layout(workspace)
    with connect_database(layout.database_path) as connection:
        request_approval(
            connection,
            action="main.merge",
            reason="Merge reviewed changes.",
            requester_type="agent",
            requester_id="george",
            approver_id="founder",
        )


def _create_submitted_cli_run(runner: CliRunner, workspace) -> None:
    _create_cli_run(runner, workspace)
    assert (
        runner.invoke(app, ["run", "work", "RUN-0001", "--workspace", str(workspace)]).exit_code
        == 0
    )
    assert (
        runner.invoke(
            app,
            [
                "run",
                "submit",
                "RUN-0001",
                "Implemented the first pass.",
                "--workspace",
                str(workspace),
            ],
        ).exit_code
        == 0
    )


def _create_cli_run(runner: CliRunner, workspace) -> None:
    assert runner.invoke(app, ["init", "--workspace", str(workspace)]).exit_code == 0
    assert (
        runner.invoke(
            app,
            [
                "task",
                "create",
                "First task",
                "--brief",
                "Build the first task.",
                "--workspace",
                str(workspace),
            ],
        )
        .exit_code
        == 0
    )
    assert (
        runner.invoke(
            app,
            [
                "run",
                "create",
                "TASK-0001",
                "--agent-id",
                "george",
                "--workspace",
                str(workspace),
            ],
        ).exit_code
        == 0
    )


def _replace_engineer_capability_policy(workspace, capability: str, target: str) -> None:
    _replace_role_capability_policy(workspace, "engineer", capability, target)


def _replace_role_capability_policy(workspace, role: str, capability: str, target: str) -> None:
    path = workspace / ".team" / "policy" / "capabilities.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    role_policy = data["roles"][role]
    for field in ("allow", "require_approval", "deny"):
        role_policy[field] = [item for item in role_policy.get(field, []) if item != capability]
    role_policy.setdefault(target, []).append(capability)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _run_git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)
