# 参考图保持一致性

用途：用参考图约束人物、产品、服装、色调和品牌风格，再交给 LTX-Video 2.3 生成更一致的视频片段。

默认模型：

- 图像/关键帧：可先由 Z-Image Turbo 或人工图片提供
- 生视频：LTX-Video 2.3

文件：

```text
workflow_canvas.json
  可直接导入 ComfyUI 画布的参考图一致性模板。
api_template.json
  API 格式参数模板，可上传到管理台“导入 API JSON 自动识别”。
runninghub_node_info_list_preset.json
  RunningHub nodeInfoList 初始映射，可复制到管理台“ComfyUI 节点映射 JSON”。
```

关键节点：

```text
2483.text  正向提示词
2612.text  负向提示词
2004.image 参考图 / 首帧图
4977.value bypass_i2v，必须为 false 才启用参考图
3159.strength 图生视频参考强度，默认 1.0
```

运行前必须做：

1. 在 `LoadImage` 节点上传参考图。
2. 确认 `bypass_i2v` 节点为 `False`。如果它是 `True`，参考图分支会被绕过，上传图片不会影响结果。
3. 确认已安装 `ComfyUI-LTXVideo` 自定义节点。
4. 确认 LTX 2.3 checkpoint、Gemma text encoder、LTX LoRA 已下载，并在节点下拉中选择本机已有文件。

如果在管理台或 RunningHub 调用，不要继续使用旧的 `12.image` 示例节点。应使用 `runninghub_node_info_list_preset.json` 里的真实节点 ID，或者上传从 ComfyUI 导出的 API JSON 后重新勾选 `LoadImage.image`。
