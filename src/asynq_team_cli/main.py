"""Typer entrypoint for the Asynq Team CLI."""

import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Annotated

import typer
import yaml
from asynq_team_core.agent_manifests import list_agent_manifests, load_agent_manifest
from asynq_team_core.approvals import (
    ApprovalStatus,
    deny_approval,
    get_approval,
    grant_approval,
    list_approvals,
)
from asynq_team_core.audit import list_task_audit_events
from asynq_team_core.backups import create_database_backup, list_database_backups
from asynq_team_core.comments import create_authorized_task_comment, list_task_comments
from asynq_team_core.config import load_config, write_config
from asynq_team_core.database import connect_database, initialize_database
from asynq_team_core.doctor import run_doctor
from asynq_team_core.inbox import InboxItemStatus, list_inbox_items
from asynq_team_core.paths import get_project_layout
from asynq_team_core.policy import authorize_agent_capability, evaluate_agent_capability
from asynq_team_core.project import initialize_project
from asynq_team_core.run_commands import record_run_command
from asynq_team_core.run_files import RunFileChangeType, record_run_file_change
from asynq_team_core.run_review import RunReviewDecision, review_authorized_run
from asynq_team_core.run_service import create_run_with_artifact_dir
from asynq_team_core.run_submission import submit_authorized_run_for_review
from asynq_team_core.run_task import start_authorized_task_run
from asynq_team_core.run_work import prepare_authorized_run_work_packet
from asynq_team_core.runner_execution import execute_run_command
from asynq_team_core.runner_policy import evaluate_runner_tool
from asynq_team_core.runs import (
    RunStatus,
    get_next_agent_run,
    get_run,
    list_runs,
    update_run_status,
)
from asynq_team_core.task_service import (
    create_authorized_follow_up_task,
    create_authorized_task_with_brief,
)
from asynq_team_core.tasks import (
    TaskStatus,
    get_task,
    list_follow_up_tasks,
    list_tasks,
    update_task_status,
)
from asynq_team_core.worker import WorkerRunOnceResult, run_worker_once

from asynq_team_cli import __version__
from asynq_team_cli.workspace_context import (
    clear_workspace_context,
    load_workspace_context,
    save_workspace_context,
)

app = typer.Typer(no_args_is_help=True)
workspace_app = typer.Typer(no_args_is_help=True)
task_app = typer.Typer(no_args_is_help=True)
agent_app = typer.Typer(no_args_is_help=True)
config_app = typer.Typer(no_args_is_help=True)
approvals_app = typer.Typer(no_args_is_help=False, invoke_without_command=True)
approval_app = typer.Typer(no_args_is_help=True)
run_app = typer.Typer(no_args_is_help=True)
backup_app = typer.Typer(no_args_is_help=True)
audit_app = typer.Typer(no_args_is_help=True)
policy_app = typer.Typer(no_args_is_help=True)
runner_app = typer.Typer(no_args_is_help=True)
worker_app = typer.Typer(no_args_is_help=True)
app.add_typer(workspace_app, name="workspace")
app.add_typer(task_app, name="task")
app.add_typer(agent_app, name="agent")
app.add_typer(config_app, name="config")
app.add_typer(approvals_app, name="approvals")
app.add_typer(approval_app, name="approval")
app.add_typer(run_app, name="run")
app.add_typer(backup_app, name="backup")
app.add_typer(audit_app, name="audit")
app.add_typer(policy_app, name="policy")
app.add_typer(runner_app, name="runner")
app.add_typer(worker_app, name="worker")


@dataclass(frozen=True)
class GitFileChange:
    """Parsed git name-status file change."""

    change_type: RunFileChangeType
    path: str
    previous_path: str | None = None


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


@workspace_app.command("use")
def workspace_use_command(
    workspace: Annotated[
        Path,
        typer.Argument(
            help="Workspace directory to use by default.",
            exists=True,
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ],
) -> None:
    """Set the default workspace for future CLI commands."""
    try:
        context = save_workspace_context(workspace)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"Workspace context: {context.workspace}")


@workspace_app.command("current")
def workspace_current_command() -> None:
    """Show the current default workspace."""
    try:
        context = load_workspace_context()
    except (TypeError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if context is None:
        typer.echo("No workspace context set.")
        raise typer.Exit(1)

    typer.echo(str(context.workspace))


@workspace_app.command("clear")
def workspace_clear_command() -> None:
    """Clear the default workspace."""
    removed = clear_workspace_context()
    if removed:
        typer.echo("Workspace context cleared.")
        return
    typer.echo("No workspace context set.")


def _resolve_workspace(workspace: Path | None) -> Path:
    if workspace is not None:
        return workspace

    try:
        context = load_workspace_context()
    except (TypeError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    if context is not None:
        return context.workspace

    return Path.cwd()


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
    git_backup: Annotated[
        bool,
        typer.Option("--git-backup/--no-git-backup", help="Enable git artifact backup config."),
    ] = True,
    git_remote: Annotated[
        str,
        typer.Option("--git-remote", help="Git remote used for artifact backup."),
    ] = "",
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
    target_workspace = _resolve_workspace(workspace)
    initialization = initialize_project(
        target_workspace,
        project_name=project_name,
        git_enabled=git_backup,
        git_remote=git_remote,
        overwrite_config=overwrite_config,
        overwrite_defaults=overwrite_defaults,
    )
    initialize_database(initialization.layout.database_path)

    typer.echo(f"Initialized Asynq Team in {initialization.layout.team_dir}")
    if initialization.created_config:
        typer.echo(f"Created config: {initialization.layout.config_path}")
        typer.echo(f"Git backup: {'enabled' if git_backup else 'disabled'}")
        if git_remote:
            typer.echo(f"Git remote: {git_remote.strip()}")
    else:
        typer.echo(f"Kept existing config: {initialization.layout.config_path}")
    if initialization.created_default_files:
        typer.echo(f"Created default files: {len(initialization.created_default_files)}")
    else:
        typer.echo("Kept existing default files")
    typer.echo(f"Database ready: {initialization.layout.database_path}")


@app.command("onboard")
def onboard_command(
    workspace: Annotated[
        Path | None,
        typer.Option(
            "--workspace",
            "-w",
            help="Workspace directory to onboard.",
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = None,
    project_name: Annotated[
        str | None,
        typer.Option("--project-name", help="Project name written to .team/config.yaml."),
    ] = None,
    git_remote: Annotated[
        str | None,
        typer.Option("--git-remote", help="Git remote used for artifact backup."),
    ] = None,
    default_model: Annotated[
        str | None,
        typer.Option("--default-model", "--model", help="Default model for generated agents."),
    ] = None,
    engineer_name: Annotated[
        str | None,
        typer.Option("--engineer-name", help="Display name for the default engineer agent."),
    ] = None,
    supervisor_name: Annotated[
        str | None,
        typer.Option("--supervisor-name", help="Display name for the supervisor agent."),
    ] = None,
    ea_name: Annotated[
        str | None,
        typer.Option("--ea-name", help="Display name for the EA agent."),
    ] = None,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Accept defaults for omitted onboarding prompts."),
    ] = False,
) -> None:
    """Initialize and customize a local Asynq Team workspace."""
    target_workspace = _resolve_workspace(workspace)
    initialization = initialize_project(target_workspace)
    initialize_database(initialization.layout.database_path)

    config = load_config(initialization.layout.config_path)
    resolved_project_name = _onboarding_value(
        project_name,
        "Project name",
        config.project.name,
        yes=yes,
    )
    resolved_git_remote = _onboarding_value(
        git_remote,
        "Git backup remote",
        config.git.remote,
        yes=yes,
        allow_empty=True,
    )
    resolved_model = _onboarding_value(
        default_model,
        "Default agent model",
        "gpt-5-codex",
        yes=yes,
    )
    resolved_names = {
        "george": _onboarding_value(engineer_name, "Engineer display name", "George", yes=yes),
        "supervisor": _onboarding_value(
            supervisor_name,
            "Supervisor display name",
            "Supervisor",
            yes=yes,
        ),
        "ea": _onboarding_value(ea_name, "EA display name", "EA", yes=yes),
    }

    updated_config = replace(
        config,
        project=replace(config.project, name=resolved_project_name),
        git=replace(config.git, remote=resolved_git_remote),
    )
    write_config(initialization.layout.config_path, updated_config)
    _update_agent_onboarding_files(initialization.layout.agents_dir, resolved_names, resolved_model)
    _update_runner_policy_default_model(initialization.layout.policy_dir, resolved_model)

    typer.echo(f"Onboarded Asynq Team in {initialization.layout.team_dir}")
    typer.echo(f"Project: {resolved_project_name}")
    typer.echo(f"Default model: {resolved_model}")
    typer.echo(f"Agents: {', '.join(f'{agent}={name}' for agent, name in resolved_names.items())}")
    typer.echo(f"Review policy files under: {initialization.layout.policy_dir}")


def _onboarding_value(
    value: str | None,
    prompt: str,
    default: str,
    yes: bool,
    allow_empty: bool = False,
) -> str:
    if value is not None:
        return _clean_single_line(value, prompt, allow_empty=allow_empty)
    if yes:
        return default
    return _clean_single_line(
        typer.prompt(prompt, default=default),
        prompt,
        allow_empty=allow_empty,
    )


def _update_agent_onboarding_files(
    agents_dir: Path,
    display_names: dict[str, str],
    default_model: str,
) -> None:
    for agent_id, display_name in display_names.items():
        path = agents_dir / f"{agent_id}.yaml"
        data = _load_yaml_file(path, f"Agent manifest {agent_id}")
        data["display_name"] = display_name
        runner = _get_yaml_mapping(data, "runner", f"Agent manifest {agent_id}")
        runner["default_model"] = default_model
        allowed_models = _get_yaml_list(runner, "allowed_models", f"Agent manifest {agent_id}")
        if default_model not in allowed_models:
            allowed_models.append(default_model)
        _write_yaml_file(path, data)


def _update_runner_policy_default_model(policy_dir: Path, default_model: str) -> None:
    path = policy_dir / "runners.yaml"
    data = _load_yaml_file(path, "Runner policy")
    runners = _get_yaml_mapping(data, "runners", "Runner policy")
    codex = _get_yaml_mapping(runners, "codex", "Runner policy")
    allowed_models = _get_yaml_list(codex, "allowed_models", "Runner policy")
    if default_model not in allowed_models:
        allowed_models.append(default_model)
    _write_yaml_file(path, data)


def _load_yaml_file(path: Path, document_name: str) -> dict:
    if not path.is_file():
        raise ValueError(f"{document_name} not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise TypeError(f"{document_name} root must be a mapping.")
    return data


def _write_yaml_file(path: Path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _get_yaml_mapping(data: dict, key: str, document_name: str) -> dict:
    value = data.get(key)
    if not isinstance(value, dict):
        raise TypeError(f"{document_name} field must be a mapping: {key}")
    return value


def _get_yaml_list(data: dict, key: str, document_name: str) -> list:
    value = data.setdefault(key, [])
    if not isinstance(value, list):
        raise TypeError(f"{document_name} field must be a list: {key}")
    return value


def _clean_single_line(value: str, field_name: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string.")
    clean_value = value.strip()
    if not clean_value and not allow_empty:
        raise ValueError(f"{field_name} must be a non-empty string.")
    if "\n" in clean_value or "\r" in clean_value:
        raise ValueError(f"{field_name} must be a single line.")
    return clean_value


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
    layout = get_project_layout(_resolve_workspace(workspace))

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


@agent_app.command("show")
def agent_show_command(
    agent_id: Annotated[str, typer.Argument(help="Agent id, such as george.")],
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
    """Show an agent manifest summary."""
    layout = get_project_layout(_resolve_workspace(workspace))
    try:
        manifest = load_agent_manifest(layout, agent_id)
    except (TypeError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"ID: {manifest.id}")
    typer.echo(f"Display name: {manifest.display_name}")
    typer.echo(f"Role: {manifest.role}")
    if manifest.supervisor:
        typer.echo(f"Supervisor: {manifest.supervisor}")
    typer.echo(f"Runner: {manifest.runner.default}")
    typer.echo(f"Default model: {manifest.runner.default_model}")
    typer.echo(f"Allowed models: {', '.join(sorted(manifest.runner.allowed_models))}")
    typer.echo(f"Can request model change: {manifest.runner.can_request_model_change}")
    if manifest.runner.max_run_budget_usd is not None:
        typer.echo(f"Max run budget USD: {manifest.runner.max_run_budget_usd:g}")


@app.command("doctor")
def doctor_command(
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
    """Run local workspace diagnostics."""
    layout = get_project_layout(_resolve_workspace(workspace))
    report = run_doctor(layout)

    for check in report.checks:
        typer.echo(f"{check.status.value}  {check.name}  {check.message}")

    if report.has_failures:
        raise typer.Exit(1)


@app.command("review")
def review_command(
    run_id: Annotated[str, typer.Argument(help="Run id, such as RUN-0001.")],
    decision: Annotated[str, typer.Argument(help="approve or return.")],
    body: Annotated[str, typer.Argument(help="Review body Markdown.")],
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
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="Replace an existing review artifact."),
    ] = False,
    actor_type: Annotated[str, typer.Option("--actor-type", help="Audit actor type.")] = "agent",
    actor_id: Annotated[str, typer.Option("--actor-id", help="Audit actor id.")] = "supervisor",
    approver_id: Annotated[str, typer.Option("--approver-id", help="Approver id.")] = "founder",
) -> None:
    """Review a submitted run."""
    layout = get_project_layout(_resolve_workspace(workspace))
    try:
        result = review_authorized_run(
            database_path=layout.database_path,
            layout=layout,
            run_id=run_id,
            decision=_parse_review_decision(decision),
            body_md=body,
            actor_type=actor_type,
            actor_id=actor_id,
            overwrite=overwrite,
            approver_id=approver_id,
        )
    except (PermissionError, TypeError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if _echo_pending_capability_approval(result.authorization):
        return

    review = result.review
    if review is None:
        raise RuntimeError("Authorized run review completed without a review.")
    typer.echo(f"{review.run.id}  {review.run.status.value}")
    typer.echo(f"Review: {review.artifact.relative_path}")
    typer.echo(f"Agent notice: {review.comment.comment.id} -> {review.run.agent_id}")


@app.command("approve")
def approve_command(
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
    _decide_approval_command(
        approval_id=approval_id,
        workspace=workspace,
        actor_id=actor_id,
        reason=reason,
        approve=True,
    )


@app.command("deny")
def deny_command(
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
    _decide_approval_command(
        approval_id=approval_id,
        workspace=workspace,
        actor_id=actor_id,
        reason=reason,
        approve=False,
    )


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
    layout = get_project_layout(_resolve_workspace(workspace))
    if not layout.config_path.is_file():
        typer.echo(f"Config not found: {layout.config_path}", err=True)
        raise typer.Exit(1)

    config = load_config(layout.config_path)
    typer.echo(yaml.safe_dump(config.to_mapping(), sort_keys=False).rstrip())


@approval_app.command("show")
def approval_show_command(
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
) -> None:
    """Show an approval request."""
    layout = get_project_layout(_resolve_workspace(workspace))
    with connect_database(layout.database_path) as connection:
        approval = get_approval(connection, approval_id)

    if approval is None:
        typer.echo(f"Approval not found: {approval_id}", err=True)
        raise typer.Exit(1)

    typer.echo(f"ID: {approval.id}")
    typer.echo(f"Action: {approval.action}")
    typer.echo(f"Status: {approval.status.value}")
    typer.echo(f"Reason: {approval.reason}")
    typer.echo(f"Requester: {approval.requester_type}:{approval.requester_id}")
    typer.echo(f"Approver: {approval.approver_id}")
    if approval.subject_type and approval.subject_id:
        typer.echo(f"Subject: {approval.subject_type}:{approval.subject_id}")
    if approval.decision_reason:
        typer.echo(f"Decision reason: {approval.decision_reason}")


@backup_app.command("run")
def backup_run_command(
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
    """Create a local database backup."""
    layout = get_project_layout(_resolve_workspace(workspace))
    try:
        backup = create_database_backup(
            database_path=layout.database_path,
            layout=layout,
            actor_type=actor_type,
            actor_id=actor_id,
        )
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"{backup.relative_path}  {backup.created_at}  {backup.size_bytes} bytes")


@backup_app.command("list")
def backup_list_command(
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
    limit: Annotated[int, typer.Option("--limit", min=1, help="Maximum backups to show.")] = 50,
) -> None:
    """List local database backups."""
    layout = get_project_layout(_resolve_workspace(workspace))
    backups = list_database_backups(layout, limit=limit)

    if not backups:
        typer.echo("No backups.")
        return

    for backup in backups:
        typer.echo(f"{backup.relative_path}  {backup.created_at}  {backup.size_bytes} bytes")


@audit_app.command("show")
def audit_show_command(
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
    limit: Annotated[int, typer.Option("--limit", min=1, help="Maximum events to show.")] = 100,
) -> None:
    """Show task-scoped audit events."""
    layout = get_project_layout(_resolve_workspace(workspace))
    try:
        events = list_task_audit_events(layout.database_path, task_id, limit=limit)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if not events:
        typer.echo("No audit events.")
        return

    for event in events:
        line = (
            f"{event.created_at}  {event.event_type}  "
            f"{event.actor_type}:{event.actor_id}  {event.entity_type}:{event.entity_id}"
        )
        detail = _format_audit_event_detail(event)
        if detail:
            line = f"{line}  {detail}"
        typer.echo(line)


@policy_app.command("check")
def policy_check_command(
    agent_id: Annotated[str, typer.Argument(help="Agent id, such as george.")],
    capability: Annotated[str, typer.Argument(help="Capability name, such as main.merge.")],
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
    """Show the capability policy decision for an agent."""
    layout = get_project_layout(_resolve_workspace(workspace))
    try:
        evaluation = evaluate_agent_capability(layout, agent_id, capability)
    except (TypeError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    typer.echo(
        f"{evaluation.agent_id}  {evaluation.role}  {evaluation.capability}  "
        f"{evaluation.decision.value}"
    )
    typer.echo(f"Reason: {evaluation.reason}")


@policy_app.command("authorize")
def policy_authorize_command(
    agent_id: Annotated[str, typer.Argument(help="Agent id, such as george.")],
    capability: Annotated[str, typer.Argument(help="Capability name, such as main.merge.")],
    reason: Annotated[str, typer.Argument(help="Reason for the requested capability.")],
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
    approver_id: Annotated[str, typer.Option("--approver-id", help="Approver id.")] = "founder",
    subject_type: Annotated[
        str | None,
        typer.Option("--subject-type", help="Optional subject type, such as task or run."),
    ] = None,
    subject_id: Annotated[
        str | None,
        typer.Option("--subject-id", help="Optional subject id, such as TASK-0001."),
    ] = None,
) -> None:
    """Authorize a capability or request the required approval."""
    layout = get_project_layout(_resolve_workspace(workspace))
    try:
        authorization = authorize_agent_capability(
            database_path=layout.database_path,
            layout=layout,
            agent_id=agent_id,
            capability=capability,
            reason=reason,
            approver_id=approver_id,
            subject_type=subject_type,
            subject_id=subject_id,
        )
    except (PermissionError, TypeError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    evaluation = authorization.evaluation
    typer.echo(
        f"{evaluation.agent_id}  {evaluation.role}  {evaluation.capability}  "
        f"{evaluation.decision.value}"
    )
    if authorization.approval_request is None:
        typer.echo("Approval: not required")
        return

    approval = authorization.approval_request.approval
    inbox_item = authorization.approval_request.inbox_item
    typer.echo(f"Approval: {approval.id} -> {approval.approver_id}")
    typer.echo(f"Inbox: {inbox_item.id} -> {inbox_item.recipient_id}")


@runner_app.command("check")
def runner_check_command(
    tool: Annotated[str, typer.Argument(help="Runner tool id, such as shell.test.")],
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
    """Check whether a runner tool is allowed."""
    layout = get_project_layout(_resolve_workspace(workspace))
    try:
        evaluation = evaluate_runner_tool(layout, tool)
    except (TypeError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"{evaluation.tool}  {evaluation.decision.value}")
    typer.echo(f"Reason: {evaluation.reason}")


@runner_app.command("exec")
def runner_exec_command(
    run_id: Annotated[str, typer.Argument(help="Run id, such as RUN-0001.")],
    tool: Annotated[str, typer.Argument(help="Runner tool id, such as shell.test.")],
    command: Annotated[list[str], typer.Argument(help="Command argv to execute.")],
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
    cwd: Annotated[
        str | None,
        typer.Option("--cwd", help="Workspace-relative working directory."),
    ] = None,
    timeout_seconds: Annotated[
        int,
        typer.Option("--timeout-seconds", min=1, help="Command timeout in seconds."),
    ] = 300,
    actor_type: Annotated[str, typer.Option("--actor-type", help="Audit actor type.")] = "agent",
    actor_id: Annotated[str, typer.Option("--actor-id", help="Audit actor id.")] = "george",
) -> None:
    """Execute a local command through runner policy."""
    layout = get_project_layout(_resolve_workspace(workspace))
    try:
        result = execute_run_command(
            database_path=layout.database_path,
            layout=layout,
            run_id=run_id,
            tool=tool,
            command=tuple(command),
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            actor_type=actor_type,
            actor_id=actor_id,
        )
    except (PermissionError, TypeError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"{result.record.run.id}  {tool}  exit_code={result.exit_code}")
    typer.echo(f"Event: {result.record.event.id}")
    if result.stdout:
        typer.echo("Stdout:")
        typer.echo(result.stdout.rstrip())
    if result.stderr:
        typer.echo("Stderr:")
        typer.echo(result.stderr.rstrip(), err=True)
    if result.exit_code != 0:
        raise typer.Exit(result.exit_code)


@worker_app.command("run-once")
def worker_run_once_command(
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
    agent_id: Annotated[
        str | None,
        typer.Option("--agent-id", "--agent", help="Limit this pass to one agent id."),
    ] = None,
    requested_model: Annotated[
        str | None,
        typer.Option("--model", help="Requested model for new runs."),
    ] = None,
    actor_type: Annotated[str, typer.Option("--actor-type", help="Audit actor type.")] = "agent",
    actor_id: Annotated[
        str | None,
        typer.Option("--actor-id", help="Audit actor id. Defaults to the agent id."),
    ] = None,
    approver_id: Annotated[str, typer.Option("--approver-id", help="Approver id.")] = "founder",
) -> None:
    """Run one local worker scheduling pass."""
    layout = get_project_layout(_resolve_workspace(workspace))
    try:
        for selected_agent_id in _worker_agent_ids(layout, agent_id):
            result = run_worker_once(
                database_path=layout.database_path,
                layout=layout,
                agent_id=selected_agent_id,
                actor_type=actor_type,
                actor_id=actor_id,
                approver_id=approver_id,
                requested_model=requested_model,
            )
            _echo_worker_run_once_result(selected_agent_id, result)
    except (PermissionError, TypeError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc


@worker_app.command("start")
def worker_start_command(
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
    agent_id: Annotated[
        str | None,
        typer.Option("--agent-id", "--agent", help="Limit the worker loop to one agent id."),
    ] = None,
    requested_model: Annotated[
        str | None,
        typer.Option("--model", help="Requested model for new runs."),
    ] = None,
    poll_seconds: Annotated[
        float,
        typer.Option("--poll-seconds", min=0.1, help="Delay between worker passes."),
    ] = 5.0,
    iterations: Annotated[
        int | None,
        typer.Option("--iterations", min=1, help="Stop after this many passes."),
    ] = None,
    daemon: Annotated[
        bool,
        typer.Option("--daemon", help="Start the worker loop in the background."),
    ] = False,
    actor_type: Annotated[str, typer.Option("--actor-type", help="Audit actor type.")] = "agent",
    actor_id: Annotated[
        str | None,
        typer.Option("--actor-id", help="Audit actor id. Defaults to the agent id."),
    ] = None,
    approver_id: Annotated[str, typer.Option("--approver-id", help="Approver id.")] = "founder",
) -> None:
    """Start a foreground local worker polling loop."""
    layout = get_project_layout(_resolve_workspace(workspace))
    agent_ids = _worker_agent_ids(layout, agent_id)
    if daemon:
        _start_worker_daemon(
            workspace=layout.workspace,
            agent_id=agent_id,
            requested_model=requested_model,
            poll_seconds=poll_seconds,
            iterations=iterations,
            actor_type=actor_type,
            actor_id=actor_id,
            approver_id=approver_id,
        )
        return

    typer.echo(f"Worker started for agents: {', '.join(agent_ids)}.")
    completed_iterations = 0

    try:
        while iterations is None or completed_iterations < iterations:
            for selected_agent_id in agent_ids:
                result = run_worker_once(
                    database_path=layout.database_path,
                    layout=layout,
                    agent_id=selected_agent_id,
                    actor_type=actor_type,
                    actor_id=actor_id,
                    approver_id=approver_id,
                    requested_model=requested_model,
                )
                _echo_worker_run_once_result(selected_agent_id, result)
            completed_iterations += 1
            if iterations is not None and completed_iterations >= iterations:
                break
            time.sleep(poll_seconds)
    except KeyboardInterrupt:
        typer.echo("Worker stopped.")
    except (PermissionError, TypeError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc


@worker_app.command("status")
def worker_status_command(
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
    """Show local worker daemon status."""
    layout = get_project_layout(_resolve_workspace(workspace))
    pid = _read_worker_pid(layout)
    if pid is None:
        typer.echo("No worker daemon.")
        return
    if _pid_is_running(pid):
        typer.echo(f"Worker daemon running: {pid}")
        typer.echo(f"Log: {_worker_log_path(layout)}")
        return

    _clear_worker_pid(layout)
    typer.echo(f"Removed stale worker daemon pid: {pid}")


@worker_app.command("stop")
def worker_stop_command(
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
    """Stop the local worker daemon."""
    layout = get_project_layout(_resolve_workspace(workspace))
    _stop_worker_daemon(layout)


@worker_app.command("restart")
def worker_restart_command(
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
    agent_id: Annotated[
        str | None,
        typer.Option("--agent-id", "--agent", help="Limit the worker loop to one agent id."),
    ] = None,
    requested_model: Annotated[
        str | None,
        typer.Option("--model", help="Requested model for new runs."),
    ] = None,
    poll_seconds: Annotated[
        float,
        typer.Option("--poll-seconds", min=0.1, help="Delay between worker passes."),
    ] = 5.0,
    iterations: Annotated[
        int | None,
        typer.Option("--iterations", min=1, help="Stop after this many passes."),
    ] = None,
    actor_type: Annotated[str, typer.Option("--actor-type", help="Audit actor type.")] = "agent",
    actor_id: Annotated[
        str | None,
        typer.Option("--actor-id", help="Audit actor id. Defaults to the agent id."),
    ] = None,
    approver_id: Annotated[str, typer.Option("--approver-id", help="Approver id.")] = "founder",
) -> None:
    """Restart the local worker daemon."""
    layout = get_project_layout(_resolve_workspace(workspace))
    _stop_worker_daemon(layout, quiet=True)
    _start_worker_daemon(
        workspace=layout.workspace,
        agent_id=agent_id,
        requested_model=requested_model,
        poll_seconds=poll_seconds,
        iterations=iterations,
        actor_type=actor_type,
        actor_id=actor_id,
        approver_id=approver_id,
    )


def _worker_agent_ids(layout, agent_id: str | None) -> tuple[str, ...]:
    if agent_id is not None:
        return (agent_id,)

    agent_ids = tuple(manifest.id for manifest in list_agent_manifests(layout))
    return tuple(sorted(agent_ids, key=lambda value: (value != "ea", value)))


def _echo_worker_run_once_result(agent_id: str, result: WorkerRunOnceResult) -> None:
    if result.task is None:
        typer.echo(f"{agent_id}: no available tasks.")
        return

    if result.routed is not None:
        typer.echo(
            f"{agent_id}: routed {result.routed.task.id} -> {result.routed.assignee_id}"
        )
        typer.echo(f"Reason: {result.routed.reason}")
        return

    typer.echo(f"{agent_id}: task {result.task.id}")
    typer.echo(f"Task: {result.task.id}  {result.task.status.value}  {result.task.title}")
    if _echo_pending_capability_approval(result.authorization):
        return

    started = result.started
    if started is None:
        raise RuntimeError("Worker run-once completed without a run.")

    run = started.work_packet.run
    typer.echo(f"Run: {run.id}  {run.status.value}  {run.agent_id}")
    if run.runner_id and run.model:
        typer.echo(f"Runner: {run.runner_id}")
        typer.echo(f"Model: {run.model}")
    typer.echo(f"Artifacts: {run.artifact_dir_path}")
    typer.echo(f"Work packet: {started.work_packet.artifact.relative_path}")


def _start_worker_daemon(
    workspace: Path,
    agent_id: str | None,
    requested_model: str | None,
    poll_seconds: float,
    iterations: int | None,
    actor_type: str,
    actor_id: str | None,
    approver_id: str,
) -> None:
    layout = get_project_layout(workspace)
    existing_pid = _read_worker_pid(layout)
    if existing_pid is not None and _pid_is_running(existing_pid):
        typer.echo(f"Worker daemon already running: {existing_pid}")
        return
    if existing_pid is not None:
        _clear_worker_pid(layout)

    _worker_state_dir(layout).mkdir(parents=True, exist_ok=True)
    command = _worker_daemon_command(
        workspace=workspace,
        agent_id=agent_id,
        requested_model=requested_model,
        poll_seconds=poll_seconds,
        iterations=iterations,
        actor_type=actor_type,
        actor_id=actor_id,
        approver_id=approver_id,
    )
    with _worker_log_path(layout).open("ab") as log_file:
        process = subprocess.Popen(
            command,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    _write_worker_pid(layout, process.pid)
    typer.echo(f"Worker daemon started: {process.pid}")
    typer.echo(f"Log: {_worker_log_path(layout)}")


def _worker_daemon_command(
    workspace: Path,
    agent_id: str | None,
    requested_model: str | None,
    poll_seconds: float,
    iterations: int | None,
    actor_type: str,
    actor_id: str | None,
    approver_id: str,
) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "asynq_team_cli.main",
        "worker",
        "start",
        "--workspace",
        str(workspace),
        "--poll-seconds",
        str(poll_seconds),
        "--actor-type",
        actor_type,
        "--approver-id",
        approver_id,
    ]
    if agent_id is not None:
        command.extend(["--agent-id", agent_id])
    if requested_model is not None:
        command.extend(["--model", requested_model])
    if iterations is not None:
        command.extend(["--iterations", str(iterations)])
    if actor_id is not None:
        command.extend(["--actor-id", actor_id])
    return command


def _stop_worker_daemon(layout, quiet: bool = False) -> None:
    pid = _read_worker_pid(layout)
    if pid is None:
        if not quiet:
            typer.echo("No worker daemon.")
        return
    if not _pid_is_running(pid):
        _clear_worker_pid(layout)
        if not quiet:
            typer.echo(f"Removed stale worker daemon pid: {pid}")
        return

    os.kill(pid, signal.SIGTERM)
    if not _wait_until_stopped(pid):
        typer.echo(f"Worker daemon did not stop: {pid}", err=True)
        raise typer.Exit(1)
    _clear_worker_pid(layout)
    if not quiet:
        typer.echo(f"Worker daemon stopped: {pid}")


def _wait_until_stopped(pid: int, timeout_seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not _pid_is_running(pid):
            return True
        time.sleep(0.1)
    return not _pid_is_running(pid)


def _worker_state_dir(layout) -> Path:
    return layout.team_dir / "worker"


def _worker_pid_path(layout) -> Path:
    return _worker_state_dir(layout) / "worker.pid"


def _worker_log_path(layout) -> Path:
    return _worker_state_dir(layout) / "worker.log"


def _read_worker_pid(layout) -> int | None:
    path = _worker_pid_path(layout)
    if not path.is_file():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except ValueError:
        _clear_worker_pid(layout)
        return None


def _write_worker_pid(layout, pid: int) -> None:
    _worker_state_dir(layout).mkdir(parents=True, exist_ok=True)
    _worker_pid_path(layout).write_text(f"{pid}\n", encoding="utf-8")


def _clear_worker_pid(layout) -> None:
    _worker_pid_path(layout).unlink(missing_ok=True)


def _pid_is_running(pid: int) -> bool:
    if pid < 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


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
    layout = get_project_layout(_resolve_workspace(workspace))
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

    layout = get_project_layout(_resolve_workspace(workspace))
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
    _decide_approval_command(
        approval_id=approval_id,
        workspace=workspace,
        actor_id=actor_id,
        reason=reason,
        approve=True,
    )


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
    _decide_approval_command(
        approval_id=approval_id,
        workspace=workspace,
        actor_id=actor_id,
        reason=reason,
        approve=False,
    )


def _decide_approval_command(
    approval_id: str,
    workspace: Path | None,
    actor_id: str,
    reason: str | None,
    approve: bool,
) -> None:
    layout = get_project_layout(_resolve_workspace(workspace))
    with connect_database(layout.database_path) as connection:
        if approve:
            decision = grant_approval(
                connection,
                approval_id,
                actor_type="human",
                actor_id=actor_id,
                reason=reason,
            )
        else:
            decision = deny_approval(
                connection,
                approval_id,
                actor_type="human",
                actor_id=actor_id,
                reason=reason,
            )

    verb = "Approved" if approve else "Denied"
    typer.echo(f"{verb} {decision.approval.id}")


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
    actor_type: Annotated[str, typer.Option("--actor-type", help="Audit actor type.")] = "human",
    actor_id: Annotated[str, typer.Option("--actor-id", help="Audit actor id.")] = "founder",
    approver_id: Annotated[str, typer.Option("--approver-id", help="Approver id.")] = "founder",
) -> None:
    """Create a task and its brief artifact."""
    layout = get_project_layout(_resolve_workspace(workspace))
    try:
        result = create_authorized_task_with_brief(
            database_path=layout.database_path,
            layout=layout,
            title=title,
            brief_md=brief or title,
            actor_type=actor_type,
            actor_id=actor_id,
            priority=priority,
            approver_id=approver_id,
        )
    except (PermissionError, TypeError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if _echo_pending_capability_approval(result.authorization):
        return

    created = result.created
    if created is None:
        raise RuntimeError("Authorized task creation completed without a task.")
    typer.echo(f"{created.task.id} {created.task.title}")
    typer.echo(f"Brief: {created.brief.relative_path}")


@task_app.command("follow-up")
def task_follow_up_command(
    parent_task_id: Annotated[str, typer.Argument(help="Parent task id, such as TASK-0001.")],
    title: Annotated[str, typer.Argument(help="Follow-up task title.")],
    brief: Annotated[
        str | None,
        typer.Option("--brief", help="Follow-up brief Markdown. Defaults to the title."),
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
    assignee_id: Annotated[
        str | None,
        typer.Option("--assignee-id", "--assignee", help="Optional assignee id."),
    ] = None,
    actor_type: Annotated[str, typer.Option("--actor-type", help="Audit actor type.")] = "agent",
    actor_id: Annotated[str, typer.Option("--actor-id", help="Audit actor id.")] = "george",
    approver_id: Annotated[str, typer.Option("--approver-id", help="Approver id.")] = "founder",
) -> None:
    """Create a linked follow-up task."""
    layout = get_project_layout(_resolve_workspace(workspace))
    try:
        result = create_authorized_follow_up_task(
            database_path=layout.database_path,
            layout=layout,
            parent_task_id=parent_task_id,
            title=title,
            brief_md=brief or title,
            actor_type=actor_type,
            actor_id=actor_id,
            priority=priority,
            assignee_id=assignee_id,
            approver_id=approver_id,
        )
    except (PermissionError, TypeError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if _echo_pending_capability_approval(result.authorization):
        return

    created = result.created
    if created is None:
        raise RuntimeError("Authorized follow-up task creation completed without a task.")
    typer.echo(f"{created.task.id} {created.task.title}")
    typer.echo(f"Parent: {created.parent_task.id}")
    typer.echo(f"Brief: {created.brief.relative_path}")


def _echo_pending_capability_approval(authorization) -> bool:
    if authorization is None or authorization.approval_request is None:
        return False

    approval = authorization.approval_request.approval
    inbox_item = authorization.approval_request.inbox_item
    typer.echo(
        f"{authorization.evaluation.agent_id}  {authorization.evaluation.capability}  "
        f"{authorization.evaluation.decision.value}"
    )
    typer.echo(f"Approval: {approval.id} -> {approval.approver_id}")
    typer.echo(f"Inbox: {inbox_item.id} -> {inbox_item.recipient_id}")
    return True


def _format_audit_event_detail(event) -> str | None:
    if event.event_type == "run.command_executed":
        command = event.payload.get("command")
        exit_code = event.payload.get("exit_code")
        if command is None or exit_code is None:
            return None
        tool = event.payload.get("tool")
        tool_detail = f" tool={tool}" if tool else ""
        return f"command={command} exit_code={exit_code}{tool_detail}"
    if event.event_type == "run.file_changed":
        path = event.payload.get("path")
        change_type = event.payload.get("change_type")
        if path is None or change_type is None:
            return None
        return f"{change_type} {path}"
    return None


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
    layout = get_project_layout(_resolve_workspace(workspace))
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
    layout = get_project_layout(_resolve_workspace(workspace))
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
    if task.parent_task_id:
        typer.echo(f"Parent: {task.parent_task_id}")
    if task.brief_artifact_path:
        typer.echo(f"Brief: {task.brief_artifact_path}")

    with connect_database(layout.database_path) as connection:
        follow_ups = list_follow_up_tasks(connection, task.id)
    if follow_ups:
        typer.echo("Follow-ups:")
        for follow_up in follow_ups:
            typer.echo(f"- {follow_up.id}  {follow_up.status.value}  {follow_up.title}")


@task_app.command("status")
def task_status_command(
    task_id: Annotated[str, typer.Argument(help="Task id, such as TASK-0001.")],
    status: Annotated[str, typer.Argument(help="New task status.")],
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
    """Update a task status."""
    layout = get_project_layout(_resolve_workspace(workspace))
    with connect_database(layout.database_path) as connection:
        try:
            task = update_task_status(
                connection,
                task_id=task_id,
                status=_parse_task_status(status),
                actor_type=actor_type,
                actor_id=actor_id,
            )
        except ValueError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc

    typer.echo(f"{task.id}  {task.status.value}")


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
    actor_type: Annotated[str, typer.Option("--actor-type", help="Audit actor type.")] = "human",
    actor_id: Annotated[str, typer.Option("--actor-id", help="Audit actor id.")] = "founder",
    approver_id: Annotated[str, typer.Option("--approver-id", help="Approver id.")] = "founder",
) -> None:
    """Add a comment to a task."""
    layout = get_project_layout(_resolve_workspace(workspace))
    try:
        result = create_authorized_task_comment(
            database_path=layout.database_path,
            layout=layout,
            task_id=task_id,
            body=body,
            author_type=actor_type,
            author_id=actor_id,
            mentions=tuple(mention or ()),
            approver_id=approver_id,
        )
    except (PermissionError, TypeError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if _echo_pending_capability_approval(result.authorization):
        return

    created = result.created
    if created is None:
        raise RuntimeError("Authorized comment creation completed without a comment.")
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
    layout = get_project_layout(_resolve_workspace(workspace))
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
    requested_model: Annotated[
        str | None,
        typer.Option("--model", help="Requested model for this run."),
    ] = None,
    actor_type: Annotated[str, typer.Option("--actor-type", help="Audit actor type.")] = "human",
    actor_id: Annotated[str, typer.Option("--actor-id", help="Audit actor id.")] = "founder",
) -> None:
    """Create an agent run record and artifact directory."""
    layout = get_project_layout(_resolve_workspace(workspace))
    try:
        created = create_run_with_artifact_dir(
            database_path=layout.database_path,
            layout=layout,
            task_id=task_id,
            agent_id=agent_id,
            actor_type=actor_type,
            actor_id=actor_id,
            requested_model=requested_model,
        )
    except (PermissionError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    typer.echo(
        f"{created.run.id}  {created.run.task_id}  {created.run.agent_id}  "
        f"{created.run.status.value}"
    )
    if created.run.runner_id and created.run.model:
        typer.echo(f"Runner: {created.run.runner_id}")
        typer.echo(f"Model: {created.run.model}")
    typer.echo(f"Artifacts: {created.run.artifact_dir_path}")


@run_app.command("task")
def run_task_command(
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
    requested_model: Annotated[
        str | None,
        typer.Option("--model", help="Requested model for this run."),
    ] = None,
    actor_type: Annotated[str, typer.Option("--actor-type", help="Audit actor type.")] = "agent",
    actor_id: Annotated[
        str | None,
        typer.Option("--actor-id", help="Audit actor id. Defaults to the agent id."),
    ] = None,
    approver_id: Annotated[str, typer.Option("--approver-id", help="Approver id.")] = "founder",
) -> None:
    """Create a task run and prepare its local work packet."""
    layout = get_project_layout(_resolve_workspace(workspace))
    effective_actor_id = actor_id or agent_id
    try:
        result = start_authorized_task_run(
            database_path=layout.database_path,
            layout=layout,
            task_id=task_id,
            agent_id=agent_id,
            actor_type=actor_type,
            actor_id=effective_actor_id,
            approver_id=approver_id,
            requested_model=requested_model,
        )
    except (PermissionError, TypeError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if _echo_pending_capability_approval(result.authorization):
        return

    started = result.started
    if started is None:
        raise RuntimeError("Authorized task run start completed without a run.")
    run = started.work_packet.run
    typer.echo(f"{run.id}  {run.task_id}  {run.agent_id}  {run.status.value}")
    if run.runner_id and run.model:
        typer.echo(f"Runner: {run.runner_id}")
        typer.echo(f"Model: {run.model}")
    typer.echo(f"Artifacts: {run.artifact_dir_path}")
    typer.echo(f"Work packet: {started.work_packet.artifact.relative_path}")


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
    layout = get_project_layout(_resolve_workspace(workspace))
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


@run_app.command("next")
def run_next_command(
    agent_id: Annotated[
        str,
        typer.Option("--agent-id", "--agent", help="Agent id for the run queue."),
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
) -> None:
    """Show the next actionable run for an agent."""
    layout = get_project_layout(_resolve_workspace(workspace))
    with connect_database(layout.database_path) as connection:
        run = get_next_agent_run(connection, agent_id)
        task = get_task(connection, run.task_id) if run is not None else None

    if run is None:
        typer.echo("No actionable runs.")
        return
    if task is None:
        typer.echo(f"Task not found for run {run.id}: {run.task_id}", err=True)
        raise typer.Exit(1)

    typer.echo(f"{run.id}  {run.status.value}  {run.task_id}  {task.title}")
    if run.artifact_dir_path:
        typer.echo(f"Artifacts: {run.artifact_dir_path}")


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
    layout = get_project_layout(_resolve_workspace(workspace))
    with connect_database(layout.database_path) as connection:
        run = get_run(connection, run_id)

    if run is None:
        typer.echo(f"Run not found: {run_id}", err=True)
        raise typer.Exit(1)

    typer.echo(f"ID: {run.id}")
    typer.echo(f"Task: {run.task_id}")
    typer.echo(f"Agent: {run.agent_id}")
    typer.echo(f"Status: {run.status.value}")
    if run.runner_id:
        typer.echo(f"Runner: {run.runner_id}")
    if run.model:
        typer.echo(f"Model: {run.model}")
    if run.requested_model:
        typer.echo(f"Requested model: {run.requested_model}")
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
    layout = get_project_layout(_resolve_workspace(workspace))
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


@run_app.command("work")
def run_work_command(
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
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="Replace an existing run work packet."),
    ] = False,
    actor_type: Annotated[str, typer.Option("--actor-type", help="Audit actor type.")] = "agent",
    actor_id: Annotated[str, typer.Option("--actor-id", help="Audit actor id.")] = "george",
    approver_id: Annotated[str, typer.Option("--approver-id", help="Approver id.")] = "founder",
) -> None:
    """Prepare a local work packet for a run."""
    layout = get_project_layout(_resolve_workspace(workspace))
    try:
        result = prepare_authorized_run_work_packet(
            database_path=layout.database_path,
            layout=layout,
            run_id=run_id,
            actor_type=actor_type,
            actor_id=actor_id,
            overwrite=overwrite,
            approver_id=approver_id,
        )
    except (PermissionError, TypeError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if _echo_pending_capability_approval(result.authorization):
        return

    packet = result.packet
    if packet is None:
        raise RuntimeError("Authorized run work preparation completed without a packet.")
    typer.echo(f"{packet.run.id}  {packet.run.status.value}")
    typer.echo(f"Work packet: {packet.artifact.relative_path}")


@run_app.command("command")
def run_command_record_command(
    run_id: Annotated[str, typer.Argument(help="Run id, such as RUN-0001.")],
    command: Annotated[str, typer.Argument(help="Command that was executed.")],
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
    exit_code: Annotated[int, typer.Option("--exit-code", help="Command exit code.")] = 0,
    cwd: Annotated[
        str | None,
        typer.Option("--cwd", help="Working directory used for the command."),
    ] = None,
    duration_ms: Annotated[
        int | None,
        typer.Option("--duration-ms", help="Command duration in milliseconds."),
    ] = None,
    actor_type: Annotated[str, typer.Option("--actor-type", help="Audit actor type.")] = "agent",
    actor_id: Annotated[str, typer.Option("--actor-id", help="Audit actor id.")] = "george",
) -> None:
    """Record command execution metadata for a run."""
    layout = get_project_layout(_resolve_workspace(workspace))
    try:
        record = record_run_command(
            database_path=layout.database_path,
            run_id=run_id,
            command=command,
            exit_code=exit_code,
            cwd=cwd,
            duration_ms=duration_ms,
            actor_type=actor_type,
            actor_id=actor_id,
        )
    except (TypeError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"{record.run.id}  command  {record.event.payload['exit_code']}")
    typer.echo(f"Event: {record.event.id}")


@run_app.command("file")
def run_file_record_command(
    run_id: Annotated[str, typer.Argument(help="Run id, such as RUN-0001.")],
    path: Annotated[str, typer.Argument(help="Workspace-relative file path.")],
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
    change: Annotated[
        str,
        typer.Option("--change", help="added, modified, deleted, or renamed."),
    ] = "modified",
    additions: Annotated[
        int | None,
        typer.Option("--additions", help="Added line count."),
    ] = None,
    deletions: Annotated[
        int | None,
        typer.Option("--deletions", help="Deleted line count."),
    ] = None,
    previous_path: Annotated[
        str | None,
        typer.Option("--previous-path", help="Previous path for renamed files."),
    ] = None,
    actor_type: Annotated[str, typer.Option("--actor-type", help="Audit actor type.")] = "agent",
    actor_id: Annotated[str, typer.Option("--actor-id", help="Audit actor id.")] = "george",
) -> None:
    """Record file-change metadata for a run."""
    layout = get_project_layout(_resolve_workspace(workspace))
    try:
        record = record_run_file_change(
            database_path=layout.database_path,
            layout=layout,
            run_id=run_id,
            relative_path=path,
            change_type=_parse_run_file_change_type(change),
            additions=additions,
            deletions=deletions,
            previous_path=previous_path,
            actor_type=actor_type,
            actor_id=actor_id,
        )
    except (TypeError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"{record.run.id}  file  {record.event.payload['change_type']}")
    typer.echo(f"Path: {record.event.payload['path']}")
    typer.echo(f"Event: {record.event.id}")


@run_app.command("audit-git")
def run_audit_git_command(
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
    repo: Annotated[
        str,
        typer.Option("--repo", help="Workspace-relative git repository path."),
    ] = ".",
    base: Annotated[str, typer.Option("--base", help="Git base ref for diff.")] = "HEAD",
    actor_type: Annotated[str, typer.Option("--actor-type", help="Audit actor type.")] = "agent",
    actor_id: Annotated[str, typer.Option("--actor-id", help="Audit actor id.")] = "george",
) -> None:
    """Record file-change audit events from a git diff."""
    layout = get_project_layout(_resolve_workspace(workspace))
    try:
        repo_path = _resolve_workspace_child(layout.workspace, repo, "repo")
        changes = _load_git_name_status(repo_path, base)
        records = [
            record_run_file_change(
                database_path=layout.database_path,
                layout=layout,
                run_id=run_id,
                relative_path=_workspace_relative_git_path(layout.workspace, repo_path, change.path),
                previous_path=(
                    _workspace_relative_git_path(layout.workspace, repo_path, change.previous_path)
                    if change.previous_path
                    else None
                ),
                change_type=change.change_type,
                actor_type=actor_type,
                actor_id=actor_id,
            )
            for change in changes
        ]
    except (subprocess.SubprocessError, TypeError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"Recorded {len(records)} file change(s).")
    for record in records:
        typer.echo(
            f"{record.event.payload['change_type']}  {record.event.payload['path']}  "
            f"{record.event.id}"
        )


@run_app.command("submit")
def run_submit_command(
    run_id: Annotated[str, typer.Argument(help="Run id, such as RUN-0001.")],
    summary: Annotated[str, typer.Argument(help="Review summary Markdown.")],
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
    checks: Annotated[
        str | None,
        typer.Option("--checks", help="Checks or test results Markdown."),
    ] = None,
    reviewer_id: Annotated[
        str,
        typer.Option("--reviewer-id", "--reviewer", help="Reviewer recipient id."),
    ] = "supervisor",
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="Replace an existing result artifact."),
    ] = False,
    actor_type: Annotated[str, typer.Option("--actor-type", help="Audit actor type.")] = "agent",
    actor_id: Annotated[str, typer.Option("--actor-id", help="Audit actor id.")] = "george",
    approver_id: Annotated[str, typer.Option("--approver-id", help="Approver id.")] = "founder",
) -> None:
    """Submit a run result for review."""
    layout = get_project_layout(_resolve_workspace(workspace))
    try:
        result = submit_authorized_run_for_review(
            database_path=layout.database_path,
            layout=layout,
            run_id=run_id,
            summary_md=summary,
            checks_md=checks,
            reviewer_id=reviewer_id,
            actor_type=actor_type,
            actor_id=actor_id,
            overwrite=overwrite,
            approver_id=approver_id,
        )
    except (PermissionError, TypeError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if _echo_pending_capability_approval(result.authorization):
        return

    submission = result.submission
    if submission is None:
        raise RuntimeError("Authorized run submission completed without a submission.")
    typer.echo(f"{submission.run.id}  {submission.run.status.value}")
    typer.echo(f"Result: {submission.artifact.relative_path}")
    typer.echo(f"Review request: {submission.comment.comment.id} -> {reviewer_id}")


def _format_state(value: bool) -> str:
    return "ok" if value else "missing"


def _parse_approval_status(value: str) -> ApprovalStatus | None:
    if value == "all":
        return None
    try:
        return ApprovalStatus(value)
    except ValueError as exc:
        raise typer.BadParameter("status must be pending, granted, denied, or all") from exc


def _parse_run_file_change_type(value: str) -> RunFileChangeType:
    try:
        return RunFileChangeType(value)
    except ValueError as exc:
        raise typer.BadParameter("change must be added, modified, deleted, or renamed") from exc


def _load_git_name_status(repo_path: Path, base: str) -> tuple[GitFileChange, ...]:
    clean_base = _require_non_empty(base, "base")
    if clean_base == "HEAD" and not _git_has_head(repo_path):
        return _load_untracked_git_files(repo_path)

    completed = subprocess.run(
        ["git", "-C", str(repo_path), "diff", "--name-status", "--find-renames", clean_base, "--"],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        message = completed.stderr.strip() or f"git diff failed with exit code {completed.returncode}"
        raise ValueError(message)

    return tuple(_parse_git_name_status_line(line) for line in completed.stdout.splitlines() if line)


def _git_has_head(repo_path: Path) -> bool:
    completed = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", "--verify", "HEAD"],
        text=True,
        capture_output=True,
        check=False,
    )
    return completed.returncode == 0


def _load_untracked_git_files(repo_path: Path) -> tuple[GitFileChange, ...]:
    completed = subprocess.run(
        ["git", "-C", str(repo_path), "ls-files", "--others", "--exclude-standard"],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        message = completed.stderr.strip() or "git ls-files failed"
        raise ValueError(message)

    return tuple(
        GitFileChange(RunFileChangeType.ADDED, line)
        for line in completed.stdout.splitlines()
        if line
    )


def _parse_git_name_status_line(line: str) -> GitFileChange:
    parts = line.split("\t")
    status = parts[0]
    if status == "A" and len(parts) == 2:
        return GitFileChange(RunFileChangeType.ADDED, parts[1])
    if status == "M" and len(parts) == 2:
        return GitFileChange(RunFileChangeType.MODIFIED, parts[1])
    if status == "D" and len(parts) == 2:
        return GitFileChange(RunFileChangeType.DELETED, parts[1])
    if status.startswith("R") and len(parts) == 3:
        return GitFileChange(RunFileChangeType.RENAMED, parts[2], previous_path=parts[1])
    raise ValueError(f"Unsupported git file status line: {line}")


def _workspace_relative_git_path(workspace: Path, repo_path: Path, git_path: str) -> str:
    clean_git_path = _require_non_empty(git_path, "git_path")
    path = Path(clean_git_path)
    if path.is_absolute():
        raise ValueError("git_path must be relative.")

    resolved = (repo_path / path).resolve(strict=False)
    try:
        return resolved.relative_to(workspace.resolve(strict=False)).as_posix()
    except ValueError as exc:
        raise ValueError(f"git_path escapes the workspace: {git_path}") from exc


def _resolve_workspace_child(workspace: Path, value: str, field_name: str) -> Path:
    clean_value = _require_non_empty(value, field_name)
    path = Path(clean_value)
    resolved = (
        path.resolve(strict=False)
        if path.is_absolute()
        else (workspace / path).resolve(strict=False)
    )
    try:
        resolved.relative_to(workspace.resolve(strict=False))
    except ValueError as exc:
        raise ValueError(f"{field_name} escapes the workspace: {value}") from exc
    return resolved


def _require_non_empty(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string.")
    return value.strip()


def _parse_inbox_status(value: str) -> InboxItemStatus | None:
    if value == "all":
        return None
    try:
        return InboxItemStatus(value)
    except ValueError as exc:
        raise typer.BadParameter("status must be open, done, or all") from exc


def _parse_task_status(value: str) -> TaskStatus:
    try:
        return TaskStatus(value)
    except ValueError as exc:
        allowed = ", ".join(status.value for status in TaskStatus)
        raise typer.BadParameter(f"status must be one of: {allowed}") from exc


def _parse_run_status(value: str) -> RunStatus | None:
    if value == "all":
        return None
    try:
        return RunStatus(value)
    except ValueError as exc:
        allowed = ", ".join(status.value for status in RunStatus)
        raise typer.BadParameter(f"status must be one of: {allowed}, or all") from exc


def _parse_review_decision(value: str) -> RunReviewDecision:
    try:
        return RunReviewDecision(value)
    except ValueError as exc:
        raise typer.BadParameter("decision must be approve or return") from exc


if __name__ == "__main__":
    app()
