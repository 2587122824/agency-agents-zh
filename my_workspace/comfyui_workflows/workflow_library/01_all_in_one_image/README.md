# 全能图片

用途：统一承接数字员工输出的 `image_prompts` / `image_jobs`。员工只需要输出提示词、参考图和控制模式，系统把每个图片 job 转成固定 API 入参并调用该工作流。

## 文件怎么用

- `workflow_canvas.json`：导入 ComfyUI 画布用。打开 ComfyUI 后直接拖入页面，或用 Load 加载这个 JSON。
- `api_template.json`：给 RunningHub/ComfyUI API 调用参考，不是画布导入文件。
- `runninghub_node_info_list_preset.json`：给管理台“当前编辑槽位节点映射 JSON”使用。
- `workflow_contract.json` / `node_info_extended_example.json`：用于你后续把真实 IPAdapter、FaceID、风格参考、产品参考节点接进去时对照字段。

当前 `workflow_canvas.json` 是可导入的基础全能图片画布，基于项目里已验证的 Z-Image 图片模板。你导入后可以继续加 IPAdapter/FaceID/Control 分支；加完后再从 ComfyUI 导出 API JSON，回到管理台导入并校准真实节点 ID。

推荐统一字段：

```json
{
  "task_type": "txt2img | img2img | keyframe | cover | product | character",
  "prompt": "图片正向提示词",
  "negative_prompt": "负向提示词",
  "reference_image": "可选参考图",
  "control_mode": "none | reference_image | ipadapter_character | ipadapter_product | style_reference",
  "width": 1080,
  "height": 1920,
  "seed": -1
}
```

当前默认 preset 兼容现有 Z-Image Turbo 图片模板：

- `{{prompt}}`
- `{{negative_prompt}}`
- `{{reference_image}}`
- `{{has_reference_image}}`
- `{{task_type}}`
- `{{control_mode}}`
- `{{width}}`
- `{{height}}`
- `{{seed}}`

如果你的 RunningHub 全能图片 workflow 已经有 IPAdapter / FaceID / 风格参考节点，可以在管理台把这些节点映射到 `{{control_mode}}`、`{{reference_image}}` 和固定权重字段。

## 推荐搭建步骤

1. 先搭基础文生图分支：prompt / negative / width / height / seed -> sampler -> SaveImage。
2. 加 `LoadImage` 参考图入口，并用 boolean switch 区分无参考图和有参考图。
3. 加 img2img 分支：参考图编码到 latent，接 denoise。
4. 加 IPAdapter Character 分支：`LoadImage -> CLIPVision/IPAdapter -> model conditioning`。
5. 加 IPAdapter Product 分支：复用参考图入口，但使用更偏主体/产品保持的 IPAdapter 权重。
6. 加 Style Reference 分支：参考图只用于风格，不强锁人物或产品。
7. 用 `control_mode` 或等价 switch/selector 决定走哪个分支。
8. 保持最终只有图片输出，不要接视频、音频、字幕或剪辑节点。

默认 `runninghub_node_info_list_preset.json` 只放兼容旧图片模板的安全节点映射。真正接入 IPAdapter 后，请参考 `workflow_contract.json` 和 `node_info_extended_example.json`，把你实际导出的节点 ID 填到管理台。
