"""Typer entrypoint for the Asynq Team CLI."""

from pathlib import Path
from typing import Annotated

import typer
import yaml
from asynq_team_core.approvals import (
    ApprovalStatus,
    deny_approval,
    grant_approval,
    list_approvals,
)
from asynq_team_core.comments import create_task_comment, list_task_comments
from asynq_team_core.config import load_config
from asynq_team_core.database import connect_database, initialize_database
from asynq_team_core.inbox import InboxItemStatus, list_inbox_items
from asynq_team_core.paths import get_project_layout
from asynq_team_core.project import initialize_project
from asynq_team_core.run_service import create_run_with_artifact_dir
from asynq_team_core.runs import RunStatus, get_run, list_runs, update_run_status
from asynq_team_core.task_service import create_task_with_brief
from asynq_team_core.tasks import get_task, list_tasks

from asynq_team_cli import __version__

app = typer.Typer(no_args_is_help=True)
task_app = typer.Typer(no_args_is_help=True)
config_app = typer.Typer(no_args_is_help=True)
approvals_app = typer.Typer(no_args_is_help=False, invoke_without_command=True)
run_app = typer.Typer(no_args_is_help=True)
app.add_typer(task_app, name="task")
app.add_typer(config_app, name="config")
app.add_typer(approvals_app, name="approvals")
app.add_typer(run_app, name="run")


def print_version(value: bool) -> None:
    """Print the CLI version and exit when requested."""
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option("--version", callback=print_version, is_eager=True, help="Show version."),
    ] = False,
) -> None:
    """Run an AI-first company from your terminal."""


@app.command("init")
def init_command(
    workspace: Annotated[
        Path | None,
        typer.Option(
            "--workspace",
            "-w",
            help="Workspace directory to initialize.",
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = None,
    project_name: Annotated[
        str,
        typer.Option("--project-name", help="Project name written to .team/config.yaml."),
    ] = "Asynq Team",
    overwrite_config: Annotated[
        bool,
        typer.Option("--overwrite-config", help="Replace an existing .team/config.yaml."),
    ] = False,
    overwrite_defaults: Annotated[
        bool,
        typer.Option(
            "--overwrite-defaults",
            help="Replace existing default agent, rule, and policy files.",
        ),
    ] = False,
) -> None:
    """Initialize local Asynq Team runtime state."""
    target_workspace = workspace or Path.cwd()
    initialization = initialize_project(
        target_workspace,
        project_name=project_name,
        overwrite_config=overwrite_config,
        overwrite_defaults=overwrite_defaults,
    )
    initialize_database(initialization.layout.database_path)

    typer.echo(f"Initialized Asynq Team in {initialization.layout.team_dir}")
    if initialization.created_config:
        typer.echo(f"Created config: {initialization.layout.config_path}")
    else:
        typer.echo(f"Kept existing config: {initialization.layout.config_path}")
    if initialization.created_default_files:
        typer.echo(f"Created default files: {len(initialization.created_default_files)}")
    else:
        typer.echo("Kept existing default files")
    typer.echo(f"Database ready: {initialization.layout.database_path}")


@app.command("status")
def status_command(
    workspace: Annotated[
        Path | None,
        typer.Option(
            "--workspace",
            "-w",
            help="Workspace directory.",
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = None,
) -> None:
    """Show local runtime status."""
    layout = get_project_layout(workspace or Path.cwd())

    typer.echo(f"Workspace: {layout.workspace}")
    typer.echo(f"Team dir: {_format_state(layout.team_dir.is_dir())} {layout.team_dir}")
    typer.echo(f"Config: {_format_state(layout.config_path.is_file())} {layout.config_path}")
    typer.echo(f"Database: {_format_state(layout.database_path.is_file())} {layout.database_path}")

    if not layout.config_path.is_file():
        typer.echo("Project: not initialized")
        return

    config = load_config(layout.config_path)
    typer.echo(f"Project: {config.project.name}")
    typer.echo(f"Storage adapter: {config.storage.adapter}")


@config_app.command("show")
def config_show_command(
    workspace: Annotated[
        Path | None,
        typer.Option(
            "--workspace",
            "-w",
            help="Workspace directory.",
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = None,
) -> None:
    """Show project-local runtime config."""
    layout = get_project_layout(workspace or Path.cwd())
    if not layout.config_path.is_file():
        typer.echo(f"Config not found: {layout.config_path}", err=True)
        raise typer.Exit(1)

    config = load_config(layout.config_path)
    typer.echo(yaml.safe_dump(config.to_mapping(), sort_keys=False).rstrip())


@app.command("inbox")
def inbox_command(
    workspace: Annotated[
        Path | None,
        typer.Option(
            "--workspace",
            "-w",
            help="Workspace directory.",
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = None,
    recipient_id: Annotated[
        str | None,
        typer.Option("--recipient-id", help="Inbox recipient id. Defaults to all recipients."),
    ] = None,
    status: Annotated[str, typer.Option("--status", help="open, done, or all.")] = "open",
    limit: Annotated[int, typer.Option("--limit", min=1, help="Maximum items to show.")] = 50,
) -> None:
    """List inbox items that need attention."""
    layout = get_project_layout(workspace or Path.cwd())
    parsed_status = _parse_inbox_status(status)
    with connect_database(layout.database_path) as connection:
        items = list_inbox_items(
            connection,
            recipient_id=recipient_id,
            status=parsed_status,
            limit=limit,
        )

    if not items:
        typer.echo("No inbox items.")
        return

    for item in items:
        typer.echo(
            f"{item.id}  {item.status.value}  {item.recipient_id}  "
            f"{item.item_type.value}  {item.title}"
        )


@approvals_app.callback(invoke_without_command=True)
def approvals_command(
    context: typer.Context,
    workspace: Annotated[
        Path | None,
        typer.Option(
            "--workspace",
            "-w",
            help="Workspace directory.",
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = None,
    approver_id: Annotated[
        str | None,
        typer.Option("--approver-id", help="Approval owner id. Defaults to all approvers."),
    ] = None,
    status: Annotated[str, typer.Option("--status", help="pending, granted, denied, or all.")] = (
        "pending"
    ),
    limit: Annotated[int, typer.Option("--limit", min=1, help="Maximum approvals to show.")] = 50,
) -> None:
    """List approvals or decide a pending approval."""
    if context.invoked_subcommand is not None:
        return

    layout = get_project_layout(workspace or Path.cwd())
    parsed_status = _parse_approval_status(status)
    with connect_database(layout.database_path) as connection:
        approvals = list_approvals(
            connection,
            status=parsed_status,
            approver_id=approver_id,
            limit=limit,
        )

    if not approvals:
        typer.echo("No approvals.")
        return

    for approval in approvals:
        typer.echo(
            f"{approval.id}  {approval.status.value}  {approval.approver_id}  "
            f"{approval.action}  {approval.reason}"
        )


@approvals_app.command("approve")
def approval_approve_command(
    approval_id: Annotated[str, typer.Argument(help="Approval id, such as APR-0001.")],
    workspace: Annotated[
        Path | None,
        typer.Option(
            "--workspace",
            "-w",
            help="Workspace directory.",
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = None,
    actor_id: Annotated[str, typer.Option("--actor-id", help="Human actor id.")] = "founder",
    reason: Annotated[str | None, typer.Option("--reason", help="Decision reason.")] = None,
) -> None:
    """Approve a pending approval."""
    layout = get_project_layout(workspace or Path.cwd())
    with connect_database(layout.database_path) as connection:
        decision = grant_approval(
            connection,
            approval_id,
            actor_type="human",
            actor_id=actor_id,
            reason=reason,
        )

    typer.echo(f"Approved {decision.approval.id}")


@approvals_app.command("deny")
def approval_deny_command(
    approval_id: Annotated[str, typer.Argument(help="Approval id, such as APR-0001.")],
    workspace: Annotated[
        Path | None,
        typer.Option(
            "--workspace",
            "-w",
            help="Workspace directory.",
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = None,
    actor_id: Annotated[str, typer.Option("--actor-id", help="Human actor id.")] = "founder",
    reason: Annotated[str | None, typer.Option("--reason", help="Decision reason.")] = None,
) -> None:
    """Deny a pending approval."""
    layout = get_project_layout(workspace or Path.cwd())
    with connect_database(layout.database_path) as connection:
        decision = deny_approval(
            connection,
            approval_id,
            actor_type="human",
            actor_id=actor_id,
            reason=reason,
        )

    typer.echo(f"Denied {decision.approval.id}")


@task_app.command("create")
def task_create_command(
    title: Annotated[str, typer.Argument(help="Task title.")],
    brief: Annotated[
        str | None,
        typer.Option("--brief", help="Task brief Markdown. Defaults to the title."),
    ] = None,
    workspace: Annotated[
        Path | None,
        typer.Option(
            "--workspace",
            "-w",
            help="Workspace directory.",
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = None,
    priority: Annotated[str, typer.Option("--priority", help="Task priority.")] = "normal",
    actor_id: Annotated[str, typer.Option("--actor-id", help="Human actor id.")] = "founder",
) -> None:
    """Create a task and its brief artifact."""
    layout = get_project_layout(workspace or Path.cwd())
    created = create_task_with_brief(
        database_path=layout.database_path,
        layout=layout,
        title=title,
        brief_md=brief or title,
        actor_type="human",
        actor_id=actor_id,
        priority=priority,
    )

    typer.echo(f"{created.task.id} {created.task.title}")
    typer.echo(f"Brief: {created.brief.relative_path}")


@task_app.command("list")
def task_list_command(
    workspace: Annotated[
        Path | None,
        typer.Option(
            "--workspace",
            "-w",
            help="Workspace directory.",
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", min=1, help="Maximum tasks to show.")] = 50,
) -> None:
    """List tasks."""
    layout = get_project_layout(workspace or Path.cwd())
    with connect_database(layout.database_path) as connection:
        tasks = list_tasks(connection, limit=limit)

    if not tasks:
        typer.echo("No tasks.")
        return

    for task in tasks:
        typer.echo(f"{task.id}  {task.status.value}  {task.priority}  {task.title}")


@task_app.command("show")
def task_show_command(
    task_id: Annotated[str, typer.Argument(help="Task id, such as TASK-0001.")],
    workspace: Annotated[
        Path | None,
        typer.Option(
            "--workspace",
            "-w",
            help="Workspace directory.",
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = None,
) -> None:
    """Show a task."""
    layout = get_project_layout(workspace or Path.cwd())
    with connect_database(layout.database_path) as connection:
        task = get_task(connection, task_id)

    if task is None:
        typer.echo(f"Task not found: {task_id}", err=True)
        raise typer.Exit(1)

    typer.echo(f"ID: {task.id}")
    typer.echo(f"Title: {task.title}")
    typer.echo(f"Status: {task.status.value}")
    typer.echo(f"Priority: {task.priority}")
    if task.assignee_id:
        typer.echo(f"Assignee: {task.assignee_id}")
    if task.brief_artifact_path:
        typer.echo(f"Brief: {task.brief_artifact_path}")


@task_app.command("comment")
def task_comment_command(
    task_id: Annotated[str, typer.Argument(help="Task id, such as TASK-0001.")],
    body: Annotated[str, typer.Argument(help="Comment body.")],
    workspace: Annotated[
        Path | None,
        typer.Option(
            "--workspace",
            "-w",
            help="Workspace directory.",
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = None,
    mention: Annotated[
        list[str] | None,
        typer.Option("--mention", help="Recipient id to mention. Can be used multiple times."),
    ] = None,
    actor_id: Annotated[str, typer.Option("--actor-id", help="Human actor id.")] = "founder",
) -> None:
    """Add a comment to a task."""
    layout = get_project_layout(workspace or Path.cwd())
    with connect_database(layout.database_path) as connection:
        created = create_task_comment(
            connection,
            task_id=task_id,
            body=body,
            author_type="human",
            author_id=actor_id,
            mentions=tuple(mention or ()),
        )

    typer.echo(f"{created.comment.id} {created.comment.task_id}")
    if created.mentions:
        typer.echo(f"Mentions: {len(created.mentions)}")


@task_app.command("comments")
def task_comments_command(
    task_id: Annotated[str, typer.Argument(help="Task id, such as TASK-0001.")],
    workspace: Annotated[
        Path | None,
        typer.Option(
            "--workspace",
            "-w",
            help="Workspace directory.",
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", min=1, help="Maximum comments to show.")] = 50,
) -> None:
    """List comments for a task."""
    layout = get_project_layout(workspace or Path.cwd())
    with connect_database(layout.database_path) as connection:
        comments = list_task_comments(connection, task_id=task_id, limit=limit)

    if not comments:
        typer.echo("No comments.")
        return

    for comment in comments:
        typer.echo(f"{comment.id}  {comment.author_id}  {comment.body}")


@run_app.command("create")
def run_create_command(
    task_id: Annotated[str, typer.Argument(help="Task id, such as TASK-0001.")],
    agent_id: Annotated[
        str,
        typer.Option("--agent-id", "--agent", help="Agent id for the run."),
    ],
    workspace: Annotated[
        Path | None,
        typer.Option(
            "--workspace",
            "-w",
            help="Workspace directory.",
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = None,
    actor_type: Annotated[str, typer.Option("--actor-type", help="Audit actor type.")] = "human",
    actor_id: Annotated[str, typer.Option("--actor-id", help="Audit actor id.")] = "founder",
) -> None:
    """Create an agent run record and artifact directory."""
    layout = get_project_layout(workspace or Path.cwd())
    try:
        created = create_run_with_artifact_dir(
            database_path=layout.database_path,
            layout=layout,
            task_id=task_id,
            agent_id=agent_id,
            actor_type=actor_type,
            actor_id=actor_id,
        )
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    typer.echo(
        f"{created.run.id}  {created.run.task_id}  {created.run.agent_id}  "
        f"{created.run.status.value}"
    )
    typer.echo(f"Artifacts: {created.run.artifact_dir_path}")


@run_app.command("list")
def run_list_command(
    workspace: Annotated[
        Path | None,
        typer.Option(
            "--workspace",
            "-w",
            help="Workspace directory.",
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = None,
    task_id: Annotated[
        str | None,
        typer.Option("--task-id", help="Filter by task id."),
    ] = None,
    agent_id: Annotated[
        str | None,
        typer.Option("--agent-id", "--agent", help="Filter by agent id."),
    ] = None,
    status: Annotated[str, typer.Option("--status", help="Run status or all.")] = "all",
    limit: Annotated[int, typer.Option("--limit", min=1, help="Maximum runs to show.")] = 50,
) -> None:
    """List run records."""
    layout = get_project_layout(workspace or Path.cwd())
    parsed_status = _parse_run_status(status)
    with connect_database(layout.database_path) as connection:
        runs = list_runs(
            connection,
            task_id=task_id,
            agent_id=agent_id,
            status=parsed_status,
            limit=limit,
        )

    if not runs:
        typer.echo("No runs.")
        return

    for run in runs:
        typer.echo(f"{run.id}  {run.status.value}  {run.task_id}  {run.agent_id}")


@run_app.command("show")
def run_show_command(
    run_id: Annotated[str, typer.Argument(help="Run id, such as RUN-0001.")],
    workspace: Annotated[
        Path | None,
        typer.Option(
            "--workspace",
            "-w",
            help="Workspace directory.",
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = None,
) -> None:
    """Show a run record."""
    layout = get_project_layout(workspace or Path.cwd())
    with connect_database(layout.database_path) as connection:
        run = get_run(connection, run_id)

    if run is None:
        typer.echo(f"Run not found: {run_id}", err=True)
        raise typer.Exit(1)

    typer.echo(f"ID: {run.id}")
    typer.echo(f"Task: {run.task_id}")
    typer.echo(f"Agent: {run.agent_id}")
    typer.echo(f"Status: {run.status.value}")
    if run.artifact_dir_path:
        typer.echo(f"Artifacts: {run.artifact_dir_path}")


@run_app.command("status")
def run_status_command(
    run_id: Annotated[str, typer.Argument(help="Run id, such as RUN-0001.")],
    status: Annotated[str, typer.Argument(help="New run status.")],
    workspace: Annotated[
        Path | None,
        typer.Option(
            "--workspace",
            "-w",
            help="Workspace directory.",
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = None,
    actor_type: Annotated[str, typer.Option("--actor-type", help="Audit actor type.")] = "human",
    actor_id: Annotated[str, typer.Option("--actor-id", help="Audit actor id.")] = "founder",
) -> None:
    """Update a run status."""
    layout = get_project_layout(workspace or Path.cwd())
    parsed_status = _parse_run_status(status)
    if parsed_status is None:
        raise typer.BadParameter("status must be a concrete run status")

    with connect_database(layout.database_path) as connection:
        try:
            run = update_run_status(
                connection,
                run_id=run_id,
                status=parsed_status,
                actor_type=actor_type,
                actor_id=actor_id,
            )
        except ValueError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc

    typer.echo(f"{run.id}  {run.status.value}")


def _format_state(value: bool) -> str:
    return "ok" if value else "missing"


def _parse_approval_status(value: str) -> ApprovalStatus | None:
    if value == "all":
        return None
    try:
        return ApprovalStatus(value)
    except ValueError as exc:
        raise typer.BadParameter("status must be pending, granted, denied, or all") from exc


def _parse_inbox_status(value: str) -> InboxItemStatus | None:
    if value == "all":
        return None
    try:
        return InboxItemStatus(value)
    except ValueError as exc:
        raise typer.BadParameter("status must be open, done, or all") from exc


def _parse_run_status(value: str) -> RunStatus | None:
    if value == "all":
        return None
    try:
        return RunStatus(value)
    except ValueError as exc:
        allowed = ", ".join(status.value for status in RunStatus)
        raise typer.BadParameter(f"status must be one of: {allowed}, or all") from exc
