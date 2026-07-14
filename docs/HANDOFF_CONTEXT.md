# Handoff Context

Last compacted: 2026-07-13

## 2026-07-14 Vlog Topic Validation Repair

- The resumed task `task_20260714_194916_小美的内衣试穿vlog_竖屏1分钟长视频` stopped at employee 03 before production because the topic guard treated the format word `vlog` as a mandatory spoken English topic token. The script explicitly covered 小美, 内衣, and 试穿, and its production contract passed, but it did not say the word `vlog`.
- In a mixed Chinese/Latin topic, `vlog` is now treated as a format marker rather than mandatory spoken subject matter. Other semantic Latin tokens remain mandatory; a topic containing only `vlog` still requires that token.
- When a format marker is ignored, Chinese bigram coverage uses a stronger threshold. The real underwear try-on script passes, while an unrelated 小美田径训练 script remains blocked. No employee output is rewritten or backfilled.
- Verification: 198 semantic-contract tests, the complete rejected employee-03 output regression, `python -m compileall -q my_workspace`, and `git diff --check` passed. No paid production job or automatic resume was run.

## 2026-07-14 Nested Visual Control Contract Repair

- The failed task `task_20260714_190103_小美的内衣试穿vlog_竖屏1分钟长视频` stopped at employee 06 before any paid visual jobs were submitted. Employee 06 had correctly copied `face_visibility`, `outfit_state_id`, and `text_policy` into each image intent's `constraints` object, but the validator and compiler only read those fields from the intent top level.
- Visual validation and compilation now accept the three explicit controls from either the canonical `constraints` object or the legacy top level. The compiler promotes the selected values into standard image and video job fields; it does not infer values from prompt prose.
- A `generate_base_asset` with `asset_role=scene` is now recognized as an explicit scene anchor, matching the compiler's existing scene-role vocabulary. Other accepted explicit aliases remain `scene_base`, `scene_reference`, `background`, `bg`, `environment`, `location`, and `set`.
- Employee 06 documentation identifies `constraints` as the canonical location and retains top-level compatibility. No hidden field backfill, scene guessing, paid retry, or production resume was added.
- Verification: 197 semantic-contract tests, real rejected employee-06 output regression, `python -m compileall -q my_workspace`, and `git diff --check` passed.

## 2026-07-14 Editor-Owned Subtitle Timing

- Employee 20 now owns the complete voice/subtitle draft, not final edit timing. A subtitle draft ending after the target duration produces a contract warning and may continue to employee 22; employee 20 must not delete or rewrite employee 03 narration to force a fit.
- When an upstream subtitle draft overruns, employee 22 must explicitly set `build_edit_timeline.subtitle_edit.policy` to `retime`, `trim`, or `disable`. `retime` and `trim` also require `target_end_seconds` within the final duration. Missing or invalid decisions fail visibly.
- Production applies only the explicit employee-22 decision: `retime` proportionally rescales all SRT timestamps, `trim` removes/caps entries at the selected endpoint, and `disable` omits subtitles. The backend does not choose a policy or silently repair timing.
- The rejected 80-second subtitle output from `task_20260714_072738_小美的瑜伽训练日记_竖屏1分钟长视频` now passes employee-20 validation with a warning and is ready to be regenerated/resumed from step 5; no paid production retry was run.
- Verification: 196 semantic-contract tests, real rejected-output regression, `python -m compileall -q my_workspace`, and `git diff --check` passed.

## 2026-07-14 Visual Contract And Review Gate

- Employee 22 edit timelines now validate every detailed `source_intent_id` and compact `clip_id` against the exact video intent IDs emitted by employee 07. Unresolved references fail visibly, and `all_assets_ready=true` cannot override missing or invalid references.
- Ordinary `generate_i2v_clip` accepts exactly one upstream image. Multiple images fail validation and compilation instead of silently binding only the first; multi-action work must be split or use an explicitly supported multi-frame intent.
- Character image intents now require structured `face_visibility`, `outfit_state_id`, and `text_policy` fields. Reused `scene_id` values require an explicit scene master/reference or a `generate_base_asset` scene anchor. These fields are preserved in compiled image/video jobs.
- Visual quality execution performs one paid provider attempt only. Deterministic media failures become `blocked`; face presence, OCR, duplicate, and low-motion findings become `review_required`. No automatic quality retry is issued. The quality report lists targeted job IDs, and task state exposes only explicit user-triggered retries.
- Face detection runs only when `face_visibility=required`; back/feet/distant shots marked `not_visible` do not receive frontal-face errors. OCR and face findings are review warnings rather than hard identity claims. Low-motion review uses a perceptual hash distance threshold of 10.
- QC writes `keyframes_contact_sheet.jpg` and `video_midframes_contact_sheet.jpg` beside `visual_content_qc.json`, with job ID, actual route, issue labels, and the bound identity reference immediately before character results when that reference is available locally.
- System audio `mode=off` now removes TTS from the packaging graph and FFmpeg dependencies even when employee script text exists. Manual FFmpeg retry also follows the current system audio mode.
- Staff 23/06/07/22 contracts document exact field ownership, scene anchoring, single-source I2V, exact timeline IDs, runtime readiness authority, and audio-off behavior.
- Verification: 193 semantic-contract tests, `python -m compileall -q my_workspace`, and `git diff --check` passed before final commit.

## 2026-07-14 System Production Config Authority

- The backend now persists a secret-free current system production config at `tmp/web_runtime_production_config.json`. RunningHub and CosyVoice credentials remain in their dedicated runtime credential files and are injected only when the current config explicitly selects those providers.
- New tasks, resume, production retry, and task-scoped ComfyUI debug now use the backend current system config. Execution requests can no longer replace voice mode, production mode, workflow slots, endpoints, or node mappings with request-body values.
- Old task snapshots and manifests are audit records only. Resume/retry no longer merges their old voice/workflow values into the current configuration.
- `off`/`package_only` continue to control automatic production only. Explicit material retry and task-scoped ComfyUI debug may run with the current configured visual provider/workflow slot; they do not enable TTS or change the saved automatic mode. TTS retry checks only the current audio configuration and fails explicitly when audio is `off`.
- Task-scoped ComfyUI debug requires the exact current workflow ID/mode slot, endpoint, and node mapping. It does not use request-body workflow overrides, task snapshots, or built-in endpoint substitutions when the current slot is missing.
- Visual settings have one owner: `web_runtime_comfy_config.json`. The backend overlays its current provider, endpoint, node mapping, and workflow library onto the production config at execution time; the frontend also completes ComfyUI sync before constructing the production config, preventing stale blank/old workflow slots.
- On page load, the backend runtime workflow library replaces stale browser-local workflow values. Automatic ComfyUI POST sync stays disabled until initialization finishes, and cached debug endpoint/node fields are refreshed from the backend while run history and form inputs are preserved. The validated first-frame slot is `06_i2v_first_frame / i2v_first_frame` using RunningHub workflow `2069607607387639810` and its matching `2483/2612/2004/4981/4979/4978/4814/4977/4823` node mapping.
- The frontend waits for RunningHub/CosyVoice credential persistence, then saves the complete current production config before starting, resuming, or retrying work.
- Verification: 188 semantic-contract tests, embedded frontend JavaScript syntax validation, `python -m compileall -q my_workspace`, and `git diff --check` passed.

## 2026-07-14 Strict Production Input Contracts

- Production packaging now requires non-empty outputs from employees `01_`, `06_`, `07_`, `20_`, and `22_`. Missing outputs fail explicitly instead of creating placeholder prompt, voice, subtitle, or edit-plan files.
- Employee `20_` voiceover and subtitle data are read only from the validated JSON contract. Invalid/missing voice text or SRT now fails explicitly; the backend no longer rebuilds SRT from voice text or writes default placeholder narration/subtitles. Explicit `enabled=false` / `status=disabled` remains the only supported no-voice/no-subtitle path.
- ComfyUI employee JSON and persisted payload files are strict. Invalid JSON is never salvaged with regex extraction or replaced by a default payload. Raw JSON objects and one or more fenced `json` blocks remain supported, including standalone `//` and block comments as specified by the employee contract.
- Removed semantic route guessing that matched generated scene references from prompt keywords/fuzzy scene IDs, bound cross-ID character variants from prompt prose, or tried an implicit `_start_frame` suffix for missing I2V sources. Scene references now require one exact `scene_id`; ambiguity fails. I2V requires an exact upstream image intent ID.
- Employee `01_` must provide one of the supported `production_type` values. The compiler no longer guesses a production route from product/story/avatar keywords. Missing, malformed, or incomplete production-template files now fail explicitly instead of creating built-in defaults or switching to the `custom` template.
- Visual preflight now reads the final compiled ComfyUI payload as its sole authority instead of failing against a stale config copy and then switching sources.
- The real task `task_20260713_215010_小美的田径训练日记_竖屏1分钟长视频` remains valid under the strict parser: 314 narration characters, 11 valid SRT entries, one valid image JSON object, one valid video JSON object, and 42 compiled visual jobs.
- Verification: 181 semantic-contract tests passed.

## 2026-07-14 CosyVoice Retry Diagnosis

- The latest retry of `task_20260713_215010_小美的田径训练日记_竖屏1分钟长视频` failed at `local_tts`, not in the visual workflow. Its saved snapshot contained 314 usable narration characters but still had `voice_config.mode=off` and an empty provider, so the explicit error was `TTS provider is not configured`; FFmpeg remained blocked and did not create a silent final video.
- The selected clone `cosyvoice-v3-flash-myvoice-4e9822dcbccb402b98ba52b7515b7203` is saved with workspace `ws-kih2cydzfvfpb7ag`, region `cn-beijing`, and target model `cosyvoice-v3-flash`. When the user explicitly selects the Aliyun provider, new tasks, resume, production retry, and audio debug now hydrate those clone-owned metadata fields from the saved clone record. This does not turn on TTS when `mode=off`, select another voice, or supply an API key.
- A real retry still requires the system configuration to be explicitly set to Aliyun CosyVoice and a valid DashScope API Key. The existing OSS `LTAI...` AccessKey is not a DashScope API Key and must not be substituted.
- Verification: 169 semantic-contract tests, `python -m compileall -q my_workspace`, and `git diff --check` passed.

## 2026-07-14 Runtime Voice Credential Persistence

- DashScope/CosyVoice credentials now have a backend runtime cache at `tmp/web_runtime_voice_config.json`, matching the existing runtime model and RunningHub credential pattern. `/api/config` and `/api/runtime-voice-config` expose only `has_api_key`; they never return the saved key.
- The saved key is injected only when the incoming voice configuration already explicitly selects `aliyun_cosyvoice`/`cosyvoice`. `mode=off`, another provider, or a missing voice configuration remains unchanged. This is credential reuse, not provider selection or fallback.
- New tasks, resume, production retry, and audio debug all use the same explicit-only injection path. Successful voice cloning saves the valid key for later synthesis; clone deletion may reuse it.
- Production config snapshots already remove `aliyun_api_key` and every key ending in `_api_key`, so runtime injection does not leak credentials into task output.
- Verification: 171 semantic-contract tests, `python -m compileall -q my_workspace`, inline browser-script syntax validation, and `git diff --check` passed.

## 2026-07-14 CosyVoice Hidden Fallback Removal

- Removed the automatic duration-overrun retry that silently increased CosyVoice speech rate and issued a second paid request. An overlong result now becomes `quality_failed` after the single selected request and tells the user to adjust the configured rate before an explicit retry.
- Removed the silent `cosyvoice-v3-flash` to `cosyvoice-v1` downgrade when Workspace ID is missing. Invalid V3 configuration now fails visibly.
- Removed invalid V1 voice substitution to `longxiaochun`. A missing or unsupported configured voice now fails visibly instead of synthesizing with a different voice.
- A mocked end-to-end contract test verifies runtime credential injection, clone metadata hydration, the V3 Workspace endpoint, selected model/voice, audio file creation, and secret-free manifests. Additional tests prove no model downgrade, no default-voice substitution, and no automatic duration retry.
- Verification: 174 semantic-contract tests, `python -m compileall -q my_workspace`, and `git diff --check` passed.

## 2026-07-14 LTX I2V Production Repair

- The old first-frame I2V RunningHub workflow `2071735603636563970` failed at `LTX2_NAG(238)` because its GGUF model weights were dimension `4096` while the active connector expected `3840`.
- The validated first-frame I2V workflow is now `2069607607387639810`. It uses the non-GGUF LTX 2.3 canvas and preserves the raw staff motion prompt plus the explicit upstream first frame.
- Material retry `c62cafacb2974d489bf7efa279734dbb` completed for `task_20260713_215010_小美的田径训练日记_竖屏1分钟长视频`: 39 jobs succeeded, 0 failed. All 12 character I2V clips were regenerated successfully through `2069607607387639810`; `clip_003_selfie_talk`, `clip_016_cta_talk`, and `enhance_all_clips` remained explicitly skipped because those optional slots were unconfigured.
- Generated I2V files were verified as distinct H.264 MP4 files at 24fps and about 4.042 seconds each. The new workflow outputs `576x1024`; the two existing environment clips remain `448x832` and are normalized during final composition.
- Visual review shows that the new I2V workflow follows its supplied first frame. Remaining character/clothing inconsistency comes from the previously generated upstream keyframes, not from prompt replacement or reuse of one output video.
- Voice-provider fallback was removed. A narration requirement no longer silently enables VoxCPM2 when system audio is off, and VoxCPM2 failure/timeout no longer switches to Windows SAPI. The selected provider now returns its own visible failure and stops.
- Visual-only FFmpeg composition is no longer allowed when usable employee voiceover text exists. A missing/disabled TTS provider remains a required `local_tts` dependency, returns a visible failure, and blocks FFmpeg instead of producing a silent final MP4. Visual-only composition remains valid only when there is no usable voiceover text.
- Real-task regression on `task_20260713_215010_小美的田径训练日记_竖屏1分钟长视频`: TTS retry now returns `local_tts_failed / TTS provider is not configured`; FFmpeg retry returns `ffmpeg_dependency_blocked / local_tts: not_configured`; no final MP4 is produced.
- The task contains 314 usable narration characters and the selected cloned voice record exists, but the task snapshot is `mode=off` and there is no DashScope/CosyVoice API Key in task config, runtime config, process/user/machine environment, or saved debug manifests. A real CosyVoice retry requires the user's DashScope API Key; do not substitute an old debug MP3 or another provider.
- Verification: `python -m compileall -q my_workspace`, 167 semantic-contract tests, focused no-TTS-fallback tests, and the real-task TTS/FFmpeg regressions passed.

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

## 2026-07-13 Workflow Validation Repair

- Generic topic-word validation is limited to content-authoring roles: `01_`, `03_`, `23_`, and `04_`. Delivery duration/aspect validation is limited to `01_`, `03_`, and `23_`.
- Technical transformation/review roles `06_`, `20_`, `07_`, `05_`, and `22_` are not required to repeat title words. They are validated against their structured role contracts and upstream outputs.
- `20_语音字幕包装师` must copy the exact TTS plain text from `03_口播脚本师` into both `generate_voiceover.voice_text` and `audio_package.voiceover_text`. Whitespace-only differences are allowed; creative rewriting fails with source `员工岗位输出契约`.
- The original requirement's delivery suffix is removed from `core_topic`, so `小美的田径训练日记，竖屏1分钟` locks topic `小美的田径训练日记`, duration `60`, and portrait delivery separately.
- Employee prompts receive role-scoped context. `06_` may see linked assets/reference images; `07_`/`20_`/`22_` may see explicitly selected long-term memory. Runtime ComfyUI/image/video configuration is not injected as employee prose.
- Empty memory templates are not injected. Shipped character/style templates contain no default aspect ratio, generic identity restrictions, or generic negative prompts.
- `06_` translates the visual decisions owned by `23_`; it must not invent age, skin tone, face, hair, clothing, or visual style. `07_` receives validation errors for invalid duration/FPS and may not silently downgrade missing three-frame work to first-frame I2V.
- `20_` must not invent TTS engines, voice names, clone IDs, speed, or pitch. Runtime generation uses the selected system audio configuration.
- The active long-video workflow requires only the topic/product information. Platform, audience, duration, purpose, available assets, and restrictions are optional inputs and remain `未指定` when omitted.
- Verification on 2026-07-13: `python -m compileall -q my_workspace`, 157 semantic-contract tests, JSON validation, and the rejected real task `task_20260713_215010_*` step 20 regression all passed.
- Production-output parsing accepts either one strict raw JSON object or one/more fenced `json` blocks, matching the staff output contract. It does not extract or repair JSON embedded in mixed prose.
- Resuming an older task refreshes `video_memory_context` from the current `my_memory` files when that scope was enabled. Stale template text saved in an old `production_config_snapshot.json` is not reused.
- `07_视频生成执行员` may use `generate_broll_clip` only for environment/object shots with no visible person or body part. Character names/IDs, identity locks, or visible body markers in B-roll fail employee-output validation; the employee must emit `generate_i2v_clip` with an explicit upstream character image.
- The production compiler no longer promotes character B-roll to I2V. It raises an explicit classification error if invalid B-roll reaches compilation, so the backend does not silently change the employee's route or create a replacement creative route.
- `23_长视频策划编导` must classify visible body close-ups as character shots, and `06_分镜生图设计师` must produce an explicit character keyframe for every visible person/body-part shot. A negative clause such as `无人物出现` remains valid for environment/object B-roll.
- `23_长视频策划编导` has an explicit delivery-constraints section and must repeat user-specified duration and aspect/orientation before writing the shot plan.
- Talking-image `source_intent_ids` may contain both an upstream character image and an audio intent such as `voiceover_main`; employee validation checks the image dependency without misclassifying the audio dependency as an image.
- Distant silhouettes, tiny people, and character-representing light dots still count as character shots. They require a character keyframe and may not be classified as environment B-roll.
- I2V intents and legacy I2V prompts must reference an existing upstream image explicitly. The compiler no longer guesses a same-numbered keyframe or generates/restores a missing keyframe; missing or dangling image references fail compilation.

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
- User preference: do not add hidden fallback/downgrade logic without asking first. For visual identity, keyframes, cover images, character/scene bindings, and material reuse, prefer explicit failure with a clear diagnostic over silently skipping, downgrading, or reusing stale assets.
- Backend-authored natural-language prompt constraints are also hidden fallback behavior. Preserve employee image/video prompt text as authoritative through both compilation and provider adaptation: do not append inferred style consistency prose, no-text clauses, identity narratives, era-specific quality clauses, scene-layout prose, safety prefixes/negative terms, provider-specific semantic replacements, or test-specific wording. Keep identity/style/scene controls in structured workflow fields and fail validation when required employee intent is missing. Technical artifact/path cleanup may remove non-semantic transport tokens but must not invent creative direction.
- Employee output checks are read-only. Do not normalize or rewrite staff dimensions, delivery resolution, or edit-timeline duration before validation. Report the employee value and expected technical contract, then stop.
- `01_需求拆解专员` must not turn unspecified platform, audience, voice, style, clothing, character details, or quality mode into defaults. Use `未指定`; any necessary `production_type` judgment must be labeled as an employee decision in `routing_reason`, not presented as a user requirement.
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
- When the original locked requirement explicitly says `9:16`, vertical, or portrait, validation uses portrait working size `480x848` even if an upstream route JSON later mislabels `aspect_ratio` as `16:9`. The user's original delivery constraint wins over a staff routing typo.
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
  - identity+scene-reference keyframe
  - identity+pose keyframe
  - multi-character identity keyframe
  - multi-character identity+pose keyframe
- `04_keyframe/identity_scene_keyframe` is a stabilization-phase submode for shots that must keep both a linked character and a linked scene. It requires `input_identity_image` plus `input_scene_image`, uses `control_mode=identity_scene_reference`, and should be selected by production only when both references are resolved. Its default nodeInfo is calibrated to the user-provided Qwen Image Edit 2509 RoleScene Blend V2 workflow: role image `LoadImage(35)`, target scene image `LoadImage(22)`, prompt `TextEncodeQwenImageEditPlusCustom_lrzjason(21).prompt`, longest-edge controls `1/8/16/24`, control resize `ImageResizeKJ(10)`, main sampler `KSampler(12)` with runtime `{{denoise}}` defaulting to `1` when staff/debug payloads omit it, refinement sampler `KSampler(23)` with fixed `denoise=0.2`, and final single image `SaveImage(33)`. Do not send `25.filename_prefix` for the current RunningHub publication, because that compare-output node is not exposed in workflow `2073714895434117122` and causes `NODE_INFO_MISMATCH`.
- `04_keyframe/style_reference_keyframe` is intentionally exposed as a concrete debug-console submode for the current stabilization phase. It uses an SDXL IPAdapter Style Transfer img2img canvas from `04_keyframe_image/style_reference_keyframe_canvas.json`, with nodeInfo in `style_reference_keyframe_nodeinfo.json`. Later architecture work can collapse it into `04_keyframe/keyframe + controls.style_reference`.
- `04_keyframe/img2img_style_keyframe` is a separate stabilization-phase submode for reference-image-based keyframes that should preserve source subject/composition. Its active nodeInfoList is calibrated to the user-provided Qwen Image Edit 2511 img2img workflow `图生图风格关键帧.json`, mapping `LoadImage(2)`, `PrimitiveStringMultiline(34)`, shortest-side `Int(8)`, `easy seed(27)`, negative `TextEncodeQwenImageEditPlus(3)`, `KSampler(24)`, and final `SaveImage(48)`. Prompt, negative prompt, input image, shortest side, seed, and denoise values must use runtime payload placeholders (`{{prompt}}`, `{{negative_prompt}}`, `{{input_base_image}}`, `{{short_side}}`, `{{seed}}`, `{{denoise}}`) rather than fixed workflow defaults. `denoise` is decided by staff/production intents; if not provided, this mode now defaults to `denoise=1`. The debug console does not expose a manual denoise control.
- If `img2img_style_keyframe` RunningHub calls finish but return only `txt` from node `35` (`easy saveText`) and no media files, the RunningHub app was published with a text output instead of the image output. The workflow canvas can still contain `SaveImage(48)`, but RunningHub must expose/select that image output node for the API result; otherwise the adapter marks the run failed with a text-only-output diagnostic.
- Production reference-driven keyframes use a concise edit prompt for Qwen Image Edit. Keep verbose identity/scene constraints in structured plan metadata, but strip character/scene IDs, working-size text, generic safety prefixes, and long identity-lock prose before writing the prompt node. Character base assets and expression sheets keep their existing longer consistency prompts.
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
- When staff image intents explicitly carry linked asset paths in `entity_usage.character_reference_image` / `character_master_image`, character keyframes and `generate_three_frame_shot` frames route semantically to `04_keyframe / identity_keyframe` with both `input_identity_image` and `input_base_image` bound to that linked master. If `entity_usage.scene_reference_image` is also present, route the shot to `04_keyframe / identity_scene_keyframe` and keep the scene on `input_scene_image` / `scene_reference_image`. Environment-only B-roll without a character remains text keyframe generation.
- The production compiler also reads the original task `input.md` and extracts the structured `linked_assets` block appended by the new-task UI. These linked characters/scenes are merged into the transient entity registry for that task, so staff only needs to output `character_id` / `scene_id`; it does not need to copy full master image paths into every `entity_usage` object.
- When staff emits multiple base assets for the same `character_id`, only the first one is treated as the master identity. Later same-character asset variants are compiled as reference-driven `img2img_style_keyframe` jobs against the master. If a keyframe prompt says `主角`/`主人公`/`protagonist` but omits `character_id`, the compiler binds the unique previous character master through the configured `identity_keyframe` slot.
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
- Multi-character keyframe routing requires concrete identity images. If staff outputs `characters[]` but identity assets cannot be resolved, fail explicitly; do not downgrade to a text-only or single-character keyframe route.

## Validation / Guardrails

- Requirement locking writes `task_brief.json` with original requirement, core topic, duration, style, structure constraints, and confirmation policy.
- Steps 1-3 receive only the compact original requirement, not raw asset-library or ComfyUI config noise.
- `## 关联资产上下文` is a generated-context boundary. It must not be copied into `original_requirement` or repeated inside the early-step read-only requirement summary.
- Duration evidence accepts literal seconds/minutes, structured duration fields, plain second ranges, and `MM:SS` / `HH:MM:SS` storyboard ranges with common ASCII or Unicode dash characters. A timeline ending at `00:60` or `01:00` is valid evidence for a 60-second task.
- Validation failures carry `issue_details` with a visible source such as `用户明确要求`, `员工岗位输出契约`, or `生产接口技术契约`. Only real timeout exceptions may show model-timeout guidance; ordinary validation errors must not suggest increasing Ollama timeout.
- Model outputs are checked for topic retention, duration/structure coverage, ungrounded drift, and inappropriate confirmation blockers.
- Topic-retention checks allow a narrow paraphrase pattern for the 2008 rebirth/business-opportunity story: if the locked topic is about a lifetime of work, missed wealth waves, and regret, outputs mentioning a worker/protagonist returning to or reborn in 2008 plus reversal/opportunity/business concepts count as on-topic even without repeating the full original sentence.
- 2008/retro/live-action visual guardrails are not global live-action constraints. Only add the 2008-era street-detail / old-signage / retro-period prompt additions when the requirement or style explicitly mentions 2008, retro, vintage, period, nostalgic, or similar era cues. Plain modern live-action prompts must not inherit those test-task constraints.
- Only decisions affecting theme, platform specs, brand/product, budget, identity, copyright/compliance, or final delivery may require human confirmation.
- Employee production-output validation is active for 03/06/20/07/22. Failed validation fails visibly on the first invalid output; do not auto-normalize employee output, auto-retry, reuse rejected candidate outputs, or auto-backfill missing material intents without asking the user first.
- Validation checks include:
  - voice text fits target duration
  - image/video intents parse as one JSON object; standalone `//` or `/* */` comments inside fenced JSON blocks are stripped before parsing because staff occasionally emits commented JSON.
  - `20_语音字幕包装师` is constrained to return one parseable JSON object only, with no prose/self-check/action block outside the JSON. This avoids malformed `production_intents.audio` outputs that block automatic resume before material generation.
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
