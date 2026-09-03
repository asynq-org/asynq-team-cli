from asynq_team_core.approvals import request_approval
from asynq_team_core.database import connect_database
from asynq_team_core.paths import get_project_layout
from typer.testing import CliRunner

from asynq_team_cli.main import app


def test_cli_prints_version() -> None:
    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip() == "0.1.15"


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


def test_status_reports_initialized_workspace(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0

    result = runner.invoke(app, ["status", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert "Team dir: ok" in result.output
    assert "Config: ok" in result.output
    assert "Database: ok" in result.output
    assert "Project: Asynq Team" in result.output


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
    assert "Artifacts: .team/runs/george/RUN-0001" in result.output
    assert "Work packet: .team/runs/george/RUN-0001/work.md" in result.output
    work_packet = tmp_path / ".team" / "runs" / "george" / "RUN-0001" / "work.md"
    assert work_packet.is_file()
    assert "First task" in work_packet.read_text(encoding="utf-8")


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

    assert result.exit_code == 0
    assert "RUN-0001  approved" in result.output
    assert "Review: .team/runs/george/RUN-0001/review.md" in result.output
    assert "Agent notice: CMT-0002 -> george" in result.output
    review_artifact = tmp_path / ".team" / "runs" / "george" / "RUN-0001" / "review.md"
    assert review_artifact.is_file()
    assert "Looks ready." in review_artifact.read_text(encoding="utf-8")
    assert "Mention on TASK-0001" in inbox_result.output


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
