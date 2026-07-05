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
- `compose_config.tool == "runninghub"` in `comfy_full` means RunningHub is the visual material provider; it must still allow local FFmpeg final composition.
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
- Production-output validation treats short-video platforms/requirements (`短视频`, `抖音`, `快手`, `小红书`) as portrait `9:16` by default unless the user explicitly says `16:9`, landscape, or horizontal.
- Delivery dimensions are normalized separately:
  - 16:9 -> `1920x1080`
  - 9:16 -> `1080x1920`
  - 1:1 -> `1080x1080`
- Global frame rate is 24fps unless the locked render context says otherwise.
- First/middle/last video clips are locked to 4 seconds / 24fps.
- Staff should reference entities and intents, not override locked face/hair/outfit/style/resolution/FPS values.
- Scene consistency is managed through the production entity scene library. Scene entities should use `scene_id`, `scene_master_image`, `scene_description`, `fixed_layout`, `lighting`, `camera_allowed_changes`, and `forbidden_changes`; `scene_reference` remains a backward-compatible alias for the master scene image. Staff should reference `scene_id` instead of restating or reinventing the location every shot.

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
- `04_keyframe/img2img_style_keyframe` is a separate stabilization-phase submode for reference-image-based keyframes that should preserve source subject/composition. Its active nodeInfoList is calibrated to the user-provided Qwen Image Edit 2511 img2img workflow `图生图风格关键帧.json`, mapping `LoadImage(2)`, `PrimitiveStringMultiline(34)`, shortest-side `Int(8)`, `easy seed(27)`, negative `TextEncodeQwenImageEditPlus(3)`, `KSampler(24)`, and final `SaveImage(48)`. Prompt, negative prompt, input image, shortest side, seed, and denoise values must use runtime payload placeholders (`{{prompt}}`, `{{negative_prompt}}`, `{{input_base_image}}`, `{{short_side}}`, `{{seed}}`, `{{denoise}}`) rather than fixed workflow defaults. `denoise` is decided by staff/production intents; if not provided, this mode now defaults to `denoise=1`. The debug console does not expose a manual denoise control.
- ComfyUI debug reference uploads support common JPEG variants including `.jfif`, `.pjpeg`, and `.pjp`. The debug console shows an immediate local object-URL preview while upload/normalization is pending, then switches to the stored reference path returned by the backend.
- Debug-console mode configs should fall back to the mode-specific default nodeInfoList when local saved state is empty or `[]`. This keeps `identity_keyframe` and related keyframe submodes prefilled from their paired nodeInfo files even if an older local config stored a blank mapping.
- Staff 06 may output `characters[]` for multi-person `generate_keyframe` intents. The compiler resolves each character to independent identity assets and routes 2-4 person shots to the multi-character keyframe modes.
- RunningHub nodeInfo placeholders include `{{character_references}}`, `{{character_reference_1}}` through `{{character_reference_4}}`, `{{character_id_1}}` through `{{character_id_4}}`, and matching position placeholders.
- Turnaround/three-view results are auto-stitched into `*_turnaround_sheet.png`. The stitched sheet is first in `downloaded_files` and recorded in manifest metadata.
- The stitched sheet uses adaptive background sampling and asymmetric layout so identity/pose keyframe models can consume a coherent reference sheet.
- Animal protagonist consistency policy:
  - Do not route animal character reference sheets to the humanoid `02_turnaround / character_turnaround` workflow, because that slot is calibrated around human skeleton/standing-pose structure and can distort four-legged anatomy.
  - Animal `generate_base_asset` prompts that look like three-view/model-sheet requests stay on `01_base_asset_image / character_base`; the compiler appends animal anatomy constraints such as four-legged structure, no humanoid skeleton, no human standing pose, and same animal front/side/back views in one image.
  - Animal expression/emotion sheets after a same-character reference sheet are routed to `04_keyframe / img2img_style_keyframe` with `input_base_image` bound to the previous character reference job, production-controlled `denoise`, and prompt constraints that preserve fur pattern, ears, eyes, body ratio, tail, and species.
  - This is a production-routing stability rule, not a debug-console taxonomy change. It can be folded into a cleaner animal-character module after output quality stabilizes.
- Human character expression/emotion sheets also route to `04_keyframe / img2img_style_keyframe` when a previous same-character reference job exists. This prevents expression assets from becoming unrelated modern portrait photos; face shape, age, hair, skin tone, body ratio, and outfit should stay locked while only expression/micro-action changes.
- When a linked character entity already has `master_image`, character `generate_base_asset` variants route to `04_keyframe / img2img_style_keyframe` with `input_base_image` bound directly to that master image. Do not start first expression/state variants from pure text-to-image, because the first generated image can drift before later consistency controls take over.
- When staff image intents explicitly carry linked asset paths in `entity_usage.character_reference_image` / `character_master_image`, character keyframes and `generate_three_frame_shot` frames route to `04_keyframe / img2img_style_keyframe` with `input_base_image` bound to that linked master. If `entity_usage.scene_reference_image` is present, keep it on the compiled item as `input_scene_image` / `scene_reference_image` for workflows that can consume scene references. Environment-only B-roll without a character remains text keyframe generation.
- The production compiler also reads the original task `input.md` and extracts the structured `linked_assets` block appended by the new-task UI. These linked characters/scenes are merged into the transient entity registry for that task, so staff only needs to output `character_id` / `scene_id`; it does not need to copy full master image paths into every `entity_usage` object.
- When staff emits multiple base assets for the same `character_id`, only the first one is treated as the master identity. Later same-character variants are compiled as reference-driven `img2img_style_keyframe` jobs against the master. If a keyframe prompt says `主角`/`主人公`/`protagonist` but omits `character_id`, the compiler binds the unique previous character master through the configured `img2img_style_keyframe` slot instead of unconfigured identity-only slots.
- If staff splits one protagonist into state IDs such as `char_main_loser` and `char_main_winner`, prompts like `与char_main_loser同一面容` bind the later state to the earlier character master. Multiple `char_main*`/`protagonist*` IDs are treated as one protagonist family for unlabeled protagonist keyframes.
- RunningHub style-reference prompts are environment-only: strip incidental human-appearance clauses and append an empty-scene/no-person constraint.
- LTX2.3 text-to-video routing uses the user's current node IDs:
  - prompt `73.text`
  - negative prompt `25.text`
  - dimensions `43/44.value`
  - duration `74.value`
  - FPS `20/21.value` plus `40.frame_rate`
  - seeds `28/46.noise_seed`
- LTX2.3 first-frame image-to-video (`06_i2v_first_frame / i2v_first_frame`) uses the user's optimized canvas from `ltx2.3首帧生视频优化版 (1).json`. Its nodeInfo must map form values to: prompt `177.text` and optional LLM prompt `178.prompt`, negative prompt `182.text`, first frame `193.image`, longest-edge resize `186.value={{long_side}}`, duration `192.value`, FPS `154.value` and `231.fps`, seeds `155/156.noise_seed`, and output prefix `232.filename_prefix`. Keep `158.value=false`, `216.value=false`, `195.bypass=false`, and `197.bypass=false` so the uploaded first-frame image and raw production prompt drive the output; preserve optimized strengths `195.strength=0.7` and `197.strength=1`.
- `10_broll_transition_video` preserves explicit subtype: `broll_scene_video` vs `empty_transition_video`.
- B-roll clips are environment-only. If a `generate_broll_clip` intent includes a locked character ID, character name, or alias, the compiler clears `character_id`, removes known character terms from the prompt, and appends a no-visible-character/no-new-character constraint. Any shot where the protagonist must appear should be produced as keyframe + image-to-video instead of text-to-video B-roll.
- The adapter repairs legacy `10_broll_transition_video / broll_scene_video` nodeInfo rows that still point at old `2483/2612/3059` nodes, replacing them with the current LTX2.3 text-to-video nodeInfo so saved runtime configs do not break B-roll generation.

## Audio / Subtitle / FFmpeg State

- Local TTS retry maps aliases such as `tts`, `bgm`, and `ffmpeg` back to canonical packaging nodes: `local_tts`, `bgm_select`, `ffmpeg_compose`.
- Existing tasks can self-heal stale `local_tts` state from a durable successful `local_tts_manifest.json` plus existing WAV.
- FFmpeg aligns continuous narration to multi-entry subtitle/shot timing with silence midpoint detection and per-segment tempo adjustment.
- Subtitle SRT quality rejects overloaded entries before packaging fallback. If alignment would require an excessive narration tempo change, FFmpeg keeps the natural TTS timing instead of forcing compressed speech.
- Burned Chinese subtitles use `subtitles_burn.srt` with punctuation-aware line breaks. Source sidecar SRT remains unchanged.
- FFmpeg pads short narration instead of truncating the visual timeline. BGM/voice mixes use longest-authoritative audio where appropriate.
- For video-clip concat, local FFmpeg reads the target duration from manifest/compose config and pads a short visual timeline by cloning the final frame with `tpad`, so a 60-second vertical task is not delivered as a materially shorter draft when clips total less than the requested duration.
- Final MP4 defaults use delivery-oriented H.264: x264 `medium`, CRF 26, max rate 3 Mbps, buffer 6 Mbps, AAC 128 kbps, fast-start. These can be overridden through `compose_config`.

## Recovery / Retry Rules

- `run_auto_production(..., prepare_only=True)` compiles package/manifest without invoking RunningHub, TTS, BGM, or FFmpeg.
- Visual quality retry should reuse completed outputs, isolate failing/duplicate jobs, expand to dependent downstream jobs, and change seeds only for retried jobs.
- Visual preflight treats repeated use of the same i2v first frame as a warning, not a blocker. Story sequences may legitimately generate multiple motion clips from one keyframe; downstream quality checks should catch actual duplicate/bad outputs.
- Visual content QC also downgrades duplicate video first frames to warnings when the clips share the same upstream keyframe dependency. Reusing one first frame for multiple i2v motions is valid for story continuity; unrelated duplicate first frames remain blocking errors.
- Each RunningHub job writes `runninghub_task_state.json` with request fingerprint and taskId so retries/service restarts can query/download existing results instead of submitting duplicates.
- Material retry promotes the best legacy `attempt_XX/production_job_state.json` into the stable root cache when needed.
- Existing-task material retry must preserve non-empty ComfyUI/RunningHub credentials and base URLs already stored in task production config when the retry payload sends blank overrides.
- Optional video enhancement slots should be skipped rather than blocking final composition when unconfigured.
- `09_talking_image / talking_image` is currently treated as optional when unconfigured. It can enhance lip-sync/口播 shots after calibration, but the long-video pipeline should still complete with ordinary visual clips plus local narration/subtitles when that slot is empty.
- Optional `talking_image` jobs must not force the early TTS/WAV injection gate. Only non-optional talking-image jobs should block material generation while waiting for `input_audio_file`.
- Packaging dependency checks should also downgrade stale blocked `talking_image` visual nodes to skipped when the mode is optional, so old manifests do not keep blocking FFmpeg after the real `clip_*` talking-image job has been skipped.
- Multi-character keyframe routing must require concrete identity images. If staff outputs `characters[]` but no identity assets can be resolved, compile the shot back to the configured text-only `04_keyframe / keyframe` route instead of blocking on unconfigured multi-identity modes.

## Validation / Guardrails

- Requirement locking writes `task_brief.json` with original requirement, core topic, duration, style, structure constraints, and confirmation policy.
- Steps 1-3 receive only the compact original requirement, not raw asset-library or ComfyUI config noise.
- Model outputs are checked for topic retention, duration/structure coverage, ungrounded drift, and inappropriate confirmation blockers.
- Topic-retention checks allow a narrow paraphrase pattern for the 2008 rebirth/business-opportunity story: if the locked topic is about a lifetime of work, missed wealth waves, and regret, outputs mentioning a worker/protagonist returning to or reborn in 2008 plus reversal/opportunity/business concepts count as on-topic even without repeating the full original sentence.
- 2008/retro/live-action visual guardrails are not global live-action constraints. Only add the 2008-era street-detail / old-signage / retro-period prompt additions when the requirement or style explicitly mentions 2008, retro, vintage, period, nostalgic, or similar era cues. Plain modern live-action prompts must not inherit those test-task constraints.
- Only decisions affecting theme, platform specs, brand/product, budget, identity, copyright/compliance, or final delivery may require human confirmation.
- Employee production-output validation is active for 03/06/20/07/22. Failed validation gets one automatic correction retry, then fails visibly.
- Validation checks include:
  - voice text fits target duration
  - image/video intents parse as one JSON object; standalone `//` or `/* */` comments inside fenced JSON blocks are stripped before parsing because staff occasionally emits commented JSON.
  - 480p working dimensions match aspect ratio
  - video references resolve to real 06 intent IDs
  - first/middle/last video compatibility rows may rely on authoritative `production_intents.video.source_intent_ids`; they do not need to duplicate all three frame references in legacy `video_prompts`.
  - first/middle/last clips stay 4 seconds / 24fps
  - subtitle timestamps and coverage are sane
  - final edit timeline and missing-assets state are internally consistent
- `production_contract_validation.json` records validation details.
- Human-confirmation detection accepts quoted JSON keys/values and Markdown-style assignments. Explicit `"human_confirmation_required": false` wins over nearby headings.

## Task Output / UI State

- Task Output prefers `task_status.steps`, `task_status.production.jobs`, `task_status.assets`, `task_status.allowed_actions`, and `task_status.diagnostics`.
- Task Output and asset-library media endpoints support HTTP byte-range requests so browser video/audio previews can seek instead of downloading the whole file as a single 200 response.
- Generated asset favorite state is keyed by normalized `source_task/source_task_id + source_file`. Task asset payloads include `favorited` and `library_asset_id` when a generated file is already in the asset library, so UI badges and lightbox favorite buttons should not infer state from display paths alone.
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
