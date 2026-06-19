# Handoff Context

## Current State

- Repo: `I:\Ai_WorkSpace\agency-agents-zh`
- Main custom app: `my_workspace/`
- Management UI: `python my_workspace/web_app.py`
- Default URL: `http://127.0.0.1:8765`
- Local model target: Ollama-compatible API at `http://127.0.0.1:11434/v1`, default model `qwen3:8b-q4_K_M`.
- Current product mode: long-video workflow only. Daily run path is `workflow_长视频全流程`.
- Original upstream agent folders should stay untouched unless explicitly requested.
- Do not commit API keys, uploaded references, voice samples, generated task outputs, local model files, or large media assets.
- Old short-video, xiaohongshu, game, software-market, and platform-design workflows/staff are archived in place. Do not delete them unless explicitly requested.

## Current UX / Runtime Rules

- `新建任务` is only for entering the long-video requirement and starting a task.
- `任务输出` is the execution console: progress, current step details, stop, confirm/continue, rerun, output files, final video preview, and export.
- `系统配置` owns model, ComfyUI/RunningHub, TTS, FFmpeg, memory, and automation settings.
- The run page is intentionally ChatGPT-style: one main demand box plus the run button.
- `推进方式`:
  - `一键到底`: runs automatically to final output.
  - `逐步确认`: pauses after each completed employee step and waits for the user to review output.
- Running/resume/rerun should lock run-related inputs until the job is completed, failed, cancelled, or paused.
- Refresh/browser-exit should pause active jobs so the task can resume from `任务输出`.
- Task output should auto-select the latest task when no task is selected.
- Progress panel should show the current step by default; details are collapsed and expandable.
- When `显示调试文件` is off, the top file tabs should only show `input.md` and `final_output.md`; step outputs remain available in the dedicated step-output list.
- The task-output summary card strip is intentionally hidden to avoid duplicate task/workflow/final-status information.

## Production Pipeline

- Long-video workflow ends with editing/composition as the final authority.
- ComfyUI/RunningHub is for visual material generation or preview clips, not final subtitle/audio burning by default.
- Audio and subtitles belong to the voice/subtitle and editing employees:
  - `20_语音字幕包装师`
  - `22_剪辑成片执行师`
- ComfyUI material scheduling is now integrated into:
  - `06_分镜生图设计师` for `image_prompts`
  - `07_视频生成执行员` for `video_prompts`
- Final composition is handled locally by FFmpeg when configured.
- `my_memory` should normally be injected only into video-output stages, not every employee step.
- In `comfy_full` mode, the engine runs a hard ComfyUI material gate after the material step. For the current long-video workflow, this is now the `07_视频生成执行员` step; older workflows with `21_` still gate there for compatibility.
- The material gate only passes when the ComfyUI adapter reports success, has downloaded files, and its manifest has `success_count == job_count` and `failed_count == 0`.
- If the material gate fails, the workflow must stop at the ComfyUI material step and must not enter the final editing step.
- RunningHub/ComfyUI endpoint placeholders such as `/run/workflow/keep` are invalid and should be surfaced as configuration errors.

## Recent Fixes

- The current long-video workflow no longer runs `21_ComfyUI素材编排师` as a separate step. Its responsibilities are integrated into `06_分镜生图设计师` (`image_prompts`) and `07_视频生成执行员` (`video_prompts`). `production_pipeline.py` now merges the first JSON blocks from 06/07 into `comfyui/comfyui_payload.json`, and the ComfyUI material gate falls back to the 07 step when no 21 step exists.
- Staff 06 and 07 now explicitly require full per-shot prompt expansion: every row in the storyboard / shot list must have a matching detailed prompt section, with no "same as above", "omitted", or "key shots only" shortcuts. Staff 21 now has a fallback requirement to cover all shot numbers in `image_prompts` / `video_prompts`, and to record inferred or missing prompts in `missing_or_inferred_prompts` instead of silently dropping shots.
- The first ComfyUI template `01_image_z_image_turbo` is a single boolean-Switch image workflow: `Switch=false` routes `EmptySD3LatentImage` to KSampler for text-to-image, and `Switch=true` routes `LoadImage -> VAEEncode` to KSampler for image-to-image. The Switch node must be the Comfy Core boolean Switch with only `on_false` and `on_true` inputs, not the `Input_1` / `Path` variant. API template and RunningHub nodeInfo preset include `LoadImage.image -> {{reference_image}}` on node `12` and `Switch.switch -> {{has_reference_image}}` on node `63`.
- ComfyUI material job chaining now supports image-to-image as well as image-to-video. During the sequential material loop, image jobs with explicit `reference_image` resolve against earlier generated images; image jobs without a reference automatically use the previous generated image when available. Video jobs keep the existing generated-image pairing behavior.
- Task output editing is now allowed while a run is paused at a step-confirmation checkpoint. The UI tracks the active run status separately from `currentRunId`, so `paused` / `awaiting_confirmation` no longer disables save, rerun, or confirm controls as if the job were still running.
- ComfyUI workflow templates now carry explicit material type metadata (`image` / `video`) through the UI payload, and `CloudComfyUIAdapter` prefers that metadata when routing material jobs. This reduces reliance on Chinese/English keyword matching in slot names. README files for `01_image_z_image_turbo`, `04_broll_material`, and deprecated `06_audio_subtitle_video_preview` were rewritten to remove mojibake and clarify production boundaries.
- Auto production mode `api_ready` is now image-only: the UI label is `只生图，不生视频`, the production pipeline no longer calls the legacy video adapter in this mode, and `production_manifest.json` marks `video_generation.adapter_status` as `skipped`.
- Resume now passes `production_config` into `WorkflowEngine.resume()`, so resumed long-video jobs can still run ComfyUI, TTS, and FFmpeg production.
- Step-confirm to auto-run transition is fixed: confirming a paused step clears `awaiting_confirmation` even after the user switches `推进方式` to auto.
- Confirmation-derived `blocked_step` / `blocked_reason` are cleared together so stale waiting-confirmation state does not remain in `run_summary.json`.
- After a completed resume, task output prefers `final_output.md` instead of reopening the last employee output.
- After `resumeSelectedTask()` finishes polling, the selected task is fetched again so step outputs, product package files, and final video preview refresh from disk.
- The confirmation button text updates immediately when the user switches advance mode; auto mode shows `确认并自动跑完后续步骤`.
- Local TTS now uses managed process execution, caps long timeouts, and kills child process trees on timeout.
- Windows SAPI TTS uses PowerShell encoded commands to preserve Chinese task paths.
- FFmpeg composition can generate `long_video_final.mp4` when audio/material inputs are available.
- Progress details preserve useful RunningHub/ComfyUI/TTS/FFmpeg metadata while hiding repetitive plain step rows.
- VoxCPM2 preset/clone TTS no longer hard-caps CPU synthesis to 300/900 seconds. The adapter now honors the requested timeout and automatically raises it for long CPU scripts based on character count; the UI default is 60 minutes with 90 minutes available.
- Task output now includes an `已生成素材` section that surfaces only generated image/video media, including final videos and image/video files under generated image, video clip, or ComfyUI output folders. Media opens through `/api/media` instead of the text editor; image assets show thumbnails in the management UI.
- During active runs, the UI only auto-focuses `任务输出` while the run was just started/resumed/rerun. If the user manually switches top tabs or terminates the task, polling no longer forces the page back to `任务输出`, so other tabs remain clickable after stop/cancel.
- Auto production can run material generation/matching and local TTS in parallel during full production, because material generation depends on visual prompts while TTS depends only on voiceover text. FFmpeg still waits until both branches finish. The ComfyUI material gate path with `stop_after_comfyui=True` remains material-only and does not start TTS.
- Task detail API now returns structured `task_state`, `allowed_actions`, and `assets` fields. The management UI uses `assets.images/videos` for the generated-material section and `allowed_actions` for output action buttons, with filename-based logic retained only as a compatibility fallback.
- Task detail API also derives structured `production_jobs` from `production_manifest.json`: material generation/matching, TTS, and FFmpeg composition. The output page renders these as production status cards so the UI no longer needs to infer branch state from raw manifest files.
- The production status cards now expose first-version branch retry actions: `重试素材` (`material`), `重试配音` (`tts`), and `重新合成` (`ffmpeg`). `/api/retry-production-job` starts these as background jobs, reuses the existing production package, updates `production_manifest.json`, appends `production_job_history`, and refreshes `run_summary.json.production_status`.
- ComfyUI material status and FFmpeg composition status are now separated in the manifest via `composition.comfyui_adapter_status` / `composition.local_ffmpeg_status` while keeping legacy `adapter_status` compatibility. This prevents a successful FFmpeg retry from incorrectly overwriting the material card state.

- During an active run, the output page now silently refreshes the selected task detail every ~2 seconds after the task directory exists. This keeps `步骤输出` in sync with newly written `step_*/output.md` files without forcing the user away from the current file or tab.
- Task detail now has a first-version authoritative `task_status` object. It normalizes workflow state, per-step state, production branch state, assets, allowed actions, and diagnostics in one backend structure. `/api/task-status?name=...` returns just this state center for polling or external diagnostics, while `/api/task` keeps backward-compatible legacy fields.
- The output page now prefers `task_status.steps`, `task_status.production.jobs`, `task_status.assets`, `task_status.allowed_actions`, and `task_status.diagnostics` over front-end filename/manifest inference. Diagnostics render as cards above production jobs, so production failures and blocked reasons are visible without opening raw JSON files.
- RunningHub/ComfyUI reference-image parameter handling is hardened. Unresolved placeholders such as `generated/shot_003_keyframe.png` are no longer passed raw into `LoadImage.image`; the adapter clears unresolved references, sets `{{has_reference_image}}` false, and drops empty `image` nodeInfo entries. Relative generated image paths are resolved against the task ComfyUI output directory when present, uploaded first for RunningHub, and the returned file value is passed to `LoadImage.image`.
- The second ComfyUI workflow template `02_ltx_video_2_3` / management slot is restored to dynamic I2V output. A previous attempt wired SaveVideo to a single-image OpenPose/LTX pose-control component, which froze motion because one still image yields one static pose, and could emit noisy audio. The experimental LTX pose-control UUID component and component definitions were removed from the default output path. SaveVideo links are back to the original dynamic LTX I2V `CreateVideo` nodes (`4819 -> 4823`, `4849 -> 4852`), and RunningHub defaults no longer pass pose-control fields. Use a separate pose-to-video workflow only when there is a pose video or pose sequence.
- The same template now keeps a visible but non-output OpenPose preview branch: `6101 DWPreprocessor -> 6102 ResizeImageMaskNode -> 6103 PreviewImage`. This lets users see/control-check the extracted pose node without wiring a single still pose into final video generation. `4823/4852 SaveVideo` remain connected only to dynamic LTX I2V outputs.
- ComfyUI workflow routing is being consolidated into two API entrypoints: `全能图片` (`all_in_one_image`, material type `image`) and `全能视频` (`all_in_one_video`, material type `video`). The management UI default workflow library now exposes only these two slots. `CloudComfyUIAdapter` routes image jobs to all-in-one image and video jobs to all-in-one video by `materialTypes`, and it now passes `task_type`, `control_mode`, `duration`, and `fps` placeholders into RunningHub nodeInfo replacement. Staff 06/07 JSON examples now require `task_type` and `control_mode` but still avoid node IDs or provider-specific internals.
- The all-in-one workflow template directories now include `workflow_contract.json` and `node_info_extended_example.json`. These are the canonical build contracts for making real ComfyUI/RunningHub workflows with IPAdapter/FaceID/style/product/video-control branches. Default `runninghub_node_info_list_preset.json` files intentionally remain conservative and only contain known-safe mappings until the user exports real API JSON and calibrates real node IDs.
- The all-in-one workflow template directories now also include importable `workflow_canvas.json` files. `01_all_in_one_image/workflow_canvas.json` is based on the verified Z-Image image canvas, and `02_all_in_one_video/workflow_canvas.json` is based on the verified LTX dynamic I2V canvas. These are ComfyUI canvas import files; `api_template.json` is only for API/RunningHub reference, not for canvas import.

- The management UI `自动化与成片配置` panel has been regrouped into clearer cards for flow switches, ComfyUI connection, two-workflow routing, node mapping, material quality gate, local voice, and advanced voice settings. `web_app.py` now uses `.automation-config`, `.config-card`, `.workflow-summary-card`, and `.mapping-card` styles for this panel.

## Important Files

- Web UI: `my_workspace/web_app.py`
- Workflow engine: `my_workspace/my_codex_core/workflow_engine.py`
- Production pipeline: `my_workspace/my_codex_core/production_pipeline.py`
- Cloud ComfyUI adapter: `my_workspace/my_codex_core/cloud_comfyui_adapter.py`
- Local TTS adapter: `my_workspace/my_codex_core/local_tts_adapter.py`
- Local FFmpeg adapter: `my_workspace/my_codex_core/local_ffmpeg_adapter.py`
- Custom staff: `my_workspace/my_custom_staff/`
- Workflows: `my_workspace/my_workflows/`
- Generated outputs: `my_workspace/my_task_output/`
- Voice samples: `my_workspace/my_voice_samples/`

## Useful Commands / Known Risks

```powershell
python -m py_compile my_workspace/my_codex_core/local_tts_adapter.py my_workspace/my_codex_core/workflow_engine.py my_workspace/my_codex_core/production_pipeline.py my_workspace/my_codex_core/cloud_comfyui_adapter.py my_workspace/web_app.py
$env:PYTHONIOENCODING='utf-8'; @'
from pathlib import Path
text=Path('my_workspace/web_app.py').read_text(encoding='utf-8')
start=text.index('<script>') + len('<script>')
end=text.index('</script>', start)
print(text[start:end])
'@ | python - | node --check
python my_workspace/web_app.py
```

Safe restart of the management UI:

```powershell
$port = 8765
$conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($conns) {
  $ownerPids = $conns | Select-Object -ExpandProperty OwningProcess -Unique
  foreach ($ownerPid in $ownerPids) {
    Stop-Process -Id $ownerPid -Force -ErrorAction SilentlyContinue
  }
}
Start-Process -FilePath python -ArgumentList 'my_workspace/web_app.py' -WorkingDirectory (Get-Location).Path -WindowStyle Hidden
```

- VoxCPM2 CPU synthesis can be slow. Use Windows SAPI fallback or short practical timeouts when local synthesis is unreliable.
- RunningHub LTX image-to-video jobs may fail if the cloud workflow expects a different reference-image node or upload format.
- Browser localStorage may contain stale ComfyUI workflow-library slots. Reset or re-save slots if mappings look wrong.
- If a task says completed but lacks final media, inspect `production_manifest.json`, `local_ffmpeg_manifest.json`, and the ComfyUI adapter manifest first.
