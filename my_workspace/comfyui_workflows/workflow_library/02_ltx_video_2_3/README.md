# 图生视频

用途：用 LTX-Video 2.3 根据提示词和参考图生成短视频素材片段。

默认模型：

- 生视频：LTX-Video 2.3

文件：

```text
workflow_canvas.json
  可直接导入 ComfyUI 画布的 LTX-Video 2.3 图生视频模板。
api_template.json
  API 格式参数模板，可上传到管理台“导入 API JSON 自动识别”。
runninghub_node_info_list_preset.json
  RunningHub nodeInfoList 初始映射，可复制到管理台“ComfyUI 节点映射 JSON”。
```

运行前必须做：

1. 在 `LoadImage` 节点上传参考图。
2. 确认 `bypass_i2v` 节点为 `False`。如果它是 `True`，参考图分支会被绕过，上传图片不会影响结果。
3. 确认已安装 `ComfyUI-LTXVideo` 自定义节点。
4. 确认 LTX 2.3 checkpoint、Gemma text encoder、LTX LoRA 已下载，并在节点下拉中选择本机已有文件。

关键节点：

```text
2483.text  正向提示词
2612.text  负向提示词
2004.image 参考图 / 首帧图
4977.value bypass_i2v，必须为 false 才启用参考图
3159.strength 图生视频参考强度，默认 0.9
```

如果在管理台或 RunningHub 调用，不要继续使用旧的 `12.image` 示例节点。应使用 `runninghub_node_info_list_preset.json` 里的真实节点 ID，或者上传从 ComfyUI 导出的 API JSON 后重新勾选 `LoadImage.image`。

常见报错说明见：

```text
../LTX_VIDEO_2_3_MODEL_REQUIREMENTS.md
```
