# Handoff Context

## Update 2026-06-17 - Production progress and image-to-video reference handoff

- Diagnosed latest long-video run `my_workspace/my_task_output/task_20260616_230032_AI员工工作流平台长视频`: workflow text steps completed, then `comfy_full` did run RunningHub/ComfyUI. ComfyUI submitted 14 material jobs: 7 image jobs succeeded and 7 image-to-video jobs failed. FFmpeg still generated `long_video_final.mp4` from the 7 downloaded images and available `audio/voiceover.wav`; final video duration is 24 seconds.
- The video jobs failed because RunningHub returned `prompt_outputs_failed_validation` for `ResizeImageMaskNode` node `4981` with required `input` missing. Root cause: the LTX image-to-video workflow requires a reference image, but the runtime did not automatically pass successful generated images into later video jobs.
- Fixed `CloudComfyUIAdapter`: it now emits production progress events for batch start, each material job submit/status/finish/fail, RunningHub polling status, and batch summary. It also automatically pairs successful image outputs into later video jobs as `reference_image`; local generated image files are converted to Base64 `data:image/...` URI before being sent to RunningHub, because cloud workflows cannot read local Windows paths.
- Fixed `production_pipeline.py` and `workflow_engine.py`: `run_auto_production()` now accepts and forwards `progress_callback`, emits package/ComfyUI/TTS/FFmpeg stage events, and reports final production status.
- Fixed management UI progress display in `web_app.py`: `/api/run-status` now includes recent `production_events` and `production_message`; the run progress panel shows after-processing events below the employee steps, and final status includes `production_status`.
- Verification: `python -m py_compile my_workspace/my_codex_core/cloud_comfyui_adapter.py my_workspace/my_codex_core/production_pipeline.py my_workspace/my_codex_core/workflow_engine.py my_workspace/web_app.py` passed; extracted inline JS from `web_app.py` and `node --check runtime/web_app_inline_check.js` passed; local image-to-data-URI conversion returned a valid `data:image/png;base64,...` value.

## Update 2026-06-16 - Latest 6-second long-video diagnosis

- Diagnosed latest long-video run `my_workspace/my_task_output/task_20260616_220532_AI员工工作流平台长视频`: `long_video_final.mp4` is exactly 6 seconds because `local_ffmpeg_manifest.json` shows FFmpeg had only one visual input, `comfyui/attempt_01/comfyui_result_01.png`, with no video clips and no generated audio.
- Root cause: `comfyui/comfyui_payload.json` contained multiple `image_prompts` and `video_prompts`, but the employee output was invalid JSON because prompt strings included unescaped quotes. The production pipeline previously fell back to `{}` on JSON parse failure, so `CloudComfyUIAdapter` submitted only one default RunningHub task instead of looping through the material prompts.
- Added ComfyUI payload recovery in `my_workspace/my_codex_core/production_pipeline.py`. If `comfyui_payload.json` is invalid JSON, it now salvages prompt items from the text and recovers `image_prompts` / `video_prompts` before calling the cloud adapter. Verified against the latest broken payload: 12 image prompts and 9 video prompts are recovered; Z-Image preset expands to 12 jobs and LTX preset expands to 9 jobs in an offline adapter smoke test.
- Added stale VoxCPM2 command-template normalization in `my_workspace/my_codex_core/local_tts_adapter.py`. Browser-local cached templates such as `voxcpm tts --text-file {text_file} --voice {voice_preset} --output {output_file}` are now ignored server-side so the project-local VoxCPM2 wrapper under `runtime/tts/venv` is used automatically.
- Current behavior expectation: the next long-video run should submit multiple RunningHub material jobs instead of one.
- Updated ComfyUI workflow-library routing so runtime no longer depends on the currently selected management UI slot. The UI now sends each configured slot's endpoint and nodeInfoList to the backend; `CloudComfyUIAdapter` picks the configured生图 slot for image/keyframe jobs and the configured生视频 slot for video jobs per material item. The dropdown is now treated as an editor for saved slots only.
- Verified automatic routing offline against the latest broken payload: 12 recovered image jobs route to `/run/workflow/image` using `txt_img_img`, and 9 recovered video jobs route to `/run/workflow/video` using `image_to_video`, even when the selected UI preset is the image slot. Default `max_material_jobs` is now 50 so image jobs do not starve video jobs in mixed runs.
- Fixed rerun-step UX: `/api/rerun-step` now creates a background job and returns `run_id`, the UI immediately renders progress, polls `/api/run-status`, and reopens the rerun step output after completion. `WorkflowEngine.rerun_step()` now emits `started`, `step_started`, `step_completed`, and `completed` progress events.
- Clarified `21_ComfyUI成片编排师/flow_rule.json`: 21 号员工 outputs a material-generation task list and ComfyUI parameter package; actual PNG/MP4 assets are produced later by `production_pipeline` / `CloudComfyUIAdapter` and written under the task output `comfyui/` directory, not directly by the employee text output.

## Update 2026-06-16

- Added final-video preview support to the management UI task-output page. When a task contains `long_video_final.mp4`, `final_video.mp4`, or another video file, the output dashboard now shows an embedded `<video controls>` player. A new `/api/media` endpoint streams whitelisted media files from the selected task directory only.
- Diagnosed latest long-video run `task_20260616_164938_AI员工工作流平台长视频`: no final video was produced because ComfyUI returned only a RunningHub async task in `RUNNING` state and no downloaded assets, VoxCPM2 failed because `voxcpm` is not installed/on PATH, and FFmpeg skipped with no video/images/audio inputs. Fixed `CloudComfyUIAdapter` provider detection so `comfy_full + ffmpeg` with a RunningHub base URL or `/run/workflow`/`/run/ai-app` endpoint now uses the RunningHub polling path instead of the generic submit-only path.
- Simplified the task-output file tab bar. It now hides debug/internal files by default, including each step's `prompt.md`, `system.md`, `metadata.json`, plus `action_log.json`, `workflow.json`, `run_summary.json`, adapter manifests, attempt folders, product-package duplicates, command logs, and adapter request/response JSON. The underlying files remain available; the UI has a `显示调试文件` checkbox to reveal them when troubleshooting.
- Clarified the full-auto UI again based on user confusion: the primary mode now uses result-oriented labels. The full automation choice is `全自动成片预览：调用 ComfyUI 素材接口 + FFmpeg 自动剪辑`; the compose selector is now labeled `自动剪辑方式`, with `本地 FFmpeg 自动剪辑预览` as the default for full-auto. `comfy_full + ffmpeg` is now supported: the pipeline calls the cloud ComfyUI material adapter first, then runs the local FFmpeg adapter so generated assets can be assembled into `final_video.mp4` when enough media exists.
- Fixed long-video production packaging after a real run: `production_pipeline.py` now extracts `20_语音字幕包装师` voice text from numbered headings such as `## 2. TTS 配音稿`, rejects placeholder voice/SRT content such as `后续SRT内容`, and generates a usable fallback `subtitles.srt` from the voice text when the employee output contains only an incomplete SRT sample. `production_manifest.json` now records `voice_text_status`, `voice_text_chars`, `subtitle_status`, `subtitle_reason`, and `subtitle_entries` so future failed runs show whether audio/subtitle inputs were actually usable.
- Clarified the management UI auto-generation mode labels. The dropdown now says `只生成视频制作包（不调用工具）`, `生成制作包 + 配音/字幕文本包（推荐）`, `调用独立生图/生视频 API（旧接口）`, and `调用 ComfyUI 生成素材/预览草稿（高算力）` to avoid implying that final voice, material, or finished video will always be produced without the corresponding local/cloud tools.
- Current known issue from the latest long-video run: local VoxCPM2 TTS failed because the command `voxcpm` is not installed or not on PATH. The pipeline now feeds it the correct voice text, but audio generation still requires installing/configuring the actual VoxCPM2 command or switching local TTS off.
- Fixed the long-video sample button so it no longer clears existing ComfyUI API settings, node mappings, poll timeout, voice mode, voice preset, uploaded voice reference path, reference text, command template, or voice timeout. The button now only fills the long-video example requirement and long-video basic defaults.
- Added default AI voice presets to the management UI local TTS section. New `voiceMode=preset` uses a built-in voice selector (`warm_female`, `clear_female`, `pro_male`, `deep_male`, `young_male`, `story_female`) and does not require a reference audio upload. The default command template is `voxcpm tts --text-file {text_file} --voice {voice_preset} --output {output_file}`. `LocalTTSAdapter` now supports `{voice_preset}` and `{voice_name}` placeholders and writes preset info to `local_tts_manifest.json` / `production_manifest.json`.
- Strengthened local FFmpeg final-composition path. `LocalFFmpegAdapter` now recursively collects videos/images from `video_clips/`, `generated_images/`, and `comfyui/`, uses `audio/voiceover.wav` when available, burns `subtitles.srt` by default, and writes `edit_timeline.json`, `ffmpeg_edit_plan.md`, `local_ffmpeg_command.txt`, stdout/stderr logs, `local_ffmpeg_manifest.json`, and `final_video.mp4` when enough assets exist.
- `production_manifest.json` now records FFmpeg manifest, command, timeline, and edit-plan files under `files` and `composition`.
- `22_剪辑成片执行师` now explicitly requires executable FFmpeg/editing timeline handoff and missing-asset diagnostics.

## Current State

- Repo: `I:\Ai_WorkSpace\agency-agents-zh`
- Origin: `https://github.com/2587122824/agency-agents-zh.git`
- Upstream: `https://github.com/jnMetaCode/agency-agents-zh.git`
- Custom work lives under `my_workspace/`; original upstream agent files are not modified.
- Current local branch may be ahead of GitHub if network push failed. Latest local commit at time of this handoff: `245d8cc Add long-form video workflow`.

Latest update on 2026-06-15:

- Removed audio and subtitle responsibilities from the ComfyUI step and simplified the default ComfyUI workflow library from 6 slots to 5 active slots. Slot `05` is `字幕安全区画面素材`; the old `06_audio_subtitle_video_preview` canvas is kept only as a deprecated compatibility artifact and is no longer listed in `index.json` or the management UI. ComfyUI mappings now only use visual-material inputs such as prompt, negative prompt, reference image, seed, width, and height. `voice_text`, `subtitle_srt`, and `subtitle_style` are no longer included in ComfyUI node mapping defaults or generated `comfyui_payload.json`; the management UI sanitizes old browser-cached node mappings that still contain those placeholders or nodes `5101-5103`. Audio, subtitles, hard subtitles, mix, and final export belong to `20_语音字幕包装师` and `22_剪辑成片执行师`.
- Added a full-auto material quality gate for ComfyUI `comfy_full` runs: management UI now exposes `素材自动评审`, `最多尝试次数`, and `最低通过分`; `production_pipeline.py` retries ComfyUI attempts in `comfyui/attempt_XX/`, scores by API success, downloaded files, and file size, writes `comfyui/auto_quality_report.json`, and keeps the best result. Missing API configuration stops after one skipped attempt.
- Updated all LTX-based ComfyUI workflow-library canvases (`02`-`06`) to use the user's visible node model names: `ltx-2.3-22b-dev-fp8.safetensors`, `gemma_3_12B_it.safetensors`, and `ltx-2.3-22b-distilled-lora-384-1.1.safetensors` without the old `ltxv/ltx2/` LoRA path prefix.
- Fixed the LTX reference-image templates `02_ltx_video_2_3` and `03_reference_consistency`: `bypass_i2v` is now false by default, I2V reference strength is raised to 0.9 / 1.0, and the RunningHub presets now target the real canvas nodes (`2004.image`, `2483.text`, `2612.text`) instead of old demo nodes (`10/11/12`). If the browser has old workflow-library settings in localStorage, reset the slot or re-import the new preset before testing.
- Refined the ComfyUI workflow library UI: the library list now shows only the selected slot, changing the dropdown loads that slot's saved endpoint/nodeInfoList/timeout/purpose into the editable fields, and node mapping/import/timeout controls were moved directly under the workflow-library selector before voice settings.
- Improved form alignment in the management UI: provider-grid forms now top-align controls, and the ComfyUI node-mapping row uses a shorter textarea so file import and timeout controls no longer stretch vertically.
- Improved ComfyUI API JSON mapping UX: imported node candidates render in a compact, height-limited scrolling panel so large workflows do not stretch the page; saving the selected workflow-library slot now persists the slot to browser localStorage and clears the temporary imported API JSON file/candidate list while keeping the saved nodeInfoList.
- Active ComfyUI workflow-library canvas templates under `my_workspace/comfyui_workflows/workflow_library/`: Z-Image Turbo text-to-image/keyframe generation, image-to-video with LTX-Video 2.3, reference consistency, B-roll material, and subtitle-safe visual material. The first Z-Image Turbo template is text-to-image only; image/reference consistency should use `03_reference_consistency` or a real Z-Image Turbo + ControlNet/IP-Adapter/editing workflow exported from ComfyUI. Each active slot has `workflow_canvas.json`, `api_template.json`, and `runninghub_node_info_list_preset.json`; 21_ComfyUI素材编排师 now references these templates.
- LTX-Video 2.3 canvas templates now include an in-canvas preflight note and `LTX_VIDEO_2_3_MODEL_REQUIREMENTS.md`; users must upload a reference image in the LoadImage node and install/select local LTX checkpoint, Gemma text encoder, and LTX LoRA files before running, otherwise ComfyUI reports `Invalid image file` or `Value not in list`.
- Added and installed the local FFmpeg execution framework: `install_ffmpeg_runtime.ps1` installs FFmpeg under `runtime/ffmpeg/`; `LocalFFmpegAdapter` detects `runtime/ffmpeg/bin/ffmpeg.exe`, `runtime/ffmpeg/ffmpeg.exe`, or PATH `ffmpeg`; when compose tool is `ffmpeg` and task assets exist, it can generate `final_video.mp4`, otherwise it records a skipped reason in `local_ffmpeg_manifest.json` without failing the workflow. System health now reports FFmpeg availability and currently detects the project-local `runtime/ffmpeg/bin/ffmpeg.exe`.
- Added visible global button-click feedback in the management UI. Operation buttons now immediately show a top-right toast with `正在处理`, status areas still show detailed success/error results, and main tab switching no longer shows a toast.
- Added a browser-local `ComfyUI 工作流库` in the management UI with 5 active default slots: text/image-to-image, image-to-video, reference consistency, B-roll material generation, and subtitle-safe visual material. Each slot stores endpoint, nodeInfoList, timeout, and purpose; runtime passes selected slot and sanitized library status to employees and `production_manifest.json`.
- Optimized the long-video workflow and full-auto UI semantics: long-video template is now available in the product template selector, the long-video sample defaults to `audio_package`, and `comfy_full` is described as `ComfyUI 素材/预览草稿` rather than final video export.
- Clarified long-video workflow boundaries: `07_视频生成执行员` outputs auxiliary AI video material only, `20_语音字幕包装师` owns `voiceover.txt` and `subtitles.srt`, `21_ComfyUI素材编排师` prepares material/preview parameters, and `22_剪辑成片执行师` owns final hard subtitles, mix, export specs, and release checks.
- Adjusted video pipeline ownership: `20_语音字幕包装师` owns voice + SRT packaging, `21_ComfyUI成片编排师` uses voice/subtitle inputs only for ComfyUI preview/draft automation, and `22_剪辑成片执行师` owns final hard subtitles, final mix, and final export.
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
- `20_语音字幕包装师` owns both voice text and subtitle/SRT packaging so voice and subtitles stay consistent.
- `21_ComfyUI成片编排师` folder remains for compatibility, but displayed role is now `ComfyUI素材编排师`.
- ComfyUI should use voice/subtitle inputs only for preview, draft automation, or special visual subtitle effects by default.
- `22_剪辑成片执行师` is the final video step.
- Final hard subtitles, final audio mix, and final export belong to `22_剪辑成片执行师` / editing tools by default.
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
- Configure a browser-local ComfyUI workflow library for common material/preview jobs.
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
- `comfy_full` can call cloud ComfyUI/RunningHub material/preview adapter if configured.
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

This template exposes only dynamic fields for visual-material mapping: `{{prompt}}`, `{{negative_prompt}}`, `{{reference_image}}`, and `{{payload}}`. Keep model, sampler, resolution, transitions, and material-generation logic in the real ComfyUI/RunningHub workflow. Final subtitle burn, final audio mix, and final export are handled by `20_语音字幕包装师` / `22_剪辑成片执行师` / editing tools by default.

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

## Update 2026-06-16 - ComfyUI material loop

- Fixed the ComfyUI/RunningHub automation path so a workflow payload with multiple material prompts is no longer submitted as only one job. `CloudComfyUIAdapter` now expands `image_prompts` / `video_prompts` into per-material jobs, runs each job in `comfyui/attempt_XX/material_YY_*`, writes each job manifest, and writes an aggregate `cloud_comfyui_manifest.json` with `looped`, `job_count`, `success_count`, `failed_count`, and all downloaded files.
- Workflow-library selection now controls which prompts are looped: `01_image_z_image_turbo` loops image/keyframe prompts, `02_ltx_video_2_3` loops video prompts, and reference/B-roll presets can loop both image and video prompt groups. Current latest long-video payload splits into 5 image jobs for Z-Image, 4 video jobs for LTX, or 9 jobs for reference/B-roll style presets.
- Partial batch success is accepted by the production pipeline and quality gate. If at least one material downloads, the manifest can continue to FFmpeg; if every material job fails, the ComfyUI adapter reports failure.
- Local FFmpeg already recursively collects media under `comfyui/`, so generated batch outputs in nested `material_YY_*` folders are automatically available for final preview composition.

## Update 2026-06-16 - Local TTS setup

- Installed VoxCPM2 package into the project runtime environment at `runtime/tts/venv` with `voxcpm==2.0.3`; `runtime/tts/cache` is used as the model cache. The first VoxCPM2 synthesis downloaded about 4GB of `openbmb/VoxCPM2` cache but CPU synthesis is slow and may take a long time.
- Added `install_voxcpm2_runtime.ps1` so the VoxCPM2 runtime can be recreated without using the global Python environment.
- Added `my_workspace/my_codex_core/voxcpm2_tts_runner.py`, a project wrapper around VoxCPM2 that reads `audio/voiceover.txt`, supports default voice presets and reference-audio cloning, chunks long text, and writes `voiceover.wav`.
- Updated `LocalTTSAdapter` so empty command templates use the project-local VoxCPM2 wrapper automatically. The default VoxCPM2 command uses CPU, disables denoiser and optimization for more stable local startup, and writes outputs under the task `audio/` directory.
- Added a Windows SAPI fallback provider (`windows_sapi`). It is fast and fully local, and is now available in the management UI as `Windows 本地语音（最快备用）`. Verified it generated `runtime/tts_sapi_smoke/voiceover.wav` successfully.
- System health now reports `VoxCPM2 本地配音` availability by checking `runtime/tts/venv/Scripts/voxcpm.exe` and the project runner.
