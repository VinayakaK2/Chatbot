# Coding Agent Runtime

A minimal, runnable coding-agent harness designed around one boundary: the model may propose actions; the deterministic runtime validates, authorizes, executes, records, and bounds them.

It intentionally focuses on the first production-critical vertical slice:

- a bounded model/tool loop;
- flat agent declarations in `agents/*.md` with YAML frontmatter;
- bounded context assembly from task plus recent persisted evidence;
- workspace-contained read, write, and shell tools;
- deny-by-default gates for writes and shell commands;
- append-only JSONL session histories in `.agent-runtime/sessions/`;
- an optional OpenAI-compatible provider configured only through environment variables;
- a deterministic no-credential demo and unit tests.

## Quick start

Python 3.11+ is sufficient; the runtime has no third-party runtime dependencies.

```powershell
python -m unittest discover -s tests -v
python -m coding_agent_runtime --demo
```

The demo reads the agent specification, writes an event history under `.agent-runtime/sessions/`, and performs no writes or shell execution.

## Live model configuration

Copy `.env.example` to a local, ignored `.env` only if your launcher loads it, or export these values through your normal secret manager:

```text
AGENT_MODEL_BASE_URL=https://your-compatible-api.example/v1
AGENT_MODEL_API_KEY=...
AGENT_MODEL_NAME=...
```

Then run the selected agent. The runtime does not load `.env` itself, does not invent credentials, and requires all three variables:

```powershell
$env:AGENT_MODEL_BASE_URL = "https://your-compatible-api.example/v1"
$env:AGENT_MODEL_API_KEY = "<secret from your secret manager>"
$env:AGENT_MODEL_NAME = "your-model-name"
python -m coding_agent_runtime "Inspect this repository and summarize its tests."
```

Writes and shell commands remain denied unless explicitly enabled for that invocation:

```powershell
python -m coding_agent_runtime "Add a focused unit test." --allow-writes
python -m coding_agent_runtime "Run the unit tests." --allow-shell
```

Those switches are deliberately separate. A real deployment should replace the CLI decision with a UI or policy service that supplies `PermissionPolicy.approval_callback` per operation.

## Design and limitations

`AgentOrchestrator` is the only loop owner. It sends assembled context to a provider, records the proposal, verifies the selected agent's allowlist, dispatches a deterministic tool, records the observation, and repeats until completion or `--max-turns` (12 by default). Tool results become evidence for the next turn; they are not trusted model claims.

This first slice is intentionally not a sandbox, shell command classifier, remote-session service, planner, subagent framework, or UI. Shell execution is therefore opt-in and should be used only in a trusted workspace. The next safe increments are a stronger OS/container sandbox, structured interactive approvals, streaming provider support, richer compaction, and a separate evaluator pass.
