# Handoff Context

## Project State

- Repository: `I:\AI_Workspace\agency-agents-zh`
- Remote setup:
  - `origin`: `https://github.com/2587122824/agency-agents-zh.git`
  - `upstream`: `https://github.com/jnMetaCode/agency-agents-zh.git`
- Current custom workspace: `my_workspace/`
- Original project agent files were not modified. All custom additions are under `my_workspace/` and `docs/HANDOFF_CONTEXT.md`.

## Custom Workspace

`my_workspace/` contains a local self-media workflow system:

```text
my_workspace/
  my_codex_core/
  my_custom_staff/
  my_workflows/
  my_task_output/
  run_flow.py
  web_app.py
  README.md
```

### Custom Staff

Each staff folder contains:

```text
agent.md
flow_rule.json
```

Current custom staff:

```text
01_需求拆解专员
02_短视频编导
03_口播脚本师
04_标题封面优化师
05_内容合规审核官
```

### Workflows

Current workflow configs:

```text
my_workspace/my_workflows/workflow_短视频全流程.json
my_workspace/my_workflows/workflow_小红书图文.json
my_workspace/my_workflows/workflow_开发外包.json
```

## Automation Engine

CLI entrypoint:

```powershell
python my_workspace/run_flow.py --workflow workflow_短视频全流程 --input "你的内容需求"
```

Core files:

```text
my_workspace/my_codex_core/staff_loader.py
my_workspace/my_codex_core/workflow_engine.py
my_workspace/my_codex_core/task_storage.py
my_workspace/my_codex_core/codex_api.py
```

Behavior:

- Loads workflow JSON from `my_workspace/my_workflows/`.
- Loads staff definitions from `my_workspace/my_custom_staff/`.
- Executes steps in workflow order.
- Writes all outputs to `my_workspace/my_task_output/task_时间戳_工作流名/`.
- Generates `final_output.md` and `run_summary.json`.
- Supports `offline`, `openai`, and `auto` provider modes.

Provider modes:

```text
offline: does not call a model; writes prompt packages and placeholder outputs.
openai: calls an OpenAI-compatible chat completions endpoint using OPENAI_API_KEY.
auto: uses openai when OPENAI_API_KEY exists, otherwise offline.
```

OpenAI mode environment variables:

```powershell
$env:OPENAI_API_KEY="你的 API Key"
$env:OPENAI_MODEL="gpt-4.1-mini"
```

## Visual Management UI

Web entrypoint:

```powershell
python my_workspace/web_app.py
```

Default URL:

```text
http://127.0.0.1:8765
```

Current verified capabilities:

- Lists 3 workflows.
- Lists 5 custom staff folders.
- Runs workflows through `/api/run`.
- Shows historical tasks from `my_task_output`.
- Opens `prompt.md`, `output.md`, `metadata.json`, `workflow.json`, and `final_output.md`.

The web service was started and verified successfully during setup. If not running in a future session, restart it with the command above.

## Output Handling

`my_workspace/my_task_output/.gitignore` ignores generated task result folders so routine output is not committed by accident.

Tracked output-related file intended to keep:

```text
my_workspace/my_task_output/.gitignore
```

Generated task folders are disposable unless the user explicitly wants to preserve a specific run.

## Verification Already Run

- Python syntax check passed for:
  - `my_workspace/run_flow.py`
  - `my_workspace/web_app.py`
  - all Python files under `my_workspace/my_codex_core/`
- JSON parsing passed for:
  - all staff `flow_rule.json`
  - all workflow JSON files
  - generated metadata JSON files
- CLI offline workflow run succeeded for `workflow_短视频全流程`.
- Web API offline workflow run succeeded for `workflow_小红书图文`.
- Local web UI responded with HTTP 200 and showed valid config.

## Important Notes

- PowerShell may display UTF-8 Chinese text incorrectly unless commands explicitly read with UTF-8. The files themselves are UTF-8.
- `package.json` from the upstream repo may also display mojibake in default PowerShell output; avoid treating terminal mojibake as file corruption without UTF-8 verification.
- The web UI is intentionally dependency-free and uses Python standard library only.
- Current engine uses OpenAI-compatible `/chat/completions`. If the user wants Responses API or another provider, update `my_workspace/my_codex_core/codex_api.py`.

## Suggested Next Improvements

1. Add a proper model provider selector for OpenAI Responses API or local models.
2. Add a task delete button in the web UI.
3. Add workflow editing in the UI.
4. Add staff editing and preview in the UI.
5. Add export buttons for final Markdown, script-only output, and publish checklist.
6. Add a real Markdown renderer in the viewer instead of plain `<pre>` output.
7. Add `.env` loading so users do not need to set environment variables manually each session.
