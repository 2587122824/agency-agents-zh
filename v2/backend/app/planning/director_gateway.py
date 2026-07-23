from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Literal, Protocol
from urllib.parse import urljoin

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..creation.agent_gateway import AgentChatTransport, AgentGatewayError, HttpxAgentChatTransport
from ..db.models import ModelConfigVersion, ProductionConfigVersion, ProviderConfigVersion


DIRECTOR_INPUT_CONTRACT_VERSION = "director-input.v2"
DIRECTOR_OUTPUT_SCHEMA_VERSION = "shot-plan.v4"
DIRECTOR_PROMPT_CONTRACT_VERSION = "director-prompt.v6"


class GenerationRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reference_image_required: bool
    multi_frame_required: bool
    identity_consistency_required: bool
    precise_text_required: bool

    @model_validator(mode="after")
    def requirements_are_coherent(self):
        if self.identity_consistency_required and not self.reference_image_required:
            raise ValueError("identity consistency requires a reference image")
        return self


class GuideFramePrompts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: str = Field(min_length=1, max_length=4000)
    middle: str = Field(min_length=1, max_length=4000)
    end: str = Field(min_length=1, max_length=4000)


class DirectorShotOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shot_code: str = Field(pattern=r"^SH-[0-9]{3}$")
    sequence_number: int = Field(ge=1, le=999)
    duration_ms: int = Field(ge=100, le=3_600_000)
    narrative_beat_code: str = Field(pattern=r"^BEAT_[0-9]{2,3}$")
    brief_segment_codes: list[str] = Field(min_length=1, max_length=80)
    continuity_group_id: str | None = Field(default=None, pattern=r"^CONT-[0-9]{3}$")
    continuity_relation: Literal["same_moment", "time_jump", "location_change", "outfit_change"]
    action_count: Literal[1]
    shot_purpose: Literal["establish", "develop", "demonstrate", "contrast", "transition", "resolve"]
    framing: Literal["extreme_close_up", "close_up", "medium", "full", "wide"]
    camera_angle: Literal["eye_level", "high", "low", "top_down", "over_shoulder"]
    camera_motion: Literal["locked", "pan", "tilt", "dolly", "tracking", "handheld"]
    subject_motion: Literal["none", "subtle", "moderate", "significant"]
    scene_entity_version_id: str | None = Field(default=None, max_length=48)
    character_entity_version_ids: list[str] = Field(default_factory=list, max_length=20)
    outfit_entity_version_ids: list[str] = Field(default_factory=list, max_length=20)
    product_entity_version_ids: list[str] = Field(default_factory=list, max_length=20)
    primary_reference_entity_version_id: str | None = Field(default=None, max_length=48)
    face_visibility: Literal["required", "optional", "not_visible"]
    face_subject_entity_version_ids: list[str] = Field(default_factory=list, max_length=20)
    text_policy: Literal["forbidden", "allowed", "required"]
    required_on_screen_text: str | None = Field(default=None, min_length=1, max_length=1000)
    audio_requirement: Literal["off", "lip_motion_only", "configured"]
    composition: str = Field(min_length=1, max_length=500)
    action: str = Field(min_length=1, max_length=1000)
    visual_prompt: str = Field(min_length=1, max_length=4000)
    guide_frame_prompts: GuideFramePrompts | None
    negative_prompt: str | None = Field(default=None, min_length=1, max_length=2000)
    new_information: str = Field(min_length=1, max_length=1000)
    generation_requirements: GenerationRequirements

    @model_validator(mode="after")
    def references_are_unique(self):
        for field in (
            "character_entity_version_ids",
            "outfit_entity_version_ids",
            "product_entity_version_ids",
            "brief_segment_codes",
            "face_subject_entity_version_ids",
        ):
            values = getattr(self, field)
            if len(values) != len(set(values)):
                raise ValueError(f"{field} must contain unique IDs")
        declared = {
            *(self.character_entity_version_ids or []),
            *(self.outfit_entity_version_ids or []),
            *(self.product_entity_version_ids or []),
        }
        if self.scene_entity_version_id:
            declared.add(self.scene_entity_version_id)
        if self.primary_reference_entity_version_id and self.primary_reference_entity_version_id not in declared:
            raise ValueError("primary reference entity must be declared by the shot")
        if self.face_visibility == "required" and not self.face_subject_entity_version_ids:
            raise ValueError("required face visibility requires exact face subject IDs")
        if self.face_visibility != "required" and self.face_subject_entity_version_ids:
            raise ValueError("face subject IDs are only allowed when face visibility is required")
        if set(self.face_subject_entity_version_ids) - set(self.character_entity_version_ids):
            raise ValueError("face subjects must be declared character entities")
        if self.text_policy == "required" and self.required_on_screen_text is None:
            raise ValueError("required text policy requires exact on-screen text")
        if self.text_policy == "forbidden" and self.required_on_screen_text is not None:
            raise ValueError("forbidden text policy requires null on-screen text")
        if self.generation_requirements.precise_text_required and self.text_policy != "required":
            raise ValueError("precise text capability requires required text policy")
        if self.generation_requirements.multi_frame_required and self.guide_frame_prompts is None:
            raise ValueError("multi-frame generation requires start, middle, and end frame prompts")
        if not self.generation_requirements.multi_frame_required and self.guide_frame_prompts is not None:
            raise ValueError("guide frame prompts are only allowed for multi-frame generation")
        return self


class DirectorOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shots: list[DirectorShotOutput] = Field(min_length=1, max_length=200)


@dataclass(frozen=True)
class DirectorSelection:
    production_config_version_id: str
    model_config_version_id: str
    provider_config_version_id: str
    model_provider: str
    model_name: str
    provider_model_id: str
    base_url: str
    api_key: str | None
    timeout_seconds: int
    input_contract_version: str
    prompt_contract_version: str
    output_schema_version: str
    max_output_tokens: int | None
    sampling: dict[str, Any]


@dataclass(frozen=True)
class DirectorResult:
    output: DirectorOutput
    raw_output: dict[str, Any]
    provider_request_id: str | None
    token_usage: dict[str, Any]


class DirectorGateway(Protocol):
    def select(self, session: Session) -> DirectorSelection: ...

    def invoke(self, selection: DirectorSelection, manifest_payload: dict[str, Any]) -> DirectorResult: ...


_DIRECTOR_SYSTEM_PROMPT = """你是片场 V2 的分镜导演智能体。你只把已接受的内容方案拆成结构化镜头候选，或按用户明确授权修订指定镜头；不与用户闲聊，不选择供应商、模型或工作流。
只返回严格 JSON：{"shots":[{"shot_code":"SH-001","sequence_number":1,"duration_ms":3000,"narrative_beat_code":"BEAT_01","brief_segment_codes":["SEG_01"],"continuity_group_id":null,"continuity_relation":"same_moment","action_count":1,"shot_purpose":"establish","framing":"medium","camera_angle":"eye_level","camera_motion":"locked","subject_motion":"moderate","scene_entity_version_id":null,"character_entity_version_ids":[],"outfit_entity_version_ids":[],"product_entity_version_ids":[],"primary_reference_entity_version_id":null,"face_visibility":"not_visible","face_subject_entity_version_ids":[],"text_policy":"forbidden","required_on_screen_text":null,"audio_requirement":"off","composition":"构图描述","action":"唯一动作","visual_prompt":"视频整体运动描述","guide_frame_prompts":null,"negative_prompt":null,"new_information":"相对前一镜头新增的信息","generation_requirements":{"reference_image_required":false,"multi_frame_required":false,"identity_consistency_required":false,"precise_text_required":false}}]}。
以下格式与枚举区分大小写，必须逐字使用，不得翻译、缩写或创造近义值：
- continuity_group_id：只能是 JSON null 或匹配 ^CONT-[0-9]{3}$ 的字符串，例如 CONT-001；禁止 CG_01、group_01 等其他格式。
- continuity_relation：same_moment | time_jump | location_change | outfit_change
- shot_purpose：establish | develop | demonstrate | contrast | transition | resolve
- framing：extreme_close_up | close_up | medium | full | wide
- camera_angle：eye_level | high | low | top_down | over_shoulder
- camera_motion：locked | pan | tilt | dolly | tracking | handheld
- subject_motion：none | subtle | moderate | significant
- face_visibility：required | optional | not_visible
- text_policy：forbidden | allowed | required
- audio_requirement：off | lip_motion_only | configured
规则：
1. SH 编号和 sequence_number 必须从 1 连续；每个镜头 action_count=1。
2. 每个脚本段必须至少被一个镜头覆盖。brief_segment_codes 只能逐字引用输入脚本段，且每个段必须属于该镜头引用的叙事节拍。
3. 每个节拍的镜头时长总和必须精确等于该节拍时长。
4. 所有枚举字段只能从上方对应列表选择一个精确值。输出前逐字段核对；后端不会映射、翻译或修复非法值。
5. 实体 ID 只能逐字引用 confirmed_entity_versions。face_visibility=required 时必须列出确切 face_subject_entity_version_ids，且只能引用该镜头已声明的 character 实体。
6. text_policy=required 时 required_on_screen_text 必须是最终成品需要出现的精确文字；forbidden 时必须为 null。普通标题、字幕和教学标注由后期文字轨完成，不要求生成素材直接包含文字。
7. continuity_relation 明确本镜头与前一镜头的关系。场景或服装发生变化时必须分别使用 location_change 或 outfit_change；同一 continuity_group_id 内实体必须完全一致。不需要连续组时填 null，不得自行创建其他前缀。
8. new_information 必须说明本镜头相对前一镜头新增的叙事信息，供用户人工检查重复，不得留空。
9. generation_requirements 只声明生成素材本身所需的生产能力，不得写路由。身份一致性要求必须同时要求参考图。只有用户明确要求文字必须由图像生成模型直接绘制进原始素材像素时，才设置 precise_text_required=true；普通标题、字幕、箭头说明和教学标注必须为 false，并保留 text_policy=required 与 required_on_screen_text 交给后期文字轨。
   multi_frame_required=true 时 guide_frame_prompts 必须精确提供 start、middle、end 三个连续画面状态；三项必须保持同一主体、服装、场景、光线、机位和构图，只描述同一动作在三个时间点的状态。否则 guide_frame_prompts 必须为 null。
10. audio_policy=off 时 audio_requirement 只能为 off 或 lip_motion_only，不得建立音频生产依赖。
11. revision_request 存在时，只能修改 selected_shot_codes；其他镜头必须与 source_shots 结构完全一致。仍需返回完整方案并满足全部合同。
12. 不得输出 Provider、模型、工作流、NodeInfoList、价格或任务 ID；不得修复输入、猜测 ID、添加默认尾缀或返回 Markdown。"""


class ConfiguredDirectorGateway:
    def __init__(self, *, transport: AgentChatTransport | None = None) -> None:
        self.transport = transport or HttpxAgentChatTransport()

    def select(self, session: Session) -> DirectorSelection:
        rows = list(session.execute(
            select(ModelConfigVersion, ProviderConfigVersion, ProductionConfigVersion)
            .join(ProviderConfigVersion, ProviderConfigVersion.id == ModelConfigVersion.provider_config_version_id)
            .join(ProductionConfigVersion, ProductionConfigVersion.id == ModelConfigVersion.production_config_version_id)
            .where(
                ModelConfigVersion.agent_role == "director",
                ModelConfigVersion.status == "published",
                ProviderConfigVersion.status == "published",
                ProductionConfigVersion.status == "published",
            )
            .order_by(ModelConfigVersion.config_key, ModelConfigVersion.version_number.desc())
        ))
        latest_by_key: dict[str, tuple[ModelConfigVersion, ProviderConfigVersion, ProductionConfigVersion]] = {}
        for model, provider, config in rows:
            latest_by_key.setdefault(model.config_key, (model, provider, config))
        if not latest_by_key:
            raise AgentGatewayError("DIRECTOR_MODEL_NOT_CONFIGURED", "当前没有已发布的分镜导演模型配置。")
        if len(latest_by_key) != 1:
            raise AgentGatewayError("DIRECTOR_MODEL_SELECTION_AMBIGUOUS", "当前存在多个分镜导演模型系列，请在系统配置中保留一个明确选择。")
        model, provider, config = next(iter(latest_by_key.values()))
        if provider.adapter_kind != "openai_compatible":
            raise AgentGatewayError("DIRECTOR_ADAPTER_UNSUPPORTED", "分镜导演模型没有绑定 OpenAI-compatible 服务供应商。")
        if "text_generation" not in provider.capabilities:
            raise AgentGatewayError("DIRECTOR_CAPABILITY_MISSING", "分镜导演模型供应商未声明文本生成能力。")
        expected = {
            "input_contract_version": DIRECTOR_INPUT_CONTRACT_VERSION,
            "output_schema_version": DIRECTOR_OUTPUT_SCHEMA_VERSION,
            "prompt_contract_version": DIRECTOR_PROMPT_CONTRACT_VERSION,
        }
        if {key: getattr(model, key) for key in expected} != expected:
            raise AgentGatewayError("DIRECTOR_CONTRACT_VERSION_UNSUPPORTED", "分镜导演模型配置的合同版本与当前运行代码不一致。")
        return DirectorSelection(
            production_config_version_id=config.id,
            model_config_version_id=model.id,
            provider_config_version_id=provider.id,
            model_provider=provider.display_name,
            model_name=model.display_name,
            provider_model_id=model.provider_model_id,
            base_url=provider.base_url,
            api_key=provider.api_key,
            timeout_seconds=provider.request_timeout_seconds,
            input_contract_version=model.input_contract_version,
            prompt_contract_version=model.prompt_contract_version,
            output_schema_version=model.output_schema_version,
            max_output_tokens=model.max_output_tokens,
            sampling=dict(model.sampling or {}),
        )

    def invoke(self, selection: DirectorSelection, manifest_payload: dict[str, Any]) -> DirectorResult:
        if os.getenv("V2_AGENT_MODEL_EXECUTION_ENABLED", "").strip().lower() not in {"1", "true", "yes"}:
            raise AgentGatewayError("AGENT_MODEL_EXECUTION_DISABLED", "分镜导演模型真实调用尚未获得后端执行授权。")
        api_key = str(selection.api_key or "").strip()
        if not api_key:
            raise AgentGatewayError("AGENT_MODEL_CREDENTIAL_UNAVAILABLE", "分镜导演模型供应商的 API Key 未填写。")
        payload: dict[str, Any] = {
            "model": selection.provider_model_id,
            "messages": [
                {"role": "system", "content": _DIRECTOR_SYSTEM_PROMPT},
                {"role": "user", "content": "以下是本次不可变分镜导演输入合同：" + json.dumps(manifest_payload, ensure_ascii=False, separators=(",", ":"))},
            ],
            "response_format": {"type": "json_object"},
        }
        if selection.max_output_tokens is not None:
            payload["max_tokens"] = selection.max_output_tokens
        for key, value in selection.sampling.items():
            if key in {"temperature", "top_p", "frequency_penalty", "presence_penalty", "seed"}:
                payload[key] = value
        response = self.transport.create_chat_completion(
            url=urljoin(selection.base_url.rstrip("/") + "/", "chat/completions"),
            api_key=api_key,
            payload=payload,
            timeout_seconds=selection.timeout_seconds,
        )
        try:
            parsed = json.loads(response["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise AgentGatewayError("DIRECTOR_OUTPUT_SCHEMA_INVALID", "分镜导演输出不是有效 JSON 对象。") from exc
        try:
            output = DirectorOutput.model_validate(parsed)
            validate_director_output_against_manifest(output, manifest_payload)
        except ValidationError as exc:
            raise AgentGatewayError(
                "DIRECTOR_OUTPUT_SCHEMA_INVALID",
                "分镜导演输出不符合严格分镜合同。",
                raw_output=parsed if isinstance(parsed, dict) else None,
                diagnostics=exc.errors(include_input=False),
            ) from exc
        except ValueError as exc:
            raise AgentGatewayError("DIRECTOR_OUTPUT_CONTRACT_INVALID", str(exc), raw_output=parsed if isinstance(parsed, dict) else None) from exc
        usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
        request_id = str(response.get("id") or "").strip() or None
        return DirectorResult(output, parsed, request_id, usage)


def validate_director_output_against_manifest(output: DirectorOutput, manifest: dict[str, Any]) -> None:
    shots = output.shots
    expected_codes = [f"SH-{index:03d}" for index in range(1, len(shots) + 1)]
    if [shot.shot_code for shot in shots] != expected_codes or [shot.sequence_number for shot in shots] != list(range(1, len(shots) + 1)):
        raise ValueError("镜头代码与顺序必须从 SH-001 和 1 连续编号。")
    beats = manifest["accepted_creative_brief"]["brief"]["narrative_beats"]
    segments = manifest["accepted_creative_brief"]["brief"]["script_segments"]
    beat_durations = {item["beat_code"]: int(item["target_duration_ms"]) for item in beats}
    segment_beats = {item["segment_code"]: item["beat_code"] for item in segments}
    actual_by_beat = {code: 0 for code in beat_durations}
    covered_segments: set[str] = set()
    for shot in shots:
        if shot.narrative_beat_code not in beat_durations:
            raise ValueError(f"镜头 {shot.shot_code} 引用了不存在的内容节拍。")
        unknown_segments = sorted(set(shot.brief_segment_codes) - set(segment_beats))
        if unknown_segments:
            raise ValueError(f"镜头 {shot.shot_code} 引用了不存在的脚本段：{unknown_segments}。")
        wrong_beat = sorted(code for code in shot.brief_segment_codes if segment_beats[code] != shot.narrative_beat_code)
        if wrong_beat:
            raise ValueError(f"镜头 {shot.shot_code} 的脚本段不属于所引用节拍：{wrong_beat}。")
        covered_segments.update(shot.brief_segment_codes)
        actual_by_beat[shot.narrative_beat_code] += shot.duration_ms
    missing_segments = sorted(set(segment_beats) - covered_segments)
    if missing_segments:
        raise ValueError(f"以下脚本段没有任何镜头覆盖：{missing_segments}。")
    mismatches = [code for code, duration in beat_durations.items() if actual_by_beat[code] != duration]
    if mismatches:
        raise ValueError(f"以下内容节拍的镜头时长不匹配：{mismatches}。")
    available_entities = {item["id"]: item for item in manifest["confirmed_entity_versions"]}
    continuity: dict[str, tuple[Any, ...]] = {}
    previous_shot: DirectorShotOutput | None = None
    for shot in shots:
        entity_ids = {
            *(shot.character_entity_version_ids or []),
            *(shot.outfit_entity_version_ids or []),
            *(shot.product_entity_version_ids or []),
        }
        if shot.scene_entity_version_id:
            entity_ids.add(shot.scene_entity_version_id)
        unknown = sorted(entity_ids - set(available_entities))
        if unknown:
            raise ValueError(f"镜头 {shot.shot_code} 引用了未确认的实体版本：{unknown}。")
        invalid_face_subjects = sorted(
            entity_id for entity_id in shot.face_subject_entity_version_ids
            if available_entities.get(entity_id, {}).get("entity_type") != "character"
        )
        if invalid_face_subjects:
            raise ValueError(f"镜头 {shot.shot_code} 的人脸主体不是已确认人物实体：{invalid_face_subjects}。")
        if shot.primary_reference_entity_version_id:
            entity = available_entities[shot.primary_reference_entity_version_id]
            attachment = entity.get("source_attachment")
            if not attachment or not attachment.get("verified") or not str(attachment.get("mime_type", "")).startswith("image/"):
                raise ValueError(f"镜头 {shot.shot_code} 的主参考实体没有已验证图片。")
        if shot.continuity_group_id:
            signature = (
                shot.scene_entity_version_id,
                tuple(shot.character_entity_version_ids),
                tuple(shot.outfit_entity_version_ids),
            )
            previous = continuity.setdefault(shot.continuity_group_id, signature)
            if previous != signature:
                raise ValueError(f"连续组 {shot.continuity_group_id} 的场景、人物或服装版本不一致。")
        if previous_shot is not None:
            scene_changed = previous_shot.scene_entity_version_id != shot.scene_entity_version_id
            outfit_changed = previous_shot.outfit_entity_version_ids != shot.outfit_entity_version_ids
            if scene_changed and shot.continuity_relation != "location_change":
                raise ValueError(f"镜头 {shot.shot_code} 的场景变化必须显式声明 location_change。")
            if not scene_changed and outfit_changed and shot.continuity_relation != "outfit_change":
                raise ValueError(f"镜头 {shot.shot_code} 的服装变化必须显式声明 outfit_change。")
        previous_shot = shot
    if manifest["audio_policy"] == "off":
        invalid = [shot.shot_code for shot in shots if shot.audio_requirement not in {"off", "lip_motion_only"}]
        if invalid:
            raise ValueError(f"音频关闭时镜头不得建立音频依赖：{invalid}。")
    revision = manifest.get("revision_request")
    if revision:
        selected = set(revision["selected_shot_codes"])
        source = {item["shot_code"]: item for item in revision["source_shots"]}
        if set(source) != {shot.shot_code for shot in shots}:
            raise ValueError("AI 修订必须返回与原候选相同的完整镜头集合。")
        changed_unselected = [
            shot.shot_code for shot in shots
            if shot.shot_code not in selected and shot.model_dump(mode="json") != source[shot.shot_code]
        ]
        if changed_unselected:
            raise ValueError(f"AI 修订改变了未授权镜头：{changed_unselected}。")


class DeterministicDirectorGateway:
    """Explicit test gateway; never registered by the runtime application."""

    def select(self, session: Session) -> DirectorSelection:
        return DirectorSelection(
            production_config_version_id="v2.director.test.v1",
            model_config_version_id="model_config_test_director",
            provider_config_version_id="provider_config_test_mock",
            model_provider="mock",
            model_name="deterministic-director-v1",
            provider_model_id="deterministic-director-v1",
            base_url="https://example.invalid/v1",
            api_key=None,
            timeout_seconds=1,
            input_contract_version=DIRECTOR_INPUT_CONTRACT_VERSION,
            prompt_contract_version=DIRECTOR_PROMPT_CONTRACT_VERSION,
            output_schema_version=DIRECTOR_OUTPUT_SCHEMA_VERSION,
            max_output_tokens=None,
            sampling={},
        )

    def invoke(self, selection: DirectorSelection, manifest_payload: dict[str, Any]) -> DirectorResult:
        brief = manifest_payload["accepted_creative_brief"]["brief"]
        entity_types = {item["id"]: item["entity_type"] for item in manifest_payload["confirmed_entity_versions"]}
        referenced = [item for item in (brief.get("entity_version_ids") or []) if item in entity_types]
        characters = [item for item in referenced if entity_types[item] == "character"]
        outfits = [item for item in referenced if entity_types[item] == "outfit"]
        scenes = [item for item in referenced if entity_types[item] == "scene"]
        products = [item for item in referenced if entity_types[item] == "product"]
        shots = [
            DirectorShotOutput(
                shot_code=f"SH-{index:03d}",
                sequence_number=index,
                duration_ms=int(beat["target_duration_ms"]),
                narrative_beat_code=beat["beat_code"],
                brief_segment_codes=[
                    item["segment_code"] for item in brief["script_segments"]
                    if item["beat_code"] == beat["beat_code"]
                ],
                continuity_group_id=None,
                continuity_relation="same_moment",
                action_count=1,
                shot_purpose="develop",
                framing="medium",
                camera_angle="eye_level",
                camera_motion="locked",
                subject_motion="moderate",
                scene_entity_version_id=scenes[0] if scenes else None,
                character_entity_version_ids=characters,
                outfit_entity_version_ids=outfits,
                product_entity_version_ids=products,
                primary_reference_entity_version_id=None,
                face_visibility="optional" if characters else "not_visible",
                face_subject_entity_version_ids=[],
                text_policy="forbidden",
                required_on_screen_text=None,
                audio_requirement="off" if manifest_payload["audio_policy"] == "off" else "configured",
                composition=str(beat["purpose"]),
                action=str(beat["summary"]),
                visual_prompt=f"{brief['content_promise']}。{beat['summary']}。",
                guide_frame_prompts=None,
                negative_prompt=None,
                new_information=str(beat["summary"]),
                generation_requirements=GenerationRequirements(
                    reference_image_required=False,
                    multi_frame_required=False,
                    identity_consistency_required=False,
                    precise_text_required=False,
                ),
            )
            for index, beat in enumerate(brief["narrative_beats"], start=1)
        ]
        output = DirectorOutput(shots=shots)
        validate_director_output_against_manifest(output, manifest_payload)
        raw = output.model_dump(mode="json")
        return DirectorResult(output, raw, "test-director-request", {"total_tokens": 1})


def get_director_gateway() -> DirectorGateway:
    return ConfiguredDirectorGateway()
