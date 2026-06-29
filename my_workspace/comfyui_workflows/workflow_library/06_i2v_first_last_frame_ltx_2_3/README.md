# 06_i2v_first_last_frame_ltx_2_3

用途：LTX 2.3 纯节点双参考图生视频工作流，用于 `06_i2v_first_last_frame`。

## 生产定位

- 输入：首帧 `{{reference_image}}` + 尾帧参考 `{{last_frame_image}}`
- 输出：一段由首帧出发、受尾帧参考约束的无声视频素材
- 默认参数：576x1024、24fps、4秒；LTX 内部 length 使用 81，目标导出约 97 帧
- 使用范围：短转场、动作闭合、镜头衔接、轻量 A 到 B 运动

## 重要说明

这个版本从零构建，不以下载目录旧工作流为母版，并且不包含任何 `Wan*` 节点。

LTX 2.3 当前可用节点没有 Wan 那种硬 `start_image/end_image` 条件节点；本画布改用 LTX guide 方案，避免尾帧参考覆盖首帧起点：

1. `Start Frame / reference_image` 进入 `LTXVImgToVideoAdvanced`，锚定视频第 0 帧。
2. 同一张首帧再进入 `LTXVAddGuideAdvanced`，`frame_idx=12`，让开头约半秒更稳定。
3. `End Frame / last_frame_image` 进入低注意力 `LTXVAddGuideAdvancedAttention`：`frame_idx=64 strength=0.42 attention_strength=0.28`。
4. LTX 采样、分离视频 latent、VAE Decode、CreateVideo、SaveVideo。

因此它是“LTX 首尾 guide 约束”，不是 Wan 风格的强制首尾帧插值。首帧应直接出现在视频开头；尾帧会作为末帧 guide 约束，命中程度取决于两张图差异、提示词和模型能力。

## 当前接线

```text
LoadImage start_frame (node 2)
  -> LTXVImgToVideoAdvanced (node 15, index 0)
  -> LTXVAddGuideAdvanced (node 17, frame_idx=12)

LoadImage end_frame (node 3)
  -> LTXVAddGuideAdvancedAttention (node 16, frame_idx=64)

Prompt / Negative
  -> LTXVConditioning
  -> CFGGuider + LTXVScheduler + SamplerCustomAdvanced
  -> LTXVSeparateAVLatent
  -> LTXVTiledVAEDecode
  -> CreateVideo
  -> SaveVideo (node 28)
```

## RunningHub 占位符

```text
{{reference_image}}
{{last_frame_image}}
{{prompt}}
{{negative_prompt}}
{{width}}
{{height}}
{{fps}}
{{duration}}
{{frame_count}}
{{ltx_guide_frame_count}}
{{seed}}
```

## 验收标准

- 画布里没有 `Wan*` 节点。
- 第一帧应接近 `reference_image`。
- 视频整体应朝 `last_frame_image` 的姿态/构图靠近。
- 中间过渡不变脸、不换衣服、不换场景。
- 只有一个 SaveVideo 主输出。

如果首尾帧差异太大，07 员工必须降级为 `06_i2v_first_frame`，或把镜头拆成多个短片段。
