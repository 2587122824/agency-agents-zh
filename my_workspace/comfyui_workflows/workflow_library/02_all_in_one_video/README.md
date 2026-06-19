# 全能视频

用途：统一承接数字员工输出的 `video_prompts` / `video_jobs`。员工只需要输出视频提示词、参考图和控制模式，系统把每个视频 job 转成固定 API 入参并调用该工作流。

## 文件怎么用

- `workflow_canvas.json`：导入 ComfyUI 画布用。打开 ComfyUI 后直接拖入页面，或用 Load 加载这个 JSON。
- `api_template.json`：给 RunningHub/ComfyUI API 调用参考，不是画布导入文件。
- `runninghub_node_info_list_preset.json`：给管理台“当前编辑槽位节点映射 JSON”使用。
- `workflow_contract.json` / `node_info_extended_example.json`：用于你后续把真实 IPAdapter、FaceID、OpenPose/姿态视频、产品参考节点接进去时对照字段。

当前 `workflow_canvas.json` 是可导入的基础全能视频画布，基于项目里已验证的 LTX 图生视频模板。它默认保持动态 I2V 输出，不把单张姿态图接进最终视频，避免再次出现画面不动或杂音问题。你导入后可以继续加 IPAdapter/FaceID/姿态视频分支；加完后再从 ComfyUI 导出 API JSON，回到管理台导入并校准真实节点 ID。

推荐统一字段：

```json
{
  "task_type": "txt2video | img2video | first_frame | broll | transition | talking_head | product_demo",
  "prompt": "视频正向提示词",
  "negative_prompt": "负向提示词",
  "reference_image": "可选首帧/人物/产品参考图",
  "control_mode": "none | first_frame | ipadapter_character | ipadapter_product | openpose_motion",
  "duration": 5,
  "width": 960,
  "height": 544,
  "fps": 24,
  "seed": -1
}
```

当前默认 preset 兼容现有 LTX 图生视频模板，默认只生成画面素材，不负责最终配音、字幕或混音。

如果你的 RunningHub 全能视频 workflow 已经有 IPAdapter 节点，建议让云端 workflow 根据 `control_mode` 决定走人物一致、产品一致、风格参考或普通首帧生视频分支。

## 推荐搭建步骤

1. 先搭基础 T2V / I2V 分支：prompt / negative / width / height / frame_count / fps / seed -> video sampler -> SaveVideo。
2. 加 `LoadImage` 参考图入口，作为首帧或视觉参考。
3. 加 IPAdapter Character 分支：参考图进入 CLIPVision/IPAdapter/FaceID 类节点，约束人物身份；视频模型仍负责运动。
4. 加 IPAdapter Product 分支：参考图进入产品/主体保持分支，控制产品外观一致。
5. 加普通 B-roll / transition 分支：无参考图或弱参考图，只依赖提示词和镜头描述。
6. OpenPose / DWPose 不要用单张图控制整段视频；如果需要动作控制，应使用 `pose_video` 或姿态序列单独走 pose-video 分支。
7. 用 `control_mode` 或等价 switch/selector 决定走 `first_frame`、`ipadapter_character`、`ipadapter_product`、`openpose_motion` 等分支。
8. 最终只输出画面视频。不要输出模型音频；本系统的 TTS / FFmpeg 负责配音、字幕和最终混音。

默认 `runninghub_node_info_list_preset.json` 只放兼容旧 LTX I2V 模板的安全节点映射。真正接入 IPAdapter 后，请参考 `workflow_contract.json` 和 `node_info_extended_example.json`，把你实际导出的节点 ID 填到管理台。
