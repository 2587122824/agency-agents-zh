# Handoff Context

Last compacted: 2026-07-04

## Project Snapshot

- Repo: `I:\Ai_WorkSpace\agency-agents-zh`
- Main custom app: `my_workspace/`
- Management UI: `python my_workspace/web_app.py`
- Default URL: `http://127.0.0.1:8765`
- Local model target: Ollama-compatible API at `http://127.0.0.1:11434/v1`
- Default model: `qwen3:8b-q4_K_M`
- Current product mode: long-video workflow only.
- Daily workflow: `my_workspace/my_workflows/workflow_长视频全流程.json`

Keep upstream agent folders untouched unless explicitly requested. Do not commit API keys, uploaded references, voice samples, generated task outputs, local model files, or large media assets.

Old short-video, xiaohongshu, game, software-market, and platform-design workflows/staff are archived in place. Do not delete them unless explicitly requested.

## Current UX Rules

- `新建任务` is only for entering the long-video requirement and starting a task.
- `任务输出` is the execution console: progress, current step details, stop, confirm/continue, rerun, output files, final video preview, and export.
- `系统配置` owns model, ComfyUI/RunningHub, TTS, FFmpeg, memory, and automation settings.
- The run page should stay ChatGPT-style: one main demand box plus the run button.
- `推进方式`:
  - `一键到底`: run automatically to final output.
  - `逐步确认`: pause after each completed employee step and wait for review.
- Running/resume/rerun must lock run-related inputs until completed, failed, cancelled, or paused.
- Refresh/browser exit should pause active jobs so they can resume from `任务输出`.
- Task Output should auto-select the latest formal task when no task is selected. It must not auto-select `__comfy_debug__`.
- Progress should show the current step by default; details are collapsed and expandable.
- When `显示调试文件` is off, top file tabs should only show `input.md` and `final_output.md`; step outputs stay in the dedicated step-output list.
- The duplicated task-output summary card strip is intentionally hidden.

## Production Pipeline Rules

- Long-video workflow ends with local editing/composition as the final authority.
- ComfyUI/RunningHub is for visual material generation or preview clips, not final subtitle/audio burning by default.
- Audio/subtitle ownership:
  - `20_语音字幕包装师`
  - `22_剪辑成片执行师`
- Visual scheduling ownership:
  - `06_分镜生图设计师` for `image_prompts`
  - `07_视频生成执行员` for `video_prompts`
- Final composition is handled locally by FFmpeg when configured.
- `my_memory` should normally be injected only into video-output stages, not every employee step.
- In `comfy_full` mode, the hard ComfyUI material gate runs after the material step. For the current long-video workflow this is the `07_视频生成执行员` step; older workflows with `21_` remain compatible.
- The material gate only passes when the ComfyUI adapter reports success, has downloaded files, and its manifest has `success_count == job_count` and `failed_count == 0`.
- If the material gate fails, stop at the ComfyUI material step. Do not enter final editing.
- RunningHub/ComfyUI endpoint placeholders such as `/run/workflow/keep` are invalid and should surface as configuration errors.

## Architecture To Preserve

- Four production layers are active:
  - Staff outputs use semantic `production_intents`.
  - `my_workspace/my_production_templates/production_templates.json` maps production types to template defaults.
  - `production_plan_compiler.py` compiles staff intents into `production_plan.json`, compatible `image_prompts`, and `video_prompts`.
  - `production_pipeline.py` executes visual/TTS/BGM/FFmpeg packaging and writes the production manifest.
- `production_graph.json`, `production_job_state.json`, and `production_manifest.json` describe the visual DAG, cache state, packaging nodes, dependencies, blockers, and artifacts.
- `task_state_center.py` is the canonical read model for Task Output. Prefer it over scattered frontend inference.
- `production_parameter_policy.py` locks character identity, style, shot composition, working render size, frame rate, and first/middle/last clip parameters.
- Visual working dimensions:
  - 16:9 -> `848x480`
  - 9:16 -> `480x848`
  - 1:1 -> `480x480`
- Delivery dimensions are normalized separately:
  - 16:9 -> `1920x1080`
  - 9:16 -> `1080x1920`
  - 1:1 -> `1080x1080`
- Global frame rate is 24fps unless the locked render context says otherwise.
- First/middle/last video clips are locked to 4 seconds / 24fps.
- Staff should reference entities and intents, not override locked face/hair/outfit/style/resolution/FPS values.

## Visual Provider State

- Supported visual providers:
  - `runninghub`
  - `comfy_mcp`
  - `local_comfyui`
- `visual_provider_router.py` normalizes provider selection.
- `ComfyMCPAdapter` supports discovery and execution through JSON-RPC `tools/list`, `tools/call`, common REST wrappers, polling, and artifact normalization.
- `/api/test-comfy-mcp` checks MCP connectivity.
- `/api/sync-comfy-mcp-workflows` saves discovered workflows to `comfyui_workflows/mcp_discovered_workflows.json` without overwriting the calibrated local workflow library.

## ComfyUI / RunningHub Contracts

- Subtemplates use typed semantic input contracts instead of one ambiguous `requires_reference` boolean.
- Adapter input roles include `input_identity_image`, `input_pose_image`, `input_source_video`, and mask/source-video roles where relevant.
- The ComfyUI debug console groups submodes by input/output shape plus post-processing intent, not by production stage:
  - `01 文生图`: text-only image generation, including base assets, style/cover images, and text-only keyframes.
  - `02 图生图`: reference-image-driven image generation, including turnaround sheets and identity/style/pose keyframes.
  - `03 图片处理`: image repair, inpaint, cutout, matting, and other non-generative image post-processing.
  - `04 文生视频`: prompt-only video generation such as B-roll, empty shots, and transitions.
  - `05 图生视频`: image-driven video generation such as first-frame, first/last-frame, first/middle/last-frame, and talking-image clips.
  - `06 视频生视频 / 视频处理`: video-driven stylization, motion transfer, enhancement, interpolation, stabilization, and repair.
- The debug-console grouping is display-only. It must not rename workflow IDs, mode values, nodeInfo mappings, input contracts, or production compiler routes. Once production quality stabilizes, these display groups can support a later architecture consolidation pass.
- Keyframe modes:
  - text-only keyframe
  - style-reference keyframe
  - img2img style keyframe
  - identity-reference keyframe
  - identity+pose keyframe
  - multi-character identity keyframe
  - multi-character identity+pose keyframe
- `04_keyframe/style_reference_keyframe` is intentionally exposed as a concrete debug-console submode for the current stabilization phase. It uses an SDXL IPAdapter Style Transfer img2img canvas from `04_keyframe_image/style_reference_keyframe_canvas.json`, with nodeInfo in `style_reference_keyframe_nodeinfo.json`. Later architecture work can collapse it into `04_keyframe/keyframe + controls.style_reference`.
- `04_keyframe/img2img_style_keyframe` is a separate stabilization-phase submode for reference-image-based keyframes that should preserve source subject/composition. Its active nodeInfoList is calibrated to the user-provided Flux/Kontext img2img workflow `风格参考图生图.json`, mapping `LoadImage(81)`, `CLIPTextEncode(39/40)`, resize primitives `90/89`, `KSampler //Inspire(86)`, and `SaveImage(50)`. Default runtime `denoise=0.45` remains a conservative starting point; lower values preserve more of the source image.
- Staff 06 may output `characters[]` for multi-person `generate_keyframe` intents. The compiler resolves each character to independent identity assets and routes 2-4 person shots to the multi-character keyframe modes.
- RunningHub nodeInfo placeholders include `{{character_references}}`, `{{character_reference_1}}` through `{{character_reference_4}}`, `{{character_id_1}}` through `{{character_id_4}}`, and matching position placeholders.
- Turnaround/three-view results are auto-stitched into `*_turnaround_sheet.png`. The stitched sheet is first in `downloaded_files` and recorded in manifest metadata.
- The stitched sheet uses adaptive background sampling and asymmetric layout so identity/pose keyframe models can consume a coherent reference sheet.
- RunningHub style-reference prompts are environment-only: strip incidental human-appearance clauses and append an empty-scene/no-person constraint.
- LTX2.3 text-to-video routing uses the user's current node IDs:
  - prompt `73.text`
  - negative prompt `25.text`
  - dimensions `43/44.value`
  - duration `74.value`
  - FPS `20/21.value` plus `40.frame_rate`
  - seeds `28/46.noise_seed`
- `10_broll_transition_video` preserves explicit subtype: `broll_scene_video` vs `empty_transition_video`.

## Audio / Subtitle / FFmpeg State

- Local TTS retry maps aliases such as `tts`, `bgm`, and `ffmpeg` back to canonical packaging nodes: `local_tts`, `bgm_select`, `ffmpeg_compose`.
- Existing tasks can self-heal stale `local_tts` state from a durable successful `local_tts_manifest.json` plus existing WAV.
- FFmpeg aligns continuous narration to multi-entry subtitle/shot timing with silence midpoint detection and per-segment tempo adjustment.
- Burned Chinese subtitles use `subtitles_burn.srt` with punctuation-aware line breaks. Source sidecar SRT remains unchanged.
- FFmpeg pads short narration instead of truncating the visual timeline. BGM/voice mixes use longest-authoritative audio where appropriate.
- Final MP4 defaults use delivery-oriented H.264: x264 `medium`, CRF 26, max rate 3 Mbps, buffer 6 Mbps, AAC 128 kbps, fast-start. These can be overridden through `compose_config`.

## Recovery / Retry Rules

- `run_auto_production(..., prepare_only=True)` compiles package/manifest without invoking RunningHub, TTS, BGM, or FFmpeg.
- Visual quality retry should reuse completed outputs, isolate failing/duplicate jobs, expand to dependent downstream jobs, and change seeds only for retried jobs.
- Each RunningHub job writes `runninghub_task_state.json` with request fingerprint and taskId so retries/service restarts can query/download existing results instead of submitting duplicates.
- Material retry promotes the best legacy `attempt_XX/production_job_state.json` into the stable root cache when needed.
- Existing-task material retry must preserve non-empty ComfyUI/RunningHub credentials and base URLs already stored in task production config when the retry payload sends blank overrides.
- Optional video enhancement slots should be skipped rather than blocking final composition when unconfigured.

## Validation / Guardrails

- Requirement locking writes `task_brief.json` with original requirement, core topic, duration, style, structure constraints, and confirmation policy.
- Steps 1-3 receive only the compact original requirement, not raw asset-library or ComfyUI config noise.
- Model outputs are checked for topic retention, duration/structure coverage, ungrounded drift, and inappropriate confirmation blockers.
- Only decisions affecting theme, platform specs, brand/product, budget, identity, copyright/compliance, or final delivery may require human confirmation.
- Employee production-output validation is active for 03/06/20/07/22. Failed validation gets one automatic correction retry, then fails visibly.
- Validation checks include:
  - voice text fits target duration
  - image/video intents parse as one JSON object
  - 480p working dimensions match aspect ratio
  - video references resolve to real 06 intent IDs
  - first/middle/last clips stay 4 seconds / 24fps
  - subtitle timestamps and coverage are sane
  - final edit timeline and missing-assets state are internally consistent
- `production_contract_validation.json` records validation details.
- Human-confirmation detection accepts quoted JSON keys/values and Markdown-style assignments. Explicit `"human_confirmation_required": false` wins over nearby headings.

## Task Output / UI State

- Task Output prefers `task_status.steps`, `task_status.production.jobs`, `task_status.assets`, `task_status.allowed_actions`, and `task_status.diagnostics`.
- Diagnostics render above production jobs and should include real blockers before export/review suggestions.
- Missing ComfyUI slot diagnostics include the raw `workflow_id / mode` and user-facing debug-console path.
- Missing slot rows expose a `去配置` action that jumps to the exact ComfyUI debug submode.
- `pending` means waiting, not running.
- `skipped` is its own visible state.
- Final completion requires an actual final/video MP4 or video asset, not just employee text completion or `final_output.md`.
- Cancel must work for running, queued, paused, awaiting-confirmation, or blocked tasks, including after browser refresh or service restart when only `task_name` is available.
- New task startup should switch immediately to Task Output and keep the pending placeholder until the backend reports the real `task_name`.

## Current Repo Notes

- As of this compaction, the only observed working-tree change was `my_workspace/my_asset_library/library.json`.
- Treat `library.json` as local asset-library state unless the user explicitly asks to commit asset metadata.
- `my_workspace/my_task_output` currently only contains `__comfy_debug__`; no formal long-video task output was present during the last inspection.

## Recommended Next Step

Run a minimal end-to-end long-video smoke test after restarting the management UI:

1. Use a short 10-15 second single-character requirement.
2. Prefer vertical 9:16 unless testing another aspect ratio.
3. Let the workflow reach the real blocker instead of adding speculative fixes.
4. If it fails, inspect Task State Center diagnostics, `production_manifest.json`, `production_job_state.json`, and the relevant adapter manifest.
5. Fix the real blocker, run focused tests, commit/push code changes, and restart the service when functionality changes.

## Verification Habits

- For Python syntax: `python -m compileall my_workspace`
- For focused logic: run the relevant tests under `my_workspace/tests/`
- For frontend JS embedded in `web_app.py`, extract/check the changed script when practical.
- For config-heavy fixes, validate JSON files after editing.
- Do not commit generated task outputs, uploaded references, voice samples, API keys, local model files, or large media assets.
