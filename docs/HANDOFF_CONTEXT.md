# Handoff Context

## Current State

- Repo: `I:\Ai_WorkSpace\agency-agents-zh`
- Origin: `https://github.com/2587122824/agency-agents-zh.git`
- Upstream: `https://github.com/jnMetaCode/agency-agents-zh.git`
- Custom work lives under `my_workspace/`; original upstream agent files are not modified.
- Current local branch may be ahead of GitHub if network push failed. Latest local commit at time of this handoff: `245d8cc Add long-form video workflow`.

Latest update on 2026-06-15:

- Added workflow breakpoint resume.
- Management UI task output page now has `继续任务`.
- `/api/resume-task` starts a background resume job and reuses `/api/run-status`.
- `WorkflowEngine.resume()` resumes from the first step with `error.json`, missing `output.md`, or empty output.
- Verified with Python compile, inline JS syntax check, offline resume smoke test, local HTTP `/api/resume-task`, and browser DOM check.

## Main Entrypoints

```powershell
# Management UI
python my_workspace/web_app.py

# Typical local startup, starts web UI and local model service
powershell -NoProfile -ExecutionPolicy Bypass -File .\start_local.ps1 -SkipModelPull -NoBrowser

# CLI workflow run
python my_workspace/run_flow.py --provider offline --workflow workflow_短视频全流程 --input "你的内容需求"
```

Default UI URL:

```text
http://127.0.0.1:8765
```

Local model setup currently targets Ollama-compatible OpenAI API:

```text
http://127.0.0.1:11434/v1
qwen3:8b-q4_K_M
```

## Workspace Layout

```text
my_workspace/
  web_app.py
  run_flow.py
  README.md
  my_codex_core/
  my_custom_staff/
  my_workflows/
  my_memory/
  my_knowledge_base/
  my_reference_images/
  my_voice_samples/
  my_task_output/
```

Generated/local asset directories are ignored except their `.gitignore` files:

```text
my_task_output/
my_reference_images/
my_voice_samples/
my_action_workspace/
```

Do not commit user API keys, uploaded images, voice samples, generated task outputs, or model files.

## Current Workflows

```text
workflow_短视频全流程.json          10 steps
workflow_长视频全流程.json          10 steps
workflow_开发外包.json              10 steps
workflow_小红书图文.json             4 steps
workflow_Unity3D游戏Steam上架.json   7 steps
workflow_软件市场机会分析.json        1 step
workflow_AI员工工作流平台设计.json     5 steps
```

Video workflow design:

- Editing/composition tool is the final output authority.
- AI image/video generation is only auxiliary素材片段.
- `21_ComfyUI成片编排师` folder remains for compatibility, but displayed role is now `ComfyUI素材编排师`.
- `22_剪辑成片执行师` is the final video step.
- Long videos use `23_长视频策划编导` for chapters, retention, and long-form素材规划.

## Current Staff

Self-media/video:

```text
01_需求拆解专员
02_短视频编导
03_口播脚本师
04_标题封面优化师
05_内容合规审核官
06_分镜生图设计师
07_视频生成执行员
20_语音字幕包装师
21_ComfyUI成片编排师   # displayed role: ComfyUI素材编排师
22_剪辑成片执行师
23_长视频策划编导
```

Game/Steam:

```text
08_游戏项目编排制片人
09_游戏创意与系统设计师
10_Unity3D架构工程师
11_关卡叙事设计师
12_3D美术技术指导
13_游戏音频与体验设计师
14_Steam发行与测试经理
```

Market/platform:

```text
15_软件市场需求分析师
16_AI员工平台产品经理
17_AI员工平台工作流架构师
18_AI员工平台软件架构师
19_AI员工平台增长验证师
```

Each staff directory contains:

```text
agent.md
flow_rule.json
```

## Management UI Capabilities

Primary tabs:

```text
运行工作流
数字员工
工作流编辑
任务输出
系统状态
```

Important features:

- Run workflows through `/api/run`; progress via `/api/run-status`.
- Create/edit/delete custom staff.
- Create/edit/delete workflows.
- Edit task output files and rebuild final output.
- Rerun a single failed/weak step.
- Resume an interrupted task from the first failed, missing, or empty step via `/api/resume-task`; progress is still polled through `/api/run-status`.
- Upload local knowledge files.
- Upload reference images with thumbnail preview.
- Upload authorized voice samples for local TTS.
- Import ComfyUI API JSON and generate RunningHub-style `nodeInfoList` mappings.
- Save browser settings in `localStorage`.
- Delete generated task folders safely under `my_task_output`.

Buttons/examples:

```text
填入示例       -> short video example
长视频示例     -> workflow_长视频全流程
游戏示例       -> workflow_Unity3D游戏Steam上架
```

## Memory And Context Rules

Long-term memory files:

```text
my_memory/brand_profile.md
my_memory/character_bible.md
my_memory/style_guide.md
```

Default behavior:

- `my_memory` is **not** appended to all workflow steps.
- UI default is `仅视频输出阶段使用 my_memory`.
- Memory is injected only into later video-output agents with IDs starting:

```text
06_
07_
20_
21_
22_
```

Advanced option:

```text
全流程使用 my_memory（高级）
```

Use this only when intentionally wanting every employee to see memory.

## Production Pipeline

Core file:

```text
my_workspace/my_codex_core/production_pipeline.py
```

Package outputs can include:

```text
production_manifest.json
auto_production.md
edit_checklist.md
final_edit_plan.md
image_prompts/storyboard_image_prompts.md
video_prompts/video_generation_prompts.md
audio/audio_subtitle_package.md
audio/voiceover.txt
audio/voiceover.wav
subtitles.srt
comfyui/comfyui_plan.md
comfyui/comfyui_payload.json
```

Current modes:

```text
off
package_only
audio_package
api_ready
comfy_full
```

Important distinction:

- `api_ready` can call separate image/video adapters if configured.
- `comfy_full` can call cloud ComfyUI/RunningHub final-production adapter if configured.
- Final video planning should still end at `22_剪辑成片执行师`.

## Local TTS / VoxCPM2

Core file:

```text
my_workspace/my_codex_core/local_tts_adapter.py
```

UI fields live under `全自动生成 -> 本地配音`.

VoxCPM2 is not bundled. The adapter only calls an already installed local command.

Default command template:

```text
voxcpm clone --text-file {text_file} --reference-audio {reference_audio} --output {output_file}
```

Supported placeholders:

```text
{text}
{text_file}
{reference_audio}
{reference_text}
{output_file}
```

Voice samples are stored under:

```text
my_workspace/my_voice_samples/
```

Use only the user's own voice or explicitly authorized voices.

## ComfyUI / RunningHub

Core file:

```text
my_workspace/my_codex_core/cloud_comfyui_adapter.py
```

The management UI can import ComfyUI API-format JSON and map node inputs to:

```text
{{prompt}}
{{negative_prompt}}
{{image_prompt}}
{{video_prompt}}
{{reference_image}}
{{voice_text}}
{{subtitle_srt}}
{{payload}}
{{seed}}
{{width}}
{{height}}
```

RunningHub defaults used in UI examples:

```text
Base URL: https://www.runninghub.cn/openapi/v2
```

API keys are used only in memory for the request and must not be written to repo or task output.

Long-video ComfyUI template:

```text
my_workspace/comfyui_workflows/long_video_universal/long_video_universal_api_template.json
my_workspace/comfyui_workflows/long_video_universal/runninghub_node_info_list_preset.json
my_workspace/comfyui_workflows/long_video_universal/payload_example.json
```

This template exposes only dynamic fields for management UI mapping: `{{prompt}}`, `{{negative_prompt}}`, `{{reference_image}}`, `{{voice_text}}`, `{{subtitle_srt}}`, and `{{payload}}`. Keep model, sampler, resolution, subtitle style, transitions, audio mix, and final composition in the real ComfyUI/RunningHub workflow.

## Current Verification Snapshot

Recently verified:

- `workflow_长视频全流程` runs offline with 10 steps.
- Web UI restarted on `http://127.0.0.1:8765` and contains `longVideoSampleBtn`.
- JSON validation passed for the long-video workflow and staff `23_长视频策划编导`.
- JS extraction from `web_app.py` passed `node --check`.
- Python syntax was checked by compiling to `runtime/pycheck` to avoid `__pycache__` permission conflicts while the web service is running.

Useful validation commands:

```powershell
python -m json.tool my_workspace\my_workflows\workflow_长视频全流程.json > $null
python -m json.tool my_workspace\my_custom_staff\23_长视频策划编导\flow_rule.json > $null
python my_workspace\run_flow.py --provider offline --workflow workflow_长视频全流程 --input "测试长视频流程"
```

If `python -m py_compile` fails with a `__pycache__` permission error while the service is running, compile to `runtime/pycheck` instead.

## Git Notes

- Normal push target: `origin main`.
- GitHub network sometimes times out on port 443. If push fails, local commits may remain ahead.
- Latest local commit before this compaction: `245d8cc Add long-form video workflow`.

## Next Useful Work

1. Add real FFmpeg/剪映工程 export once final-edit plans stabilize.
2. Add actual local VoxCPM2 install/start helper if the user wants bundled TTS.
3. Add current-task media preview for `audio/voiceover.wav`, `subtitles.srt`, and generated clips.
4. Improve ComfyUI node mapping presets for common image/video/audio workflows.
5. Add stricter output schemas for `22_剪辑成片执行师` so final edit plans can be converted into timelines.
