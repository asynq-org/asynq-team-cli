# Asynq Team CLI

`asynq-team-cli` is the terminal interface for local-first Asynq Team workspaces.

Asynq Team is a local-first operating layer for working with AI agents as a small software team. It turns agent work into explicit tasks, reviewable artifacts, approval records, and audit trails instead of leaving important decisions buried in chat history.

The CLI is useful when you want:

- a project-local task ledger for agent and human work;
- repeatable workspace initialization under `.team/`;
- Markdown artifacts that are easy to inspect and review in git;
- a local SQLite runtime instead of a hosted service requirement;
- a clear inbox for questions, blockers, and approvals;
- explicit approve/deny commands for sensitive actions.

It helps initialize a `.team/` workspace, create tasks, inspect runtime config, and handle the MVP human attention loop through inbox and approval commands.

The CLI is early and pre-1.0. Command names and output may change while the MVP is still taking shape.

## Install From Source

The CLI currently depends on a local checkout of `asynq-team-core`.

```bash
git clone git@github.com:asynq-org/asynq-team-core.git
git clone git@github.com:asynq-org/asynq-team-cli.git
cd asynq-team-cli
poetry install
```

Run the CLI through Poetry:

```bash
poetry run team --help
```

## Quick Start

Initialize a workspace:

```bash
poetry run team init --workspace /path/to/workspace --project-name "Example Team"
```

This creates local runtime state under `.team/`, including:

- `.team/config.yaml`;
- `.team/team.db`;
- default agent manifests;
- default rule files;
- default policy files.

Inspect the workspace:

```bash
poetry run team status --workspace /path/to/workspace
poetry run team config show --workspace /path/to/workspace
```

Create and list tasks:

```bash
poetry run team task create "Review onboarding" \
  --brief "Check first-run setup and list blockers." \
  --workspace /path/to/workspace

poetry run team task list --workspace /path/to/workspace
poetry run team task show TASK-0001 --workspace /path/to/workspace
```

Review inbox items and approvals:

```bash
poetry run team inbox --workspace /path/to/workspace
poetry run team approvals --workspace /path/to/workspace
poetry run team approvals approve APR-0001 --workspace /path/to/workspace
poetry run team approvals deny APR-0002 --workspace /path/to/workspace
```

## Development

Use Poetry for local development:

```bash
poetry install
poetry run python scripts/check_release_metadata.py
poetry run ruff check .
poetry run pytest
poetry run pip-audit
```

Before committing package-relevant changes, update `project.version` and add a release-note fragment under `.release-notes/`.
