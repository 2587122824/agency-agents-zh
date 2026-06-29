# 生产级关键帧工作流说明

这份说明不是“再修补一次弱条件引导版”，而是把关键帧工作流的生产级要求直接定死。

## 结论

关键帧工作流必须满足两个条件：

1. 主链必须支持真正的 img2img
2. 多人物必须支持多参考图分路

如果只满足第二条，不满足第一条，参考图效果通常仍然偏弱。

## 你应该输入什么

- `prompt`
- `negative_prompt`
- `reference_image`
- `reference_image_1`
- `reference_image_2`
- `reference_image_3`
- `reference_image_4`
- `width`
- `height`
- `seed`

推荐业务含义：

- `reference_image`：主参考图，用于 img2img 主链
- `reference_image_1`：角色A
- `reference_image_2`：角色B
- `reference_image_3`：场景或风格
- `reference_image_4`：道具、产品或额外人物

## 你应该输出什么

- 一张可直接给 07 图生视频作为首帧的关键帧图

重点不是单张图漂不漂亮，而是：

- 人物身份稳定
- 服装和体型稳定
- 场景稳定
- 构图适合后续视频延展

## 生产级结构

### 主 latent 链

- 无参考图：`EmptyLatent -> Switch.on_false -> KSampler.latent_image`
- 有参考图：`LoadImage MainReference -> VAEEncode -> Switch.on_true -> KSampler.latent_image`

这个 `Switch` 用 `{{has_reference_image}}` 控制。

### 多人物身份链

- 角色A参考图 -> FaceID / InstantID / IPAdapter FaceID
- 角色B参考图 -> FaceID / InstantID / IPAdapter FaceID
- 场景或风格参考图 -> IPAdapter Style / Reference

这些分支最终都要汇总到同一个 `MODEL`，再送入 `KSampler`。

## 当前仓库文件如何使用

- [production_workflow_contract.json](/I:/AI_WorkSpace/agency-agents-zh/my_workspace/comfyui_workflows/workflow_library/04_keyframe_image/production_workflow_contract.json)
- [production_nodeinfo_extended_example.json](/I:/AI_WorkSpace/agency-agents-zh/my_workspace/comfyui_workflows/workflow_library/04_keyframe_image/production_nodeinfo_extended_example.json)

前者约束工作流架构，后者约束 RunningHub / 调试台的 nodeInfoList 映射。

## 现实约束

如果底模和 FaceID / IPAdapter 模型体系不一致，工作流结构再对，参考图也可能不稳定。

所以上线前必须确认：

- 底模体系一致
- FaceID / IPAdapter 体系一致
- 参考图分支真实接入
- img2img 主链真实生效
