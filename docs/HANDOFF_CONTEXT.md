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
  my_memory/
  my_reference_images/
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
06_分镜生图设计师
07_视频生成执行员
```

### Workflows

Current workflow configs:

```text
my_workspace/my_workflows/workflow_短视频全流程.json
my_workspace/my_workflows/workflow_小红书图文.json
my_workspace/my_workflows/workflow_开发外包.json
```

### Long-Term Memory

Long-term memory templates are stored in:

```text
my_workspace/my_memory/brand_profile.md
my_workspace/my_memory/character_bible.md
my_workspace/my_memory/style_guide.md
```

The web UI can append these files to each workflow run when `启用 my_memory` is selected.

### Reference Images

Reference image uploads are stored under:

```text
my_workspace/my_reference_images/
```

The directory tracks only `.gitignore`; uploaded images are local generated assets and should not be committed by default.

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
my_workspace/my_codex_core/production_pipeline.py
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
$env:OPENAI_MODEL="gpt-5.5"
```

For relay/proxy providers, set an OpenAI-compatible base URL:

```powershell
$env:OPENAI_API_KEY="你的中转站 Key"
$env:OPENAI_BASE_URL="https://你的中转站域名/v1"
```

The CLI also supports a one-off key:

```powershell
python my_workspace/run_flow.py --provider openai --api-key "你的 API Key" --base-url "https://你的中转站域名/v1" --model gpt-5.5 --workflow workflow_短视频全流程 --input "你的内容需求"
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
- Lists 7 custom staff folders.
- Runs workflows through `/api/run`.
- Accepts a one-off API Key from the web form; the key is passed to the engine for that request only and is not written to task output.
- Accepts a one-off OpenAI-compatible Base URL from the web form for relay/proxy providers; the URL is used only for that request and is not written to task output.
- Provides a grouped model dropdown with recommended, lightweight, reasoning, legacy-compatible, and custom model options.
- Provides image-generation configuration fields in the web UI: target image tool, model, size/aspect, count per shot, style, quality, negative prompt, consistency focus, image API key presence, and image base URL presence. These fields are appended to workflow input for `06_分镜生图设计师`; the system does not directly call image-generation APIs yet.
- Provides video-generation configuration fields in the web UI: target tool, model, aspect ratio, duration, style, video API key presence, and video base URL presence. These fields are appended to workflow input for `06_分镜生图设计师` and `07_视频生成执行员`; the system does not directly call video-generation APIs yet.
- Provides reference image upload fields in the web UI. Selected local images show thumbnail previews before running. Uploaded images are saved under `my_reference_images/`; workflow input receives stored path, role, and note for `06_分镜生图设计师` and `07_视频生成执行员`. No vision model is used yet, so the system does not claim to understand image content.
- Provides memory controls in the web UI: `长期记忆` appends `my_memory/*.md`, and `继承历史任务` can append either the previous final product only or both the previous demand and final product.
- Persists web UI settings in browser `localStorage` by default, including API keys, Base URLs, model selections, image-generation configuration, and video-generation configuration. The `清除已保存配置` button removes the saved local settings.
- Shows workflow run progress in the web UI with a progress bar and per-step staff status. `/api/run` now starts a background job and returns a `run_id`; `/api/run-status?id=...` returns current status.
- Supports an optional web UI task name. When provided, the task name is used in the output directory suffix, task list display, run progress title, and `run_summary.json` `task_title`.
- Web UI layout keeps core workflow/task/input fields visible first, with model interface, memory inheritance, image-generation, video-generation, and reference-image controls moved into compact collapsible sections.
- Provides a full-auto production framework option. In `package_only` mode it creates `production_manifest.json`, `auto_production.md`, image prompt files, video prompt files, audio/subtitle placeholders, output folders, and an edit checklist without calling external media APIs.
- Shows historical tasks from `my_task_output`.
- Opens `prompt.md`, `output.md`, `metadata.json`, `workflow.json`, and `final_output.md`.
- Deletes historical task output directories through `/api/delete-task`; deletion is constrained to a validated child directory under `my_workspace/my_task_output`.

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
- `06_分镜生图设计师` was added before video generation; `workflow_短视频全流程` and `workflow_开发外包` now run 06 for storyboard/keyframe image prompts, then 07 for video generation packages. 07 produces prompts, shot lists, TTS copy, SRT drafts, and edit instructions; it does not create mp4 files directly.
- Local web UI responded with HTTP 200 and showed valid config.
- Reference image UI and `/api/upload-reference-image` were verified with HTTP 200. The endpoint returned a stored local path under `my_reference_images/` for `06_分镜生图设计师` and `07_视频生成执行员`.

## Important Notes

- PowerShell may display UTF-8 Chinese text incorrectly unless commands explicitly read with UTF-8. The files themselves are UTF-8.
- `package.json` from the upstream repo may also display mojibake in default PowerShell output; avoid treating terminal mojibake as file corruption without UTF-8 verification.
- The web UI is intentionally dependency-free and uses Python standard library only.
- Web UI saved settings are client-side browser state only; they are not committed to the repo and are not written into `my_task_output`, but anyone with access to the same browser profile may reuse them.
- Long-term memory files are tracked in the repo as editable templates. They should contain reusable brand/style/character guidance, not secrets.
- Current engine uses OpenAI-compatible `/chat/completions`. Official default base URL is `https://api.openai.com/v1`; relay/proxy providers can be configured through `OPENAI_BASE_URL`, CLI `--base-url`, or the web UI `中转站 Base URL` field.
- Image provider API fields in the UI are currently planning/configuration inputs only. They are not used to call GPT Image, Midjourney, Stable Diffusion, FLUX, Seedream, Jimeng, Kling, or other image APIs.
- Video provider API fields in the UI are currently planning/configuration inputs only. They are not used to call Sora, Runway, Pika, Seedance, Kling, Jimeng, Hailuo, Luma, or other video APIs.
- Full-auto production currently generates a production asset package only. Real image/video API adapters should read `production_manifest.json` and write generated media into `generated_images/` and `video_clips/`.
- Reference images are local assets for prompt planning. Actual image understanding requires adding a vision model later.
- Default model is currently `gpt-5.5`; UI also offers `gpt-5.4`, `gpt-5.4-mini`, `gpt-5.4-nano`, `o3`, `o4-mini`, `gpt-4.1`, `gpt-4.1-mini`, `gpt-4o`, `gpt-4o-mini`, and a custom model field.
- GitHub Actions were adjusted for this fork: CI has `paths-ignore` for `docs/**` and `my_workspace/**`, and its agent scan prunes `my_workspace/`; `Sync to Gitee` is manual-only and skips when `GITEE_TOKEN` is absent.

## Suggested Next Improvements

1. Add a proper model provider selector for OpenAI Responses API or local models.
2. Add actual video API adapters for a selected provider after choosing which provider to use first.
3. Add workflow editing in the UI.
4. Add staff editing and preview in the UI.
5. Add export buttons for final Markdown, script-only output, and publish checklist.
6. Add a real Markdown renderer in the viewer instead of plain `<pre>` output.
7. Add `.env` loading so users do not need to set environment variables manually each session.

## Unity 3D Steam Game Workflow

Added a custom Unity 3D game team under `my_workspace/my_custom_staff/` based on original project agents:

```text
08_游戏项目编排制片人
09_游戏创意与系统设计师
10_Unity3D架构工程师
11_关卡叙事设计师
12_3D美术技术指导
13_游戏音频与体验设计师
14_Steam发行与测试经理
```

The source inspirations are kept in each staff `flow_rule.json` under `source_agents`. Original project agent files were not modified.

New workflow:

```text
my_workspace/my_workflows/workflow_Unity3D游戏Steam上架.json
```

Purpose: turn a Unity 3D Steam game idea into a production blueprint, including project orchestration, GDD, Unity architecture, level/narrative design, 3D technical art, game audio/feedback, Steam store/launch/testing package.

Management UI changes:

- Web title changed from self-media-specific wording to `自定义工作流管理台`.
- Added `游戏示例` button. It selects `workflow_Unity3D游戏Steam上架`, fills a small-team Unity 3D exploration puzzle game example, and sets relevant image/video planning defaults.
- Existing workflow execution, progress tracking, task output, deletion, localStorage config, and history viewer are reused for the game workflow.

Verification run:

```powershell
python -m py_compile my_workspace/run_flow.py my_workspace/web_app.py my_workspace/my_codex_core/*.py
```

PowerShell did not expand `*.py`, so the syntax check was rerun with explicit file enumeration and passed.

```powershell
python my_workspace/run_flow.py --provider offline --workflow workflow_Unity3D游戏Steam上架 --input "我要做一款 Unity 3D 第三人称探索解谜游戏，上架 Steam。目标玩家喜欢低多边形、环境谜题和短流程独立游戏。团队规模是单人或两人，先做 20-30 分钟 Demo，不做联网，不做大型开放世界。"
```

Result: workflow completed offline with 7 steps and wrote output under:

```text
my_workspace/my_task_output/task_20260613_103554_workflow_Unity3D游戏Steam上架/
```

The web management UI was restarted on:

```text
http://127.0.0.1:8765
```

Verified HTTP 200 and page markers:

```text
gameSampleBtn
自定义工作流管理台
workflow_Unity3D游戏Steam上架
```

## Software Market Opportunity Analyst

Added a new custom staff member:

```text
my_workspace/my_custom_staff/15_软件市场需求分析师/
```

Files:

```text
agent.md
flow_rule.json
```

This staff combines source-agent ideas from:

```text
product/product-trend-researcher.md
product/product-manager.md
product/product-feedback-synthesizer.md
marketing/marketing-growth-hacker.md
marketing/marketing-app-store-optimizer.md
engineering/engineering-rapid-prototyper.md
engineering/engineering-software-architect.md
```

Added workflow:

```text
my_workspace/my_workflows/workflow_软件市场机会分析.json
```

Purpose: evaluate high-potential software opportunities by market pain, timing, MVP difficulty, acquisition feasibility, monetization, and risk. The workflow has one step using `15_软件市场需求分析师` and is intended for use from the management UI or CLI.

## AI Staff Workflow Platform Design

Added platform-design staff:

```text
16_AI员工平台产品经理
17_AI员工平台工作流架构师
18_AI员工平台软件架构师
19_AI员工平台增长验证师
```

Source-agent inspirations:

```text
product/product-manager.md
product/product-trend-researcher.md
product/product-feedback-synthesizer.md
specialized/specialized-workflow-architect.md
specialized/agents-orchestrator.md
engineering/engineering-software-architect.md
engineering/engineering-backend-architect.md
engineering/engineering-frontend-developer.md
design/design-ux-architect.md
marketing/marketing-growth-hacker.md
marketing/marketing-app-store-optimizer.md
```

Added workflow:

```text
my_workspace/my_workflows/workflow_AI员工工作流平台设计.json
```

Purpose: design an AI staff workflow platform using `my_custom_staff` as the custom employee source, covering product positioning, workflow architecture, software architecture, and growth validation.

Management UI update:

- Added `数字员工管理` panel to `my_workspace/web_app.py`.
- New UI can list, select, create, edit, save, and delete custom staff under `my_workspace/my_custom_staff/`.
- Save writes `agent.md` and `flow_rule.json`; `flow_rule.json` is JSON-validated.
- Delete is constrained to a validated child directory under `my_workspace/my_custom_staff/`.

New endpoints:

```text
GET  /api/staff
GET  /api/staff-detail?name=...
POST /api/save-staff
POST /api/delete-staff
```

Verification:

```powershell
python -m py_compile my_workspace/run_flow.py my_workspace/web_app.py my_workspace/my_codex_core/*.py
```

Passed with explicit PowerShell file enumeration.

```powershell
python my_workspace/run_flow.py --provider offline --workflow workflow_AI员工工作流平台设计 --input "我要做中小企业AI员工工作流平台，以my_custom_staff里的自定义员工为核心，能管理数字员工、运行工作流、查看任务输出，先自用跑通再销售。"
```

Result: workflow completed offline with 5 steps and wrote output under:

```text
my_workspace/my_task_output/task_20260613_123206_workflow_AI员工工作流平台设计/
```

The web management UI was restarted on `http://127.0.0.1:8765` and verified with:

```text
/api/config contains AI员工工作流平台设计 and 19_AI员工平台增长验证师
/api/staff contains 16_AI员工平台产品经理
home page contains 数字员工管理 and saveStaffBtn
```

## Management UI Product Redesign

The web management UI was simplified around three primary workspaces instead of one long stacked page:

```text
运行工作流
数字员工
任务输出
```

Changes in `my_workspace/web_app.py`:

- Added top navigation buttons using `data-view-target`.
- Added view containers using `data-view`.
- `运行工作流` is the default workspace.
- `数字员工` contains the staff manager only.
- `任务输出` shows the task sidebar and output viewer; the sidebar is hidden in other workspaces.
- Completed workflow runs automatically switch to `任务输出` and open the generated task.
- Non-output workspaces use a single-column full-width layout.

Verification:

```text
Local page returned HTTP 200.
Page contains data-view-target="run", data-view-target="staff", data-view-target="output", taskSidebar, and data-view="staff" hidden.
```

[2026-06-12 20:04:03 +08:00] command: git push origin main failed twice with GitHub port 443 connection timeout; local commit 8b56043 remains ahead of origin/main by 1.


[2026-06-12 20:07:33 +08:00] command: restarted my_workspace web management UI on http://127.0.0.1:8765 and verified HTTP 200 with reference image controls present.


[2026-06-12 20:10:02 +08:00] command: added local thumbnail previews for selected reference images in the web UI; restarted http://127.0.0.1:8765 and verified page includes reference-preview and URL.createObjectURL.


[2026-06-12 20:28:58 +08:00] command: added 06_分镜生图设计师, moved original video-generation staff to 07_视频生成执行员, updated short-video and outsourcing workflows to 7 steps, verified JSON/Python syntax, offline short-video run with 7 steps, and web /api/config staff count 7.


[2026-06-12 20:35:21 +08:00] command: added web UI image-generation configuration for 06_分镜生图设计师, including tool/model/size/count/style/quality/negative prompt/consistency/API presence fields; verified HTTP page markers and /api/run wrote 生图配置 without writing test secrets.


[2026-06-12 20:44:00 +08:00] command: added web workflow progress display; /api/run now starts a background job and /api/run-status polls status. Verified HTTP page has progressBox and offline workflow completed with 7/7 steps.


[2026-06-12 21:55:00 +08:00] command: added optional web UI task naming; task_title is passed to WorkflowEngine/TaskStorage, stored in run_summary.json, shown in progress and task lists, and used in task output directory suffix.


[2026-06-12 22:03:00 +08:00] command: optimized web UI layout by moving model API, memory, image, video, and reference image settings into compact collapsible sections; verified HTTP page markers and offline API run completed.


[2026-06-12 22:43:25 +08:00] command: added full-auto production framework package mode via production_pipeline.py and web UI controls. Verified package_only run created production_manifest.json, image_prompts/, video_prompts/, subtitles.srt, audio/voiceover.txt, and edit_checklist.md without storing secrets.

## Offline Deploy Agent Foundation

[2026-06-13 16:58:08 +08:00] command: added a conservative execution-action foundation for offline agents.

New files/directories:

```text
my_workspace/my_codex_core/action_executor.py
my_workspace/my_action_workspace/.gitignore
my_workspace/my_action_logs/.gitignore
my_workspace/my_knowledge_base/.gitignore
my_workspace/my_local_models/local_model_presets.json
my_workspace/my_deploy/OFFLINE_DEPLOY.md
```

Workflow engine changes:

- `WorkflowEngine` now creates an `ActionExecutor` rooted at `my_workspace/my_action_workspace`.
- Agent step outputs are scanned for JSON action blocks.
- Supported actions are currently limited to `mkdir`, `create_file`, and `write_json`.
- All action paths must be relative and are constrained under `my_action_workspace`.
- When actions run, their results are written to the task `action_log.json` and included in the step record as `action_results`.
- The per-step prompt now tells agents how to provide optional action JSON blocks.

Management UI changes:

- `模型接口配置` now includes local model presets and local model name selection.
- Local presets are loaded from `my_workspace/my_local_models/local_model_presets.json`.
- Current presets cover Ollama, LM Studio, vLLM, and Xinference using OpenAI-compatible Base URLs.
- Selecting a local preset switches provider to `openai`, fills the preset Base URL and `local` API Key, and puts the selected local model into the custom model field.
- Added `测试当前模型接口`, backed by `POST /api/test-model`, which calls the configured OpenAI-compatible `/chat/completions` endpoint.
- `记忆与继承` now includes local knowledge controls.
- Added `GET /api/knowledge` and `POST /api/upload-knowledge`.
- Allowed knowledge file types are `.md`, `.txt`, `.json`, and `.csv`; upload max size is 5 MB; files must decode as UTF-8.
- When `use_knowledge` is enabled, workflow input appends up to 20,000 characters from `my_knowledge_base`.

Documentation changes:

- Updated `my_workspace/README.md` with local model, knowledge base, action execution, and offline deployment notes.
- Added `my_workspace/my_deploy/OFFLINE_DEPLOY.md` for offline startup order and action boundaries.

Verification run:

```powershell
python - <syntax compile script>
python - <json parse script>
python - <action executor smoke script>
python my_workspace/run_flow.py --provider offline --workflow workflow_软件市场机会分析 --input "测试离线部署基础框架：请输出一个可离线使用的AI员工平台验证方案。"
```

Results:

- Python syntax check passed for `my_workspace/run_flow.py`, `my_workspace/web_app.py`, and every Python file under `my_workspace/my_codex_core`.
- JSON parsing passed for all staff/workflow JSON files and `my_local_models/local_model_presets.json`.
- Action executor smoke test created `smoke/hello.txt` and `smoke/data.json` under `my_action_workspace`.
- Offline workflow run completed with 1 step and wrote output under `my_workspace/my_task_output/task_20260613_165808_workflow_软件市场机会分析/`.
- Management UI restarted on `http://127.0.0.1:8765` and returned HTTP 200.
- Home page markers verified: `localModelPreset`, `localModelName`, `testModelBtn`, `useKnowledge`, `uploadKnowledgeBtn`, and `knowledgeList`.
- `/api/config` returned 4 local model presets.
- `/api/knowledge` returned HTTP 200.
- `/api/upload-knowledge` was smoke-tested with a small UTF-8 text file; the uploaded test file was listed, then removed.

Important limitation:

- This is a safe foundation, not a full autonomous operating layer. It does not run shell commands, delete files, call paid media APIs, or edit arbitrary project paths. Those require an approval layer, action queue, permission model, and audit log before enabling.

## One-Stop Local Startup

[2026-06-13 17:25:00 +08:00] command: added a one-stop local startup path for packaging the project as a local service.

New startup files:

```text
start_local.ps1
start_local.bat
```

Behavior:

- `start_local.ps1` finds `ollama` from PATH, or `runtime/ollama/ollama.exe` for a future bundled package.
- It starts `ollama serve` when `http://127.0.0.1:11434/v1/models` is not reachable.
- It checks the default model `qwen3:8b-q4_K_M`; if missing and `-SkipModelPull` is not provided, it runs `ollama pull qwen3:8b-q4_K_M`.
- It sets `OPENAI_API_KEY=local`, `OPENAI_BASE_URL=http://127.0.0.1:11434/v1`, and `OPENAI_MODEL=<model>` for the launched web app process.
- It starts `python my_workspace/web_app.py --port 8765`.
- It opens `http://127.0.0.1:8765` unless `-NoBrowser` is passed.
- `start_local.bat` is a double-click wrapper around the PowerShell script using `ExecutionPolicy Bypass`.

Management UI changes:

- Added top navigation item `系统状态`.
- Added `GET /api/system-health`.
- The system status page checks Python runtime, workspace path, task output directory write access, knowledge base write access, action workspace write access, Ollama command presence, and Ollama OpenAI-compatible model endpoint.
- Added a first-start guide covering one-click startup, selecting local model, testing model connection, uploading knowledge base files, and running a sample workflow.

Documentation:

- Updated `my_workspace/README.md` with one-stop startup commands and system status notes.
- Updated `my_workspace/my_deploy/OFFLINE_DEPLOY.md` with the light one-stop startup flow and manual fallback flow.

Validation run:

- PowerShell parser check for `start_local.ps1` passed using default PowerShell file reading after converting script messages to ASCII.
- Python syntax check passed for `my_workspace/run_flow.py`, `my_workspace/web_app.py`, and every Python file under `my_workspace/my_codex_core`.
- Management UI restarted on `http://127.0.0.1:8765` and returned HTTP 200.
- Home page markers verified: `data-view-target="system"`, `refreshHealthBtn`, `healthGrid`, `首次启动向导`, and `/api/system-health`.
- `/api/system-health` returned 7 checks: Python runtime, workspace path, task output path, knowledge base path, action workspace path, Ollama command, and Ollama model service.
- Superseded by the later Ollama setup section: after installing Ollama and migrating the model, Python, local directories, Ollama command, and Ollama model service all return `ok`.

Packaging note:

- Current implementation is a lightweight local package path. It does not embed a model file in Git. A full offline installer can later place `ollama.exe` under `runtime/ollama/` and pre-import model data outside Git because model files are too large for normal source control.

## Ollama Runtime And Qwen3 Local Model

[2026-06-13 19:10:00 +08:00] command: installed Ollama with winget and pulled the requested quantized local model.

Installed runtime:

```text
C:\Users\Administrator\AppData\Local\Programs\Ollama\ollama.exe
```

Requested model:

```text
qwen3:8b-q4_K_M
```

Ollama model list after pull:

```text
NAME               ID              SIZE      MODIFIED
qwen3:8b-q4_K_M    500a1f067a9f    5.2 GB    Less than a second ago
```

Important storage change:

- The model was first downloaded to the default Ollama model path:

```text
C:\Users\Administrator\.ollama\models
```

- The model directory was then copied into the project:

```text
I:\AI_Workspace\agency-agents-zh\runtime\models
```

- Source size before copy: about 5.22 GB.
- Project model directory after copy contains the `qwen3:8b-q4_K_M` manifests/blobs and is ignored by Git through `runtime/.gitignore`.
- The original C drive model directory was intentionally kept as a backup and was not deleted.

Startup script update:

- `start_local.ps1` default model changed to `qwen3:8b-q4_K_M`.
- `start_local.ps1` now sets `OLLAMA_MODELS` to `<repo>\runtime\models` before starting Ollama.
- This makes project startup use the project-local model directory instead of the default C drive model directory.
- `start_local.ps1` now also detects the winget install path `C:\Users\Administrator\AppData\Local\Programs\Ollama\ollama.exe` when `ollama` is not yet available in the current PATH.

Validation:

```powershell
$env:OLLAMA_MODELS='I:\AI_Workspace\agency-agents-zh\runtime\models'
Start-Process 'C:\Users\Administrator\AppData\Local\Programs\Ollama\ollama.exe' -ArgumentList 'serve'
ollama list
POST http://127.0.0.1:11434/v1/chat/completions model=qwen3:8b-q4_K_M
```

Results:

- `ollama list` using project `runtime\models` shows `qwen3:8b-q4_K_M`.
- OpenAI-compatible `/v1/chat/completions` call returned a response for `qwen3:8b-q4_K_M`.
- `powershell -NoProfile -ExecutionPolicy Bypass -File .\start_local.ps1 -SkipModelPull -NoBrowser` succeeded and printed the project model directory.
- `GET /api/system-health` returned 7 checks all `ok`, including the winget Ollama executable and the model service showing `qwen3:8b-q4_K_M`.
- The previous partial standalone zip download under `runtime/_cache` was removed.

Notes:

- `runtime/ollama/ollama.exe` is still not present because the standalone zip download was unreliable and was abandoned after winget installation succeeded.
- `install_ollama_runtime.ps1` exists to support future standalone runtime embedding, but the current working runtime is the winget-installed Ollama executable plus project-local model storage.

## Local Offline Mode Shortcut

[2026-06-13 19:20:00 +08:00] command: added one-click local offline model configuration in the web UI.

Management UI changes:

- Added `一键本地离线模式` button under `模型接口配置`.
- The button automatically sets:

```text
provider=openai
api_key=local
base_url=http://127.0.0.1:11434/v1
local_model_preset=ollama
model=custom
custom_model=qwen3:8b-q4_K_M
```

- Workflow runs now preflight-test the local Ollama model when provider is `openai` and Base URL is `http://127.0.0.1:11434/v1`.
- If local model preflight fails, the UI stops before creating a workflow run and shows the model connection error.
- System health now adds a separate `推荐本地模型` check for `qwen3:8b-q4_K_M`.

Documentation:

- Updated `my_workspace/README.md` to mention the one-click local offline mode and preflight behavior.

Validation:

- Python syntax check passed for `my_workspace/run_flow.py`, `my_workspace/web_app.py`, and every Python file under `my_workspace/my_codex_core`.
- JSON check passed for `my_workspace/my_local_models/local_model_presets.json`.
- PowerShell parser check passed for `start_local.ps1`.
- Management UI restarted on `http://127.0.0.1:8765` and returned HTTP 200.
- Home page contains `localOfflineBtn`, `一键本地离线模式`, `qwen3:8b-q4_K_M`, and `ensureLocalModelReady`.
- `GET /api/system-health` returned 8 checks, all `ok`, including `推荐本地模型: qwen3:8b-q4_K_M 已可用`.
- `POST /api/test-model` with `base_url=http://127.0.0.1:11434/v1`, `api_key=local`, and `model=qwen3:8b-q4_K_M` returned `ok=true`.
