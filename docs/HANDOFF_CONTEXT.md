# Handoff Context

Last compacted: 2026-07-13

## 2026-07-16 V2 Project Control Console Sprint 11

- Added read-only project control projections under `v2/backend/app/control/` and API routes for project summaries and detailed control evidence.
- The projection exposes both persisted Project status and a separately evaluated navigation stage; reads never mutate or commit project state.
- Blockers are classified from snapshot, WorkItem, QCReport, and DeliveryAttempt record types, never error-message keyword matching.
- Costs remain separated by currency and status. Actual routes come only from frozen WorkAttempt manifests and execution records.
- Replaced the static architecture homepage with an API-backed project console and added `/projects/:projectId/control` for blockers, costs, routes, and recent events.
- Added `docs/V2_PROJECT_CONTROL_IMPLEMENTATION.md` and updated product, data-model, and state-machine documents.
- Verification target: 28 backend tests, Python compileall, TypeScript/Vite build, desktop/mobile browser checks, push, and restart on `8766`.
- No provider call, retry, fallback, route substitution, state repair, or new state transition was introduced.

## 2026-07-16 V2 Final Delivery Contracts Sprint 10

- Added `Project.delivery_asset_id` and persisted `DeliveryAttempt` under Alembic revision `20260716_10`.
- Final delivery consumes exactly one current active-snapshot `confirmed` timeline. Explicit authorization freezes an immutable request manifest and fingerprint but starts no renderer, provider, WorkItem, WorkAttempt, or charged CostEvent.
- The first execution kind is `external_upload`. Multipart MP4 uploads are streamed to random temporary files with in-stream SHA-256 and size limits; every exit path removes temporary files.
- Uploaded files register as unverified `final_delivery` Assets. Deterministic verification re-reads the file and checks hash, byte size, MP4 signature, dimensions, duration, storage policy, current timeline, snapshot, and all input asset hashes.
- Passing verification marks the Asset `verified`, Timeline `exported`, and Project `completed`. Failure persists blocked QC evidence, archives only the failed final file, preserves the confirmed timeline, and creates no retry or second attempt.
- Integrated authorization, upload, verification, blocked evidence, and completed download into `/editor`; blocked and completed projects remain visible.
- Added `docs/V2_DELIVERY_IMPLEMENTATION.md` and updated product, data-model, and state-machine documents.
- Verification target: 26 backend tests, Python compileall, TypeScript/Vite build, fresh ten-revision migration, desktop/mobile browser checks, push, and restart on `8766`.
- Next slice must be designed before implementation. Do not add delivery retry, local rendering, provider execution, alternate upload paths, or unblock semantics without explicit user confirmation.

## 2026-07-16 V2 Timeline Editor Contracts Sprint 9

- Added persisted `Timeline` and `TimelineItem` records under Alembic revision `20260716_09`, including immutable version lineage, exact active-snapshot ownership, row-version guards, validation evidence, and contract hashes.
- Added explicit `ApproveQualityStage`, `CreateTimelineCandidate`, `ReviseTimelineCandidate`, `ValidateTimeline`, and `ConfirmTimeline` commands. Existing projects must revise a named version rather than create an unlinked parallel candidate.
- Timeline validation blocks unresolved gaps, unapproved/cross-project/cross-snapshot/wrong-type assets, unknown or exceeded source durations, undeclared speed changes, overlaps, main-track holes, output overruns, and disabled/empty enabled tracks. It never rewrites or repairs a candidate.
- `source=editor_assistant` requires an exact completed editor AgentRun from the same project. A source label alone has no authority.
- Confirmation requires the exact validated hash, row version, and explicit scope acknowledgement. It marks referenced assets `used`, supersedes the prior confirmed version, and moves the project to `delivery_ready`; it performs no export, provider call, retry, or cost event.
- Replaced `/editor` with an API-backed editor workspace covering quality-stage admission, approved asset bin, monitor, video/audio/subtitle tracks, explicit gaps, four time fields, immutable version history, structured validation findings, revision, and confirmation.
- Added `docs/V2_TIMELINE_EDITOR_IMPLEMENTATION.md` as the implementation contract. Verification target: 24 backend tests, Python compileall, TypeScript/Vite build, fresh nine-revision migration, desktop/mobile browser checks, push, and restart on `8766`.
- Next slice: DeliveryAttempt and final-delivery Asset contracts consuming only the current confirmed timeline. Do not invoke FFmpeg or providers until delivery authorization, idempotency, file verification, and retry semantics are explicit.

## 2026-07-16 V2 Asset Lifecycle And Quality Review Sprint 8

- Added persisted `Asset`, `QCReport`, `QCFinding`, and append-only `AssetReviewDecision` records under `v2/` with Alembic revision `20260716_08`.
- Provider outputs can be registered only from a completed WorkAttempt whose persisted response manifest explicitly has `media_created=true`, an exact output index, a matching response hash, and an output type matching the DAG contract. Mock responses cannot become assets.
- Asset verification reads the real local file, computes SHA-256, probes PNG/JPEG/WAV/MP4/SRT/JSON media facts, and compares the provider-declared hash/MIME. Missing, changed, corrupt, or mismatched files persist blocked QC evidence and archive the asset instead of leaving an ephemeral API error.
- The versioned `v2.file-contract.v1` ruleset checks deterministic dimensions, video duration, and references. Visual/audio content enters `review_required` while subjective analyzers are disconnected; it is never silently passed.
- Added explicit approve/reject commands with row-version guards, exact QC report binding, required rationale, actor audit, idempotent command receipts, and no retry or production call. Rejection archives only.
- Replaced the `/review` placeholder with an API-backed quality workspace showing real previews, hashes, dimensions, findings, downstream DAG impact, output gaps, and human-decision dialogs.
- Verification target: 21 backend tests, Python compileall, TypeScript/Vite build, fresh eight-revision migration, browser desktop/mobile checks, push, and restart on `8766`.
- Next slice: confirmed Timeline candidate/editor contracts that can reference only approved assets. Do not implement retry commands or real providers until retry cost confirmation and provider reconciliation contracts are designed.

## 2026-07-15 V2 Execution Authorization Sprint 7

- Added explicit `locked -> active -> submitted` production authorization. Activation changes the project's active snapshot only and creates zero WorkItems; submission independently revalidates contract hash, confirmed amount/currency, the exact complete DAG node list, and high-risk confirmation.
- Submission deterministically creates one unique WorkItem and one initial WorkAttempt per DAG node. Request fingerprints cover immutable snapshot, node, workflow, provider, adapter, and input/output contracts. Command replay is idempotent.
- Added dependency-aware Worker leasing. Only explicitly configured `adapter_kind=mock` nodes and local timeline-contract nodes execute. Mock responses set `media_created=false` and never create provider task IDs, assets, charged CostEvents, network requests, retries, or fallback routes.
- Unconnected real adapters block with `PROVIDER_ADAPTER_NOT_CONNECTED`; required descendants block with `DEPENDENCY_BLOCKED` rather than hanging. No second attempt is created.
- Replaced the obsolete production queue prototype with snapshot, WorkItem, WorkAttempt, fingerprint, progress, and blocker views. Plan review now has separate activation and high-risk submission controls.
- Stabilized configuration semantic diffs by sorting pricing rules by workflow key instead of random database IDs.
- Added Alembic revision `20260715_07`. Verification target: 17 backend tests, Python compileall, TypeScript/Vite build, fresh seven-revision migration, browser desktop/mobile checks, push, and restart on `8766`.
- Next slice: Asset persistence and lifecycle plus QC/review contracts. Do not connect real provider adapters until real output reconciliation, cost charging, and user-confirmed retry semantics are designed.

## 2026-07-15 V2 Pricing And Snapshot Lock Sprint 6

- Added versioned `PricingCatalogVersion` and exact `PricingRule` configuration components plus per-node pricing metadata and `CostEvent` audit rows under `v2/`.
- Pricing rules bind one exact workflow-slot version and support explicit `call` or `output_second` units, six-decimal calculation, and optional minimum charge. Missing rules or inapplicable units block analysis; there is no average price, zero-price assumption, or provider default.
- Production impact can explicitly select a pricing catalog from the same published production configuration. The backend calculates every provider-bound DAG node and persists the exact pricing rule, quantity, unit, amount, currency, and aggregate estimate.
- Added a separate high-risk snapshot lock command. It requires the current contract hash, exact expected amount, exact currency, and explicit confirmation. A stale hash, wrong amount/currency, unpublished/expired catalog, or node-total mismatch blocks locking.
- Successful locking changes only `preparing -> locked` and `cost_status=estimated -> confirmed`. It writes one confirmed `estimated` CostEvent per provider node; it does not represent a charge, create WorkItems, activate the snapshot, call a provider, or incur cost.
- Added price-catalog editing to `/settings`, including exact workflow rules, unit price, minimum charge, currency, effective window, and confirmation threshold. Existing configurations may remain without pricing but cannot lock new snapshots.
- Added price selection, six-decimal estimate display, and an independent cost-confirmation modal to the plan page. No configuration, workflow, video spec, or price catalog is auto-selected.
- Added Alembic revision `20260715_06`. Verification passed with 14 backend tests, Python compileall, TypeScript/Vite build, fresh six-revision migration, a complete temporary-browser flow through `locked / confirmed`, desktop/mobile layout checks, and no browser console warnings/errors. The temporary database and service were removed after verification.
- Next slice: explicit snapshot activation, deterministic WorkItem compilation from locked DAG nodes, execution leases/idempotency, and a no-provider Mock Worker path. Do not connect real provider adapters before activation and submission confirmation boundaries are implemented.

## 2026-07-15 V2 Production Preparation Sprint 5

- Added persisted `ProductionImpactAnalysis`, immutable `ProductionSnapshot`, `SnapshotEntityVersion`, `DAGNode`, and `DependencyEdge` contracts under `v2/` with Alembic revision `20260715_05`.
- Production routing is fully explicit: the user selects one published configuration version, one exact video-spec version, one keyframe workflow-slot version, one video workflow-slot version, and a TTS slot only when the confirmed plan uses voiceover. No first option is selected automatically.
- Impact analysis validates exact ownership and publication state, operation kinds, workflow/video-spec compatibility, shot durations, entity versions, plan activity, audio/TTS compatibility, and plan/config hashes. Errors remain visible and never trigger route substitution, ID guessing, prompt rewriting, or repair.
- The deterministic compiler creates one keyframe node and one I2V node per shot. Every ordinary I2V node has exactly one required `source_image` edge; audio-off plans create no TTS node. A final timeline-contract node depends on the exact generated clips.
- Production reads `audio_mode` and `aspect_ratio` from the confirmed `PlanVersion.creative_brief`, not mutable or stale project summary columns.
- Explicit scope confirmation creates an immutable `preparing` snapshot, exact configuration reference, entity-version freezes, DAG nodes, and dependency edges. There is no snapshot revise endpoint.
- Pricing is deliberately not inferred. Until a pricing catalog exists, snapshots keep `cost_status=not_configured`, `estimated_cost=null`, and `COST_ESTIMATE_REQUIRED`; they cannot lock, activate, create WorkItems, call providers, or incur cost.
- Added `/projects/{project_id}/production-preparation`, impact-analysis creation, and snapshot creation APIs plus the API-backed production preparation section on the plan page.
- Verification passed with 13 backend tests, Python compileall, TypeScript/Vite build, fresh five-revision migration, runtime migration at `20260715_05`, desktop/mobile browser checks, no page-level horizontal overflow, and no browser console warnings/errors.
- Next slice: versioned pricing catalog, deterministic cost calculation, separate high-risk cost confirmation, and `preparing -> locked`. Do not create WorkItems or provider submissions before that boundary is complete.

## 2026-07-15 V2 System Configuration Sprint 4

- Added the first persisted V2 system-configuration authority under `v2/`: production configuration versions and exact provider, model, workflow-slot, video-spec, audio, and storage-policy component versions.
- Configuration lifecycle is explicit: `draft -> validating -> validation_failed | ready -> published -> retired`. Published and retired versions are immutable; changes require cloning a new draft.
- Added strict create, revise, validate, publish, retire, clone, diff, reference, component-history, and workflow-slot-history APIs. Mutating commands require idempotent `command_id`; versioned commands also require `expected_row_version`.
- Command replay now rejects a `command_id` reused for another command type. Configuration diffs compare semantic component keys and values instead of database row IDs, so an unchanged clone reports no false high-risk change.
- Publishing requires explicit high-risk confirmation and a matching validated configuration hash. Retiring records reference impact. Neither action creates snapshots, work items, provider calls, or production cost.
- Provider and storage secrets are never accepted as configuration fields. The registry stores only `credential_ref`; provider base URLs reject embedded credentials, query strings, and fragments.
- Workflow validation checks exact provider capability, model and video-spec references, NodeInfoList completeness and duplicate bindings, audio/TTS compatibility, and storage requirements. Invalid values block publication and are never repaired or rerouted.
- Added the API-backed `/settings` page with version history, typed multi-component editing, NodeInfoList rows, validation reports, semantic diffs, references, configuration hashes, explicit publish/retire dialogs, and clone-to-draft behavior.
- Runtime configuration remains empty until the user creates and publishes a real configuration. No fake provider setup or hidden default was inserted.
- Added Alembic revision `20260715_04`. Verification passed with 11 backend tests, Python compileall, TypeScript/Vite production build, fresh and runtime migrations at `20260715_04`, API/frontend HTTP checks, and desktop/mobile browser inspection. The settings editor has no page-level horizontal overflow at 1440x900 or 390x844 and emits no browser console warnings/errors.
- Next slice: explicit published-config impact analysis, immutable `ProductionSnapshot`, and deterministic `DAGNode` / `DependencyEdge` compilation. Do not create WorkItems or paid submissions before the separate cost-confirmation boundary exists.

## 2026-07-15 V2 Creation Center Sprint 3

- Added the first planning vertical slice under `v2/`: an accepted `RequirementVersion` can produce a `CreativeBriefCandidate`, explicit user acceptance makes it available to the Director stage, and an accepted `ShotPlanCandidate` creates an immutable `PlanVersion` with persisted `Shot` rows.
- Added typed `Entity` and `EntityVersion` records. Explicit non-inspiration attachment bindings now resolve to a real confirmed entity version; uploads still create no identity, voice, outfit, scene, or product binding by themselves. Existing string-only bindings migrate to `legacy_unresolved` instead of being inferred or repaired.
- Creative and Director runs use audited `AgentInputManifest` and `AgentRun` records. Manifests store confirmed attachment-binding IDs and the exact confirmed entity-version references read by the candidate. The deterministic Mock Agents make no model, provider, workflow, storage, or paid production call.
- Creative Brief preserves confirmed topic, duration, aspect ratio, audio mode, and entity references. Optional visual style stays unspecified and assumptions stay empty. The Director proposes three structured shots whose durations exactly match the confirmed requirement; it does not select providers or workflow slots.
- Added `/api/v1/projects/{project_id}/planning-center` plus explicit generate, accept, and reject commands for Creative Brief and shot-plan candidates. Command receipts remain idempotent, stale requirement versions are rejected, and no candidate becomes authority without an explicit accept command.
- Added the API-backed `/projects/:projectId/plan` page with requirement/brief/plan version visibility, field provenance, candidate review controls, structured shot contracts, confirmed entity versions, the single backend-evaluated next action, and an explicit no-snapshot/no-cost boundary.
- Added Alembic revision `20260715_03`. Verification passed with 8 backend tests, Python compileall, TypeScript/Vite production build, a fresh three-revision migration test, a runtime upload -> entity binding -> brief -> shot plan -> `plan_v1` smoke flow, desktop/mobile browser checks, and no browser console errors. Mobile has no page-level horizontal overflow; the shot table scrolls only inside its own container.
- No ProductionSnapshot, WorkItem, provider call, workflow choice, paid retry, fallback, prompt rewrite, output repair, inferred binding, or hidden default was introduced.

## 2026-07-15 V2 System Configuration Design Baseline

- Expanded `docs/V2_PRODUCT_DESIGN.md` to v0.3 with a complete system-configuration product contract instead of creating another design document. It now defines configuration goals, nine configuration modules, model/provider/workflow-slot fields, NodeInfoList ownership, video/audio policies, management UX, confirmation levels, implementation order, API boundaries, and acceptance criteria.
- Expanded `docs/V2_DATA_MODEL_DESIGN.md` to v0.2 with versioned ProductionConfig, ModelConfig, ProviderConfig, WorkflowSlot, VideoSpec, AudioConfig, StoragePolicy, QualityPolicy, ExecutionPolicy, PricingCatalog, PricingRule, component-reference, and configuration-reference contracts. Secret values remain outside the database and snapshots.
- Expanded `docs/V2_STATE_MACHINE_EVENT_SYSTEM.md` to v0.2 with the configuration `draft -> validating -> ready -> published -> retired` lifecycle, transition guards, publication impact semantics, runtime configuration failure behavior, concurrency, events, and acceptance tests.
- Publishing or retiring configuration never mutates an existing project or ProductionSnapshot, creates WorkItems, calls a provider, or incurs production cost. Adopting a new configuration requires explicit impact analysis and a new snapshot.
- No automatic paid retry, provider/model/workflow substitution, output repair, hidden default, or fallback setting was introduced. Missing or invalid configuration blocks with exact errors.

## 2026-07-15 V2 Creation Center Sprint 2

- Added a deterministic `RequirementCompletenessEvaluator` for the current blocking fields: core topic, duration, aspect ratio, and audio mode. It validates both value presence and allowed provenance; optional fields remain unspecified and never create questions.
- Added version-bound `ClarificationRequest` records with reason code, controlled options, risk level, explicit resolution, and stale handling. Resolving a clarification creates a new immutable RequirementVersion with `source=user_confirmation`; it never overwrites the previous version or infers another field.
- Candidate generation now stops before creating an AgentRun when blocking clarifications exist. Candidate acceptance also validates completeness and records `validation_failed` rather than repairing invalid output.
- Agent input now contains only messages not already consumed by the candidate behind the active RequirementVersion. After requirement acceptance, the backend returns `REQUIREMENT_READY_FOR_PLANNING`; a repeated generation command without new input fails as `NO_NEW_REQUIREMENT_INPUT`.
- Updated the creation-center UI with a high/medium risk clarification card, controlled option buttons, explicit field scope, and a ready-for-planning state. The old repeat-generation button is no longer shown after all messages are consumed.
- Added Alembic revision `20260715_02` for clarification contracts. Verification passed with 7 backend tests, Python compileall, TypeScript/Vite build, fresh two-revision migration, runtime requirement v1 -> candidate -> v2 flow, repeat-generation rejection, and HTTP 200 checks on API/frontend. No model/provider call, automatic retry, fallback, prompt rewrite, or inferred value was added.

## 2026-07-15 Three-Frame QwenVL Peak-VRAM Repair

- The next explicit 10-second / 24fps debug run passed node validation but failed at `417 / CLIPTextEncode` with `VRAM grow failed: 770707456 bytes` (about 735 MiB).
- The persisted request proved `417.text` was empty, so this was not prompt-length growth. The imported nodeInfoList kept the FP16 QwenVL model resident through `426.keep_model_loaded=true`, leaving insufficient peak VRAM for the LTX text encoder.
- The three-frame preset, backend runtime repair, and frontend normalizer now force `426.keep_model_loaded=false`. QwenVL is released before LTX text encoding; no model, quantization, workflow endpoint, duration, FPS, or generated-frame mapping was changed.
- No automatic retry or additional provider call was made.

## 2026-07-15 V2 Creation Center Sprint 1

- Implemented the first real V2 creation-center vertical slice entirely under `v2/`; V1 and its provider adapters remain untouched.
- Added immutable `RequirementVersion` records, user messages, audited `AgentInputManifest` and `AgentRun` records, requirement candidates, clarification storage, attachments, explicit attachment bindings, and command receipts for idempotency.
- Project creation now establishes `requirement_v1`. The deterministic Mock Creative Agent reads only the active requirement, persisted messages, and confirmed attachment bindings. A successful run creates an `awaiting_review` candidate only; explicit acceptance creates the next requirement version and preserves history.
- New messages make unresolved candidates stale. Candidate acceptance validates the exact active base version, and repeated commands return their first persisted result. No automatic retry, model call, provider call, prompt rewrite, route substitution, hidden default, or inferred entity binding was added.
- Attachment upload is now real rather than metadata-only. The backend reads the file, validates supported media signatures, computes SHA-256, writes under `v2/runtime/uploads`, and only then records `verified`. Upload success does not bind identity or voice; those remain separate user commands with explicit entity IDs.
- Replaced the V2 project contract screen with an API-backed creation center showing the authority/candidate version bar, conversation, per-field provenance, explicit candidate confirmation, attachment binding state, AgentRun history, cost boundary, and one backend-evaluated next action.
- Added an Alembic baseline/creation migration with compatibility for the earlier `create_all` preview schema. `start_v2.ps1` now applies migrations before starting API and Worker.
- Verification passed: 5 backend API tests, Python compileall, TypeScript/Vite production build, fresh Alembic migration test (14 tables), runtime requirement v1 -> candidate -> v2 smoke flow, real attachment upload smoke flow, API health, and scoped `git diff --check`. Browser-control setup returned blank tabs, so screenshot-level desktop/mobile interaction verification remains for the next frontend pass.

## 2026-07-15 Three-Frame QwenVL Frame-Count Repair

- A user-triggered 10-second / 24fps debug run failed before generation because imported nodeInfoList incorrectly sent the computed `240` video frames to `426.frame_count`; RunningHub identified node 426 as `Allab_QwenVL_Advanced` and rejected values above 64.
- Removed `426.frame_count` from the three-frame nodeInfoList. It is a QwenVL media-sampling control, not the generated-video length. The computed `{{frame_count}}` remains mapped only to `424.length` and `373.frames_number`.
- Runtime repair and the debug frontend normalizer now remove this invalid row from imported or saved three-frame mappings. No automatic retry or additional provider call is performed.

## 2026-07-15 First/Middle/Last I2V Duration And FPS Mapping

- Updated only the `06_i2v_first_middle_last_frame / i2v_first_middle_last_frame` RunningHub node mapping so the debug form controls the published workflow instead of retaining imported fixed values.
- Duration now maps to `436.value={{duration}}`; FPS maps to `412.value`, `373.frame_rate`, and `413.frame_rate`; computed total frames map to `424.length` and `373.frames_number` through `{{frame_count}}`.
- Runtime node-list repair now replaces stale fixed values and appends any missing rows for those six fields while preserving all unrelated imported rows and every other workflow slot.
- No generation request or paid provider call was made as part of this configuration repair.

## 2026-07-15 First-Frame I2V Runtime Slot Restore

- Restored only the frontend/runtime `06_i2v_first_frame / i2v_first_frame` slot to the previously validated RunningHub workflow `2069607607387639810`.
- Restored its matching 9-row node mapping: prompt `2483.text`, negative prompt `2612.text`, first frame `2004.image`, longest edge `4981.resize_type.longer_size`, frame count `4979.value`, FPS `4978.value`, seed `4814.noise_seed`, conditioning switch `4977.value=false`, and output prefix `4823.filename_prefix`.
- Fixed the ComfyUI debug frontend normalizer so an explicit `2069607607387639810` endpoint keeps that 9-row mapping instead of replacing it with the incompatible `2071735603636563970` mapping. The existing normalization remains scoped to the explicitly selected optimized publication or an unconfigured endpoint.
- The runtime update was applied through `/api/runtime-comfy-config`. Every other workflow entry was hash-checked and remained unchanged. No task was started and no provider call was made.

## 2026-07-15 V2 Creation Prototype Contract States

- Updated `/prototype-v2/` creation UI to represent the creation-center contracts instead of presenting Agent prose as already-authoritative project state.
- Added an explicit `requirement_v1 -> candidate_02` version bar, current Creative Agent validation state, and expandable history showing a stale candidate plus a failed AgentRun with no automatic retry.
- AI output is visibly labeled as a candidate that has not taken effect. Each requirement summary field now shows its source as user input, Agent proposal, user-confirmed attachment, or versioned system configuration.
- Upload verification and identity binding are separate. The prototype starts with a high-risk blocking clarification; choosing `inspiration_only` keeps the identity blocker, while explicit confirmation creates the visible `identity_reference -> char_main` binding state and reduces the unresolved count.
- Replaced the incorrect external identity-reference image with the existing athlete image used elsewhere in the prototype.
- Verified desktop layout and the complete history/inspiration/binding interaction flow. Verified mobile creation content remains within the viewport and the version bar wraps without horizontal overflow. No browser console errors were observed.

## 2026-07-15 V2 Creation Center Design Baseline

- Added `docs/V2_CREATION_CENTER_DESIGN.md` as the focused implementation specification for converting conversations and attachments into confirmed RequirementVersion and PlanVersion records.
- The creation center now has explicit contracts for requirement field completeness, deterministic clarification, AgentInputManifest context assembly, candidate lifecycles, attachment verification and entity binding, Creative/Director Agent boundaries, AgentRun auditing, structured diffs, change impact, command idempotency, optimistic concurrency, stale-result rejection, API commands/queries, events, UI states, and acceptance tests.
- Context assembly uses the active requirement/plan versions, current message, explicit reply links, current confirmed decisions, entity versions, attachment bindings, and a non-secret system configuration version. It never reads an unbounded transcript or implicit shared Agent memory.
- Agent success only creates a candidate. Schema validation and explicit confirmation promote candidates to RequirementVersion or PlanVersion; old candidates become stale when their base version changes.
- Upload success never creates an identity reference automatically. Identity, voice, and other high-risk attachment bindings require explicit user confirmation.
- Missing optional fields remain unspecified. Only deterministic blocking-field rules create clarification requests. No automatic retry, output repair, prompt rewrite, model switch, hidden template, inferred entity binding, or stale-output overwrite is allowed.
- The implementation order starts with repositories, candidate/version records, input manifests, AgentRun audit, idempotency, and Mock Agent tests before connecting a real model or any production provider.

## 2026-07-15 V2 Prototype Readability Pass

- Increased prototype typography across the dashboard, creation decisions, plan tables, production queue, events, review cards, impact dialog, and editor timeline. Important labels and content now use 12-13px, while supporting text generally uses 10-11px instead of the previous 7-9px range.
- Increased table rows, queue rows, segmented controls, locked-setting rows, affected-item rows, and production-phase cells so larger text does not overlap or compress adjacent content.
- Fixed the project-control navigation regression where the sidebar `create` route returned to the overview instead of opening the `brief` creation screen.
- Verified the dashboard, creation console, plan table, and review grid at 1440x900 with no horizontal overflow. Verified the mobile review layout at 390x844 remains single-column with no horizontal scrolling.

## 2026-07-15 V2 Prototype Product-Control Update

- Updated the frontend-only `/prototype-v2/` experience to match the V2 design baseline without changing V1 production behavior or calling providers.
- Added a project control dashboard showing the computed project stage, active `plan_v1` / `snapshot_001`, production and review counts, actual/estimated/refunded cost summaries, the single next action, production phases, and recent persisted-event examples.
- Creation decisions now show low/medium/high confirmation risk. Low-risk defaults are visible and versioned, high-risk outfit changes expose an impact analysis, and the plan page distinguishes the confirmed plan from the not-yet-created production snapshot.
- Added an impact-confirmation dialog for plan snapshot creation, decision changes, and user-selected asset retries. It displays affected shots/assets, exact new calls, estimated cost, and downstream invalidation before recording the user's intent.
- Asset review now supports selecting exact materials and viewing retry impact. The prototype explicitly states that confirmation does not call providers or charge money; no automatic retry, route replacement, or hidden fallback was introduced.
- Verified JavaScript syntax, unique interactive IDs, balanced CSS blocks, desktop layout at 1440x900, mobile layout at 390x844, no horizontal overflow, review navigation, asset selection, and the retry-impact dialog.

## 2026-07-15 V2 Product, Data, And State Design Baseline

- Expanded `docs/V2_PRODUCT_DESIGN.md` to version 0.2 with confirmation risk levels, decision impact UX, immutable production snapshots, a project control dashboard, strict AI Agent contracts, visible cost confirmation, an explicit/versioned V2.1 template reservation, a concrete 30-second demo flow, and a state/data-first implementation order.
- Added `docs/V2_DATA_MODEL_DESIGN.md` as the authoritative entity and persistence specification. It defines the complete ER model, version and snapshot ownership, exact DAG dependency types, WorkItem/WorkAttempt idempotency, asset/QC/timeline lifecycles, cost ledger, Repository boundaries, SQLite constraints, PostgreSQL migration boundaries, indexes, and an end-to-end data chain.
- Added `docs/V2_STATE_MACHINE_EVENT_SYSTEM.md` as the authoritative lifecycle specification. It defines project/work/asset/plan/snapshot/QC/delivery states, guarded transition matrices, `blocked_from_state`, active-snapshot handling, Worker crash reconciliation, cancellation semantics, the persisted event envelope, Outbox/SSE recovery, idempotent consumers, and a concrete event trace.
- Product defaults must be declared, visible, versioned, and attributable. Templates must be explicitly selected and versioned. Optional DAG dependencies never authorize replacement inputs.
- AI Agents only produce candidate contracts. They cannot create WorkItems, invoke providers, mutate authoritative state, or write production assets.
- No design introduces automatic paid retry, workflow/provider substitution, prompt rewriting, output repair, hidden defaults, or fallback. Any retry or changed production scope requires explicit user selection, impact display, and cost confirmation.
- Documentation authority is split deliberately: product behavior belongs to `V2_PRODUCT_DESIGN.md`, entity/field rules to `V2_DATA_MODEL_DESIGN.md`, and state/event semantics to `V2_STATE_MACHINE_EVENT_SYSTEM.md`.

## 2026-07-15 V2 Modular Application Foundation

- GitHub Actions now separates repository agent validation from V2 application validation. The legacy `CI` workflow excludes `v2/` Markdown from agent-frontmatter scanning, while `V2 CI` runs Python contract tests and the TypeScript production build for V2 changes.
- The authoritative V2 product and architecture design is documented in `docs/V2_PRODUCT_DESIGN.md`. Future state-machine, confirmation-boundary, fallback, retry, provider, and paid-call changes should update that document and receive user confirmation before implementation.
- Added an isolated V2 application under `v2/`. V1 remains unchanged on port `8765`; V2 runs on port `8766` and does not import V1 production adapters.
- V2 backend uses FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, and SQLite. Projects, append-only user decisions, work items, and project events are persisted as separate database entities.
- Project confirmation is explicit: unresolved decisions return a visible conflict. Only a confirmed project can enqueue the registered `contract_validation` work kind.
- A separate database-backed Worker claims queued work atomically, executes only registered work kinds, and writes persisted events. Unknown kinds block explicitly; there is no route guessing, provider downgrade, automatic retry, prompt rewrite, or output repair.
- Project events are available through SSE and work across the API and Worker process boundary because the database, rather than process memory, is authoritative.
- V2 frontend uses React, TypeScript, Vite, React Router, TanStack Query, Zustand, CSS Modules, and Lucide. It includes API-backed project creation, contract/decision views, queue submission, and reserved review/editor/entity/config module routes.
- The production build is served by FastAPI. `v2/start_v2.bat` invokes `v2/start_v2.ps1` with a process-scoped execution-policy bypass and starts the API and Worker together; the V2 runtime database and logs remain ignored under `v2/runtime/`.
- Verification completed with the production frontend build, TypeScript checking, Python compileall, API contract tests, SSE read, desktop/mobile browser checks, and a real local draft -> confirm -> queue -> worker -> `review_required` smoke flow. No external provider or paid production call was made.

## 2026-07-14 V2 Interactive Product Prototype

- Added an isolated, frontend-only V2 product prototype at `/prototype-v2/`. It does not call models, production providers, or paid APIs and does not change the existing V1 workflow behavior.
- The prototype covers the intended main flow: conversational requirement capture, explicit decision confirmation, structured shot contracts, staged material production, human-owned quality review, and an edit timeline that accepts reviewed assets.
- The UI makes system-owned settings, user decisions, actual production routes, retry counts, and blocking states visible. It explicitly demonstrates audio-off behavior and does not simulate automatic retry, route replacement, prompt rewriting, or hidden fallback.
- Added responsive layouts for desktop and mobile, plus interactive navigation, decision controls, mock production stages, review filters, asset selection, and timeline inspection.
- The management server serves the prototype's static HTML/CSS/JS from `my_workspace/v2_prototype/`; the current production application remains at `/`.

## 2026-07-14 Special-Case Topic Fallback Removal

- Removed all topic-specific semantic exceptions from the requirement guard, including the format-token allowlist and the dedicated concept matching previously written for individual story or subject patterns. The backend no longer accepts a changed topic by guessing that selected words or synonyms are close enough.
- Topic ownership is now one uniform explicit contract. `requirement_lock_prompt` provides `core_topic`; employees 01, 03, 23, and 04 must copy it verbatim into their standard output anchor. Validation checks that exact anchor or an exact structured anchor field. It does not rewrite, infer, translate, or backfill the employee output.
- Employee 03 guidance no longer contains product-, platform-, persona-, or attribute-specific prohibition examples. It states only the general provenance rule: factual descriptions require an explicit user, upstream, or linked-asset source; otherwise the field remains unspecified or neutral.
- Historical rejected outputs without the exact anchor are not silently accepted. A user-initiated resume regenerates the employee output under the current contract; no automatic resume or reuse is performed.
- Verification: 200 semantic-contract tests, `python -m compileall -q my_workspace`, `git diff --check`, and a repository search confirming the special-case topic functions and allowlist are absent.

## 2026-07-14 Requirement Ownership And Stale Run Repair

- Requirement validation now has separate owners instead of one generic delivery gate: employees 01/03/23 retain duration validation, while only employees 01 and 23 must repeat explicit orientation/aspect constraints. Employee 03 owns topic, narration structure, and duration; a valid voiceover script no longer fails merely because it does not say `竖屏` or `9:16`.
- Employee 03's contract explicitly forbids inventing default platforms, personas, brands, styles, materials, functions, prices, selling points, or product effects. Missing non-blocking information stays `未指定` or uses neutral wording; the employee must not append a non-blocking confirmation/assumption section. This is a prompt contract, not backend content rewriting.
- The latest rejected script from `task_20260714_202854_小美的内衣试穿vlog_竖屏1分钟长视频` now passes both requirement alignment and the employee-03 production contract. Employee 03 still fails when duration is missing; employees 01 and 23 still fail when an explicit portrait requirement is missing.
- Task state no longer lets an older in-memory `paused` run override a newer terminal `run_summary.json`. A paused/queued/running record older than a persisted `failed`, `completed`, or `cancelled` summary is ignored by both task detail and `/api/active-run`, so a failed task is shown as failed instead of appearing stuck at an earlier confirmation.
- Verification: 200 semantic-contract tests, complete real rejected-output regression, `python -m compileall -q my_workspace`, and `git diff --check` passed. No automatic resume, output backfill, paid retry, or provider call was run.

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
- Topic-retention checks require the exact `core_topic` anchor supplied by the requirement lock. The backend does not maintain subject-specific synonym lists or infer that a paraphrase is equivalent.
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
