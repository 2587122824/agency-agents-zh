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

## Local Model Timeout Handling

[2026-06-13 19:35:00 +08:00] command: increased local model timeout handling after Ollama runs timed out around step 5 of the short-video workflow.

Root cause:

- `my_workspace/my_codex_core/codex_api.py` used a fixed 120-second `urllib.request.urlopen` timeout.
- Local `qwen3:8b-q4_K_M` can exceed 120 seconds once workflow context grows after several steps, especially around compliance/review steps.

Changes:

- `CodexAPI` now accepts a `timeout` argument.
- `MY_WORKFLOW_TIMEOUT` environment variable can override the timeout.
- If Base URL is local Ollama (`127.0.0.1:11434` or `localhost:11434`), default timeout is now 900 seconds.
- Cloud/default timeout remains 120 seconds.
- `WorkflowEngine` accepts and passes timeout to `CodexAPI`.
- CLI `my_workspace/run_flow.py` adds `--timeout`.
- Web UI `模型接口配置` adds `模型超时` select: 120, 300, 600, 900, 1800 seconds.
- `一键本地离线模式` sets model timeout to 900 seconds.
- `/api/run` accepts `timeout` and passes it into the background workflow job.
- If a model step fails, the engine now writes failure details to that step's `output.md` and `error.json` before re-raising.
- If a web workflow fails after a task directory exists, the UI now refreshes task output and opens the failed task automatically.

Expected behavior:

- Local Ollama workflows should no longer fail at 120 seconds by default.
- If a later step still exceeds 900 seconds, the user can set `模型超时=1800 秒`.
- Failed runs now leave readable output/error files instead of appearing to produce nothing.

## Workflow Editor

[2026-06-13 20:05:00 +08:00] command: added a visual workflow editor to the management UI after the user confirmed the local model run had succeeded and asked for the next editor step.

Management UI changes:

- Added top navigation item `工作流`.
- Added `工作流编辑器` page for `my_workspace/my_workflows`.
- The editor can list existing workflow JSON files, select a workflow, edit file name, display name, description, and ordered steps.
- Each step can choose an existing custom staff folder from a dropdown, edit task text, edit expected output, move up/down, or delete.
- Editing an existing workflow preserves advanced top-level fields such as `workflow_id`, `input_required`, `final_outputs`, and `acceptance_criteria`.
- Added buttons: `刷新工作流`, `新建工作流`, `新增步骤`, `保存工作流`, `删除工作流`.
- Saving a workflow refreshes `/api/config`, so the `运行工作流` dropdown updates without restarting the server.

Backend API changes:

- Added `GET /api/workflows`.
- Added `GET /api/workflow-detail?name=...`.
- Added `POST /api/save-workflow`.
- Added `POST /api/delete-workflow`.
- Added safe workflow path handling so saves/deletes are limited to `my_workspace/my_workflows/*.json`.
- Save validation requires a workflow name, at least one step, an existing staff agent, non-empty task text, and non-empty output text.

Documentation:

- Updated `my_workspace/README.md` to mention the `工作流` page and its create/edit/delete/reorder capability.

Validation:

- `python -m py_compile my_workspace/web_app.py` passed.
- Extracted the embedded `<script>` from `my_workspace/web_app.py` and `node --check runtime/_tmp_web_app_script.js` passed.
- Restarted the management UI on `http://127.0.0.1:8765`.
- Home page markers verified: `data-view-target="workflow"`, `workflowEditorStatus`, `saveWorkflowBtn`, and `addWorkflowStepBtn`.
- `GET /api/workflows` returned all current workflows and custom staff folders.
- `GET /api/workflow-detail?name=workflow_短视频全流程` returned the full short-video workflow.
- Smoke-tested workflow create/read/delete through the API using temporary file `workflow_codex_smoke_editor.json`; it was saved, read back, verified to preserve extra top-level fields, and deleted successfully.

## Output Editor, Step Rerun, Export Package, And Product Templates

[2026-06-13 20:45:00 +08:00] command: implemented the four requested next-step capabilities after the user asked to add all four.

Management UI changes:

- Added `产品类型` selector in the run form.
- Product templates currently cover `短视频`, `小红书图文`, `Unity 3D Steam 游戏`, `软件市场分析`, and `AI 员工平台`.
- Changing product type auto-fills the recommended workflow, task title, aspect ratio/duration defaults, and production mode defaults.
- `填入示例` now uses the selected product template; `游戏示例` switches to the game template.
- Replaced the output viewer's read-only `pre` with an editable textarea.
- Added task-output action buttons: `保存当前文件`, `重建最终汇总`, `重跑当前步骤`, and `导出产品包`.

Backend/API changes:

- Added `POST /api/save-file` to save editable text files under a task directory.
- Added `POST /api/rebuild-final-output` to rebuild `final_output.md` from current step outputs.
- Added `POST /api/rerun-step` to rerun one step using the original `workflow.json`, original `input.md`, existing prior step outputs, and the currently selected model provider.
- Added `POST /api/export-task` to generate an `export_package/` folder under the task output.
- Export templates create practical delivery files:
  - short video: `视频制作包.md`, `字幕.srt`, `镜头清单.csv`, `生图提示词.json`, `视频提示词.json`
  - xiaohongshu: `小红书文案.md`, `标题列表.txt`, `封面文案.txt`, `发布检查清单.md`
  - game: `GDD.md`, `Unity开发任务清单.md`, `Steam商店页文案.md`, `测试发行清单.md`
  - software market: `软件机会排行榜.md`, `MVP验证计划.md`, `商业化假设.md`
  - agent platform: `产品需求文档.md`, `员工管理方案.md`, `工作流架构.md`, `技术落地清单.md`
- Added safe task-file path handling so text editing stays inside `my_workspace/my_task_output/<task>/`.

Workflow engine changes:

- Added `WorkflowEngine.rerun_step(task_dir, step_no)`.
- Rerun backs up the previous `output.md` to `output_backup_YYYYMMDD_HHMMSS.md`.
- Rerun writes fresh `system.md`, `prompt.md`, `metadata.json`, `output.md`, `rerun_result.json`, rebuilds `final_output.md`, and appends `rerun_history` to `run_summary.json`.
- Offline rerun works without calling a model; OpenAI-compatible rerun uses the selected provider/model/API config from the UI.

Documentation:

- Updated `my_workspace/README.md` with output editing, one-step rerun, export package, and product template usage notes.

Validation:

- `ast.parse` syntax check passed for `my_workspace/web_app.py` and `my_workspace/my_codex_core/workflow_engine.py`.
- Extracted the embedded `<script>` from `my_workspace/web_app.py`; `node --check` passed.
- Started the current management UI on `http://127.0.0.1:8767` for verification because an older process was still responding on `8765`.
- Home page markers verified: `productTemplate`, `saveFileBtn`, `rebuildFinalBtn`, `rerunStepBtn`, and `exportTaskBtn`.
- Node-based smoke test created temporary task `task_codex_editor_smoke`, then verified:
  - `POST /api/save-file` returned `final_output.md`
  - `POST /api/rebuild-final-output` returned `final_output.md`
  - `POST /api/rerun-step` in `offline` mode returned `step_01_agent/output.md`
  - `POST /api/export-task` returned `export_package` with 9 files and `export_package/视频制作包.md`
  - `POST /api/delete-task` removed the temporary task successfully
- Replaced the old process on `http://127.0.0.1:8765`; page markers `saveFileBtn` and `productTemplate` returned `True` on the default management UI port.

## Bundled Ollama Startup And Staff Manager Layout

[2026-06-13 21:05:00 +08:00] command: copied the installed Ollama runtime into the project and optimized the staff management layout after the user asked why system status still showed C drive and then asked product design to improve the page layout.

Runtime changes:

- Copied installed Ollama runtime from `C:\Users\Administrator\AppData\Local\Programs\Ollama` into `runtime/ollama/`.
- Project runtime now includes `runtime/ollama/ollama.exe`, `runtime/ollama/ollama app.exe`, `runtime/ollama/app.ico`, and `runtime/ollama/lib/` on disk.
- The copied runtime is ignored by Git through `runtime/.gitignore`; binaries and large dependency files are intentionally not committed.
- `start_local.ps1` now prefers `runtime/ollama/ollama.exe` before PATH or the system install path.
- `start_local.ps1` now stops existing listeners on Ollama port `11434` and web port `8765` by default before starting services. This prevents old processes from keeping stale `OLLAMA_MODELS` or old web UI code.
- Added `-KeepExistingOllama` and `-KeepExistingWeb` switches for cases where an existing listener should be preserved.
- `start_local.ps1 -SkipModelPull -NoBrowser` printed:
  - `Ollama: I:\AI_Workspace\agency-agents-zh\runtime\ollama\ollama.exe`
  - `Models: I:\AI_Workspace\agency-agents-zh\runtime\models`

Management UI changes:

- System health now prefers showing project `runtime/ollama/ollama.exe` when it exists, before PATH or system install path.
- Verified `/api/system-health` shows `Ollama 命令 = I:\Ai_WorkSpace\agency-agents-zh\runtime\ollama\ollama.exe` after restarting the current web process.
- `数字员工管理` page layout was redesigned as a two-column management workspace:
  - top toolbar separates title/status from action buttons
  - left sidebar contains `员工搜索` and a compact staff list
  - right side is a bordered editor panel for `agent.md` and `flow_rule.json`
  - staff cards now use tighter spacing, single-line title/meta, and a small role pill
- Added `staffFilter` input and client-side filtering by staff name, display name, or role.

Validation:

- PowerShell parser check for `start_local.ps1` passed.
- `ast.parse` check for `my_workspace/web_app.py` passed.
- Extracted web script from `my_workspace/web_app.py`; `node --check` passed.
- `powershell -NoProfile -ExecutionPolicy Bypass -File .\start_local.ps1 -SkipModelPull -NoBrowser` succeeded and started project Ollama plus web app.
- Home page markers verified: `staffFilter`, `manager-toolbar`, `staff-sidebar`.
- `/api/system-health` verified project-local Ollama command path and `qwen3:8b-q4_K_M` availability.

## Web Startup Reliability Fix

[2026-06-13 21:30:00 +08:00] command: investigated the user's `127.0.0.1:8765` `ERR_EMPTY_RESPONSE` report and made Web startup more diagnosable.

Findings:

- Port `8765` could be listening while the browser still showed `ERR_EMPTY_RESPONSE`.
- Raw socket and PowerShell checks later confirmed `/`, `/api/config`, `/api/system-health`, `/api/staff`, `/api/tasks`, and `/api/workflows` can return HTTP 200 after restarting the Web process.
- `http://127.0.0.1:8765/` returned title `自定义工作流管理台`; `/api/system-health` returned 8 checks.

Changes:

- Updated `start_local.ps1` to launch the Web app directly with Python from the project root.
- Web stdout/stderr are redirected to:
  - `runtime/logs/web_app.out.log`
  - `runtime/logs/web_app.err.log`
- `start_local.ps1` now performs a second post-start check so a short-lived Web process is not mistaken for a ready service.
- If the Web app fails to become ready or exits immediately after startup, `start_local.ps1` reports the exact log paths to inspect.
- Fixed the digital staff manager layout:
  - staff manager grid now uses `minmax(300px, 360px) minmax(0, 1fr)`
  - right-side editor is constrained with `min-width: 0`, `max-width: 100%`, and internal textarea scrolling
  - staff cards now have enough height for name, folder, and role without clipping

Validation:

- `python -m py_compile my_workspace/web_app.py` passed.
- PowerShell parser checks for `start_local.ps1` passed.
- After stopping the old 8765 listener, `powershell -NoProfile -ExecutionPolicy Bypass -File .\start_local.ps1 -SkipModelPull -NoBrowser` started Ollama and Web.
- `http://127.0.0.1:8765/` returned HTTP 200 and contained the updated staff-manager CSS.
- `/api/system-health` returned HTTP 200 with 8 checks.
- A delayed re-check after 6 seconds still returned HTTP 200.

## Task Output Overview Upgrade

[2026-06-13 21:55:00 +08:00] command: added a structured task-output overview after the user asked to add the next-step output detail page improvements.

Management UI changes:

- The `任务输出` page now has an output dashboard above the file editor.
- Added four summary cards for:
  - task title
  - workflow
  - final output status
  - product package status
- Added `步骤输出` list:
  - detects files matching `step_*/output.md`
  - shows each step as a clickable entry
  - clicking a step opens the corresponding `output.md`
- Added `产品包文件` list:
  - detects files under `export_package/`
  - prioritizes common deliverables such as `README.md`, `final_output.md`, `视频制作包.md`, `小红书文案.md`, `GDD.md`, `产品需求文档.md`, and `manifest.json`
  - clicking a package file opens it in the editor
- The current file is highlighted in both normal file tabs and the structured output lists.
- Added responsive layout rules so summary cards and output sections collapse on narrow screens.

Backend robustness:

- `run_summary.json` is now read with `utf-8-sig` in task listing, task detail, and export handling.
- This prevents PowerShell-created UTF-8 BOM JSON files from breaking the task output page.
- Task detail now tolerates malformed `run_summary.json` by falling back to an empty summary.

Validation:

- `python -m py_compile my_workspace/web_app.py` passed.
- Extracted embedded frontend script and parsed it with Node via stdin; syntax passed.
- Restarted local services with `powershell -NoProfile -ExecutionPolicy Bypass -File .\start_local.ps1 -SkipModelPull -NoBrowser`.
- Home page returned HTTP 200 and contained `outputDashboard`, `outputSummaryGrid`, and `packageOutputList`.
- Temporary smoke task `task_codex_output_overview_smoke` verified:
  - `/api/task` returned 5 files before export
  - `/api/export-task` generated 9 files
  - `/api/task` detected 2 step output files and 9 package files after export
  - temporary task directory was deleted after validation

## Run Page And Output List Layout Fix

[2026-06-13 22:15:00 +08:00] command: fixed task-output list clipping and optimized the `运行工作流` tab layout after the user reported display issues.

Management UI changes:

- Fixed `任务输出` structured lists:
  - output buttons now have a taller two-line layout
  - file title and file path use separate classes
  - long names are ellipsized instead of being vertically clipped
- Optimized `运行工作流` tab:
  - added a lightweight `任务基础信息` section
  - product type, workflow, and task name are grouped together
  - execution mode, model, and model timeout are grouped together
  - original request is now in a dedicated `原始需求` section with clearer helper text
  - action buttons are styled as a bottom action bar on desktop and fall back to normal flow on narrow screens
- Moved `模型超时` out of the collapsible model config and confirmed it appears only once.
- Run-page sections use divider lines rather than nested cards to avoid visual clutter.

Validation:

- `python -m py_compile my_workspace/web_app.py` passed.
- Extracted embedded frontend script and parsed it with Node via stdin; syntax passed.
- Confirmed `id="modelTimeout"` appears exactly once.
- Restarted local services with `powershell -NoProfile -ExecutionPolicy Bypass -File .\start_local.ps1 -SkipModelPull -NoBrowser`.
- Home page returned HTTP 200 and contained `run-form`, `run-primary-grid`, `output-link-title`, and `output-link-subtitle`.

## Browser Action Executor Fix

[2026-06-14 00:00:00 +08:00] command: fixed the missing browser-operation capability in the local action executor after the user reported that agents could not operate the computer/browser.

Action executor changes:

- Extended `my_workspace/my_codex_core/action_executor.py`.
- Existing safe file actions remain:
  - `mkdir`
  - `create_file`
  - `write_json`
- Added controlled browser/workspace actions:
  - `open_url`: opens an http/https URL in the default browser.
  - `fetch_url`: fetches an http/https URL, converts HTML to readable text when possible, and saves it under `my_action_workspace`.
  - `open_workspace_path`: opens an existing file or folder under `my_action_workspace`.
- URL safety:
  - only `http` and `https` schemes are allowed.
  - URLs must include a host.
  - `file://` and other schemes are blocked.
- Filesystem safety:
  - all written or opened local paths still go through `_safe_path`.
  - absolute paths and paths escaping `my_action_workspace` remain blocked.
- Fetch safety:
  - request timeout is clamped to 3-60 seconds.
  - fetched content is capped at 1 MB.
  - no shell commands are introduced.

Workflow prompt changes:

- Updated `WorkflowEngine._build_step_prompt` so staff can request `open_url`, `fetch_url`, and `open_workspace_path` in JSON action blocks.
- Prompt explicitly states that browser actions only allow http/https and that file paths are constrained under `my_action_workspace`.

Documentation changes:

- Updated `my_workspace/README.md` action list and example JSON.
- Updated `my_workspace/my_deploy/OFFLINE_DEPLOY.md` action boundaries.
- Documentation still states unsupported capabilities: deleting files, shell execution, arbitrary system paths, code push, paid API calls, and mouse/keyboard control without an approval layer.

Validation:

- `python -m py_compile my_workspace/my_codex_core/action_executor.py my_workspace/my_codex_core/workflow_engine.py` passed.
- Smoke test started a temporary local HTTP server and executed actions:
  - `mkdir` returned `done`.
  - `fetch_url` returned `done` and saved readable text to `my_action_workspace`.
  - illegal `file://` fetch returned `error`.
  - `action_log.json` recorded all 3 attempted actions.

## RunningHub Cloud Image Adapter

[2026-06-14 +08:00] command: connected the image-generation production path to RunningHub cloud ComfyUI without storing user secrets in repo or task output.

New file:

```text
my_workspace/my_codex_core/cloud_image_adapter.py
```

Behavior:

- Supports `tool=runninghub` for image generation in `api_ready` production mode.
- Submits to the configured RunningHub workflow endpoint, reads `taskId`, polls `/query`, and downloads successful image results into:

```text
my_workspace/my_task_output/<task>/generated_images/
```

- Writes provider responses and manifest files:

```text
generated_images/runninghub_submit_response.json
generated_images/runninghub_query_response.json
generated_images/cloud_image_manifest.json
production_manifest.json
```

- RunningHub result URLs are temporary, so generated image files are downloaded locally immediately.
- API keys are used only in memory for the current request. They are not written to `production_manifest.json`, `cloud_image_manifest.json`, response files, README, or this handoff document.

Management UI changes:

- `生图配置` now includes `RunningHub Cloud ComfyUI`.
- Added RunningHub fields:
  - Base URL, default intended value: `https://www.runninghub.cn/openapi/v2`
  - Workflow endpoint, default intended value: `/run/workflow/2048294089858228226`
  - Instance type: `default` or `plus`
  - `nodeInfoList JSON`
  - poll timeout
- Selecting RunningHub auto-fills the default Base URL, endpoint, and empty `nodeInfoList` when those fields are blank.
- Existing browser `localStorage` setting persistence now includes the new RunningHub image fields.

Production pipeline changes:

- `run_auto_production()` now calls the image adapter only when:
  - production mode is `api_ready`
  - image tool is not `prompt_only`
  - image API key, Base URL, and workflow endpoint are present
- Missing config results in `adapter_status=skipped` rather than an accidental API call.
- Failed API calls write `generated_images/cloud_image_error.json` and mark the image adapter failed in `production_manifest.json`.

Verification:

```powershell
python -m py_compile my_workspace/web_app.py my_workspace/my_codex_core/production_pipeline.py my_workspace/my_codex_core/cloud_image_adapter.py
node --check runtime/_tmp_web_app_script.js
```

Passed.

Local fake RunningHub smoke tests passed:

- `CloudImageAdapter` submitted a fake workflow, queried fake `/query`, and downloaded `runninghub_01.png`.
- `run_auto_production(..., mode=api_ready, tool=runninghub)` downloaded `generated_images/runninghub_01.png`, set `production_manifest.status=image_generated`, and confirmed the dummy API key was not written into `production_manifest.json`.

Git status after implementation should include:

```text
M docs/HANDOFF_CONTEXT.md
M my_workspace/README.md
M my_workspace/web_app.py
M my_workspace/my_codex_core/production_pipeline.py
?? my_workspace/my_codex_core/cloud_image_adapter.py
```

Branch may still be ahead of origin because the previous `Browser Action Executor Fix` commit was committed locally but GitHub push failed with port 443 connectivity errors.

## RunningHub Cloud Video Adapter

[2026-06-14 +08:00] command: connected the video-generation production path to the user-provided RunningHub AI App endpoint.

User-provided video endpoint:

```text
/run/ai-app/2066043648160133122
```

New file:

```text
my_workspace/my_codex_core/cloud_video_adapter.py
```

Behavior:

- Supports `tool=runninghub` for video generation in `api_ready` production mode.
- Submits to the configured RunningHub AI App endpoint, reads `taskId` from either top-level `taskId` or nested `data.taskId`, polls `/query`, and downloads video results into:

```text
my_workspace/my_task_output/<task>/video_clips/
```

- Supports downloaded result types:

```text
mp4, mov, webm, m4v
```

- Writes provider response and manifest files:

```text
video_clips/runninghub_video_submit_response.json
video_clips/runninghub_video_query_response.json
video_clips/cloud_video_manifest.json
production_manifest.json
```

- API keys are used only in memory for the current request. They are not written to `production_manifest.json`, `cloud_video_manifest.json`, response files, README, or this handoff document.

Management UI changes:

- `视频生成配置` now includes `RunningHub AI App`.
- Added video RunningHub fields:
  - Base URL, default intended value: `https://www.runninghub.cn/openapi/v2`
  - Video endpoint, default intended value: `/run/ai-app/2066043648160133122`
  - `RunningHub Video nodeInfoList JSON`
  - poll timeout
- Selecting RunningHub auto-fills the default Base URL, endpoint, and empty `nodeInfoList` when those fields are blank.
- Browser `localStorage` setting persistence now includes the new RunningHub video fields.

Production pipeline changes:

- `run_auto_production()` now calls the video adapter when:
  - production mode is `api_ready`
  - video tool is not `prompt_only`
  - video API key, Base URL, and workflow endpoint are present
- Missing config results in `adapter_status=skipped` instead of an accidental API call.
- Failed video API calls write `video_clips/cloud_video_error.json` and mark the video adapter failed in `production_manifest.json`.

Verification:

```powershell
python -m py_compile my_workspace/web_app.py my_workspace/my_codex_core/production_pipeline.py my_workspace/my_codex_core/cloud_image_adapter.py my_workspace/my_codex_core/cloud_video_adapter.py
node --check runtime/_tmp_web_app_script.js
```

Passed.

Local fake RunningHub video smoke tests passed:

- `CloudVideoAdapter` submitted a fake AI App task, read nested `data.taskId`, queried fake `/query`, and downloaded `runninghub_video_01.mp4`.
- `run_auto_production(..., mode=api_ready, video tool=runninghub)` downloaded `video_clips/runninghub_video_01.mp4`, set `production_manifest.status=video_generated`, and confirmed the dummy API key was not written into `production_manifest.json`.

## Media Parameter UI Expansion

[2026-06-14 +08:00] command: expanded the management UI image/video configuration with normal and advanced generation parameters.

Image configuration now includes:

```text
seed
guidance_scale
steps
denoise_strength
sampler
control
```

Where `control` is intended for LoRA, ControlNet, IP-Adapter, and face-reference notes.

Video configuration now includes:

```text
negative_prompt
seed
fps
motion_strength
camera_motion
resolution
guidance_scale
frames
image_strength
camera_path
audio_notes
advanced_params
```

The new fields are wired through:

- DOM element lookup in `my_workspace/web_app.py`
- browser `localStorage` save/restore
- settings persistence binding
- `/api/run` `image_config` and `video_config`
- `_append_image_config()` and `_append_video_config()` so staff 06/07 receive the parameters as context
- `production_manifest.json` via `production_pipeline.py`

The UI labels for the newly added fields use English placeholders to avoid the current Windows PowerShell/encoding path corrupting newly inserted Chinese text into `?` characters.

Validation:

```powershell
python -m py_compile my_workspace/web_app.py my_workspace/my_codex_core/production_pipeline.py my_workspace/my_codex_core/cloud_image_adapter.py my_workspace/my_codex_core/cloud_video_adapter.py
node --check runtime/_tmp_web_app_script.js
```

Passed.

## Media Parameter UI Simplification

[2026-06-14 +08:00] command: simplified the management UI media generation controls after the user said there were too many parameters and visible labels should be Chinese.

Visible image controls now prioritize:

```text
工具、模型、尺寸/画幅、每镜头图片数、风格、质量、负面提示词、一致性重点、API Key、Base URL
```

Hidden image controls remain in the DOM so existing JS, localStorage, and RunningHub adapter defaults continue to work:

```text
imageWorkflowEndpoint
imageInstanceType
imageNodeInfoList
imagePollTimeout
imageSeed
imageGuidance
imageSteps
imageDenoise
imageSampler
imageControl
```

Visible video controls now prioritize:

```text
工具、模型、画幅、目标时长、风格、负面提示词、运动强度、镜头运动、API Key、Base URL
```

Hidden video controls remain in the DOM for compatibility:

```text
videoSeed
videoFps
videoResolution
videoGuidance
videoFrames
videoImageStrength
videoCameraPath
videoAudioNotes
videoAdvancedParams
videoWorkflowEndpoint
videoNodeInfoList
videoPollTimeout
```

Visible RunningHub option labels were changed to Chinese:

```text
RunningHub 云端 ComfyUI
RunningHub 视频应用
```

Visible video labels were changed to Chinese for:

```text
视频负面提示词
运动强度
镜头运动
```

Validation:

```powershell
python -m py_compile my_workspace/web_app.py my_workspace/my_codex_core/production_pipeline.py
node --check runtime/_tmp_web_app_script.js
.\start_local.ps1 -SkipModelPull -NoBrowser
```

Local page returned HTTP 200 and contained the expected Chinese visible labels while hidden advanced fields remained present for compatibility.

## Media Prompt UI Refinement

[2026-06-14 +08:00] command: further simplified the image/video generation controls after the user said video parameters that can be merged into prompts should be optimized and remaining English labels should be translated.

Management UI changes:

- `生图平台 API Key` was renamed to `生图平台密钥`.
- `生图平台 Base URL` was renamed to `生图平台接口地址`.
- Restored visible image fields for:
  - `负面提示词`
  - `一致性重点`
- `视频平台 API Key` was renamed to `视频平台密钥`.
- `视频平台 Base URL` was renamed to `视频平台接口地址`.
- Added `视频画面与运动要求` as the main natural-language field for video generation.
- Moved separate `运动强度` and `镜头运动` controls into hidden compatibility defaults.

Behavior changes:

- `video_config.prompt_notes` now stores the natural-language video movement/shot requirement.
- Workflow prompts sent to staff now include only user-facing video essentials instead of listing hidden technical parameters such as FPS, frames, guidance, resolution, image strength, endpoint, and poll timeout.
- Image workflow prompts were also reduced to user-facing essentials; hidden advanced fields remain in DOM and request config for adapter compatibility.
- `production_manifest.json` now includes `video_generation.prompt_notes`.

Documentation:

- Updated `my_workspace/README.md` to document the simplified Chinese parameter set and explain that `画面与运动要求` replaces many separate video technical controls for normal users.

## Voice Subtitle And ComfyUI Production Staff

[2026-06-14 +08:00] command: added separate voice/subtitle and ComfyUI production orchestration staff after the user noted ComfyUI can also run voice and subtitle nodes but they cost more compute.

New custom staff:

```text
my_workspace/my_custom_staff/20_语音字幕包装师/
my_workspace/my_custom_staff/21_ComfyUI成片编排师/
```

Staff responsibility split:

- `07_视频生成执行员` now focuses on video visuals, shot prompts, reference images, first frames, and video clip execution.
- `20_语音字幕包装师` owns TTS copy, TTS parameters, subtitle segmentation, SRT drafts, BGM, sound effects, and mixing notes.
- `21_ComfyUI成片编排师` maps image/video/audio/subtitle outputs into ComfyUI or RunningHub execution parameters and gives low-cost fallback paths.

Workflow updates:

- `workflow_短视频全流程.json` now has 9 steps:
  - 06 storyboard image planning
  - 07 video visual generation package
  - 20 voice/subtitle package
  - 21 ComfyUI final production orchestration
- `workflow_开发外包.json` also now has 9 steps with the same 07/20/21 split.
- `workflow_小红书图文.json` was not changed because it is not a video production workflow.

Management UI updates:

- `全自动生成` mode now exposes clearer choices:
  - `只生成视频制作包`
  - `生成视频 + 语音字幕制作包`
  - `调用生图/生视频 API`
  - `ComfyUI 全自动成片（高算力预留）`
- Video config summary now states it feeds 06, 07, 20, and 21.
- Export package for short-video outputs now includes `语音字幕制作包.md`, `ComfyUI成片编排.md`, and `ComfyUI参数包.json`.

Production pipeline updates:

- `run_auto_production()` now extracts voiceover and SRT from `20_语音字幕包装师` instead of 07.
- It writes:
  - `audio/audio_subtitle_package.md`
  - `audio/voiceover.txt`
  - `subtitles.srt`
  - `comfyui/comfyui_plan.md`
  - `comfyui/comfyui_payload.json`
- `production_manifest.json` now references the audio package, ComfyUI plan, and ComfyUI payload.
- API adapters are marked `pending` only for `api_ready` mode; package and ComfyUI planning modes do not falsely indicate external API calls.

## Cloud ComfyUI Final Production Adapter

[2026-06-15 +08:00] command: added the ComfyUI final-production configuration and adapter framework after the user asked to add the next step.

New file:

```text
my_workspace/my_codex_core/cloud_comfyui_adapter.py
```

Behavior:

- Supports cloud ComfyUI / RunningHub final-production execution in `comfy_full` mode.
- Reads `comfyui/comfyui_payload.json` generated from `21_ComfyUI成片编排师`.
- Submits to the configured endpoint, polls `/query`, and downloads result files into:

```text
my_workspace/my_task_output/<task>/comfyui/
```

- Downloadable result extensions include:

```text
mp4, mov, webm, m4v, mp3, wav, aac, png, jpg, jpeg, webp
```

- Writes:

```text
comfyui/runninghub_comfyui_submit_response.json
comfyui/runninghub_comfyui_query_response.json
comfyui/cloud_comfyui_manifest.json
```

- API keys are used only in memory for the current request. They are not written to `production_manifest.json`, adapter manifests, response files, README, or this handoff document.

Management UI changes:

- `全自动生成` now has ComfyUI final-production fields:
  - 成片平台密钥
  - 成片平台接口地址
  - 成片工作流接口
  - ComfyUI 节点映射 JSON
  - 成片轮询超时
- Selecting `ComfyUI 全自动成片（高算力预留）` switches `合成工具` to `RunningHub / 云端 ComfyUI`.
- Selecting the cloud compose tool fills default Base URL `https://www.runninghub.cn/openapi/v2` and `[]` node mapping when blank.
- Browser `localStorage` persistence now includes the new ComfyUI final-production fields.

Production pipeline changes:

- `run_auto_production()` calls the ComfyUI adapter only when:
  - `mode == comfy_full`
  - compose tool is a cloud provider such as `runninghub`
  - ComfyUI API key, Base URL, and workflow endpoint are present
- Missing config returns `adapter_status=skipped` instead of making a request.
- `production_manifest.json` records only booleans for whether API key, Base URL, endpoint, and node mapping were provided; it does not store secrets or Base URLs.

Node mapping placeholders:

```text
{{payload}}
{{prompt}}
{{voice_text}}
{{subtitle_srt}}
```
