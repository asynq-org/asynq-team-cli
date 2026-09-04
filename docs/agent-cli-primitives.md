# Agent CLI Primitives

This document lists lower-level CLI commands for agents and maintainers. Founder-facing
flows belong in the README.

## Workspace Setup

```bash
poetry run team init --workspace /path/to/workspace --project-name "Example Team"
poetry run team workspace use /path/to/workspace
poetry run team workspace current
poetry run team init --workspace /path/to/workspace \
  --project-name "Example Team" \
  --git-remote git@github.com:example/team-state.git
```

## Inspect Workspace

```bash
poetry run team status --workspace /path/to/workspace
poetry run team doctor --workspace /path/to/workspace
poetry run team config show --workspace /path/to/workspace
poetry run team backup run --workspace /path/to/workspace
poetry run team backup list --workspace /path/to/workspace
poetry run team audit show TASK-0001 --workspace /path/to/workspace
poetry run team policy check george main.merge --workspace /path/to/workspace
poetry run team policy authorize george main.merge "Merge reviewed changes." \
  --subject-type run \
  --subject-id RUN-0001 \
  --workspace /path/to/workspace
poetry run team agent show george --workspace /path/to/workspace
poetry run team runner check shell.test --workspace /path/to/workspace
poetry run -- team runner exec RUN-0001 shell.test --workspace /path/to/workspace -- poetry run pytest
```

Use `poetry run -- team ...` for `runner exec` commands. The first `--` stops Poetry
from parsing runner options before the second `--` passes the command argv to Asynq Team.

## Worker Loop

```bash
poetry run team worker run-once --workspace /path/to/workspace
poetry run team worker start --workspace /path/to/workspace
poetry run team worker start --workspace /path/to/workspace --iterations 1
```

## Tasks

```bash
poetry run team task create "Review onboarding" \
  --brief "Check first-run setup and list blockers." \
  --workspace /path/to/workspace

poetry run team task create "Capture follow-up" \
  --actor-type agent \
  --actor-id george \
  --workspace /path/to/workspace

poetry run team task follow-up TASK-0001 "Document review checklist" \
  --brief "Capture the checklist as a future scoped improvement." \
  --workspace /path/to/workspace

poetry run team task list --workspace /path/to/workspace
poetry run team task show TASK-0001 --workspace /path/to/workspace
poetry run team task status TASK-0001 in_progress --workspace /path/to/workspace
poetry run team task comment TASK-0001 "Please review this." \
  --mention supervisor \
  --workspace /path/to/workspace
poetry run team task comment TASK-0001 "I found a follow-up." \
  --actor-type agent \
  --actor-id george \
  --workspace /path/to/workspace
poetry run team task comments TASK-0001 --workspace /path/to/workspace
```

## Runs

```bash
poetry run team run task TASK-0001 \
  --agent-id george \
  --model gpt-5-codex \
  --workspace /path/to/workspace

poetry run team run create TASK-0001 \
  --agent-id george \
  --model gpt-5-codex \
  --workspace /path/to/workspace

poetry run team run list --workspace /path/to/workspace
poetry run team run next --agent george --workspace /path/to/workspace
poetry run team run show RUN-0001 --workspace /path/to/workspace
poetry run team run status RUN-0001 planning --workspace /path/to/workspace
poetry run team run work RUN-0001 --workspace /path/to/workspace
poetry run team run command RUN-0001 "poetry run pytest" \
  --exit-code 0 \
  --cwd repos/core \
  --workspace /path/to/workspace
poetry run team run file RUN-0001 repos/core/src/example.py \
  --change modified \
  --additions 12 \
  --deletions 3 \
  --workspace /path/to/workspace
poetry run team run audit-git RUN-0001 \
  --repo repos/core \
  --workspace /path/to/workspace
poetry run team run submit RUN-0001 "Implemented the first pass." \
  --checks "- poetry run pytest" \
  --workspace /path/to/workspace

poetry run team review RUN-0001 approve "Looks ready." \
  --workspace /path/to/workspace
```

## Inbox And Approvals

```bash
poetry run team inbox --workspace /path/to/workspace
poetry run team approvals --workspace /path/to/workspace
poetry run team approval show APR-0001 --workspace /path/to/workspace
poetry run team approvals approve APR-0001 --workspace /path/to/workspace
poetry run team approvals deny APR-0002 --workspace /path/to/workspace
poetry run team approve APR-0001 --workspace /path/to/workspace
poetry run team deny APR-0002 --workspace /path/to/workspace
```
