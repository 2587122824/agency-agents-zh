# B-roll 素材生成

用途：用 LTX-Video 2.3 生成补画面、转场、氛围镜头和说明性画面。

默认模型：

- 视频素材：LTX-Video 2.3

文件：

```text
workflow_canvas.json
  可直接导入 ComfyUI 画布的 B-roll 视频素材模板。
api_template.json
  API 格式参数模板，可上传到管理台“导入 API JSON 自动识别”。
runninghub_node_info_list_preset.json
  RunningHub nodeInfoList 初始映射，可复制到管理台“ComfyUI 节点映射 JSON”。
```

关键 API 映射：

```text
2483.text  正向提示词
2612.text  负向提示词
2004.image 参考图 / 首帧图
4977.value bypass_i2v，必须为 false 才启用参考图
3159.strength 图生视频参考强度，默认 0.9
3059.width / 3059.height 输出尺寸，默认 960x544
3059.length / 4979.value 帧数，默认 121
```

职责边界：

- 本模板只负责生成可剪辑的视频画面素材。
- 不向 ComfyUI 传入配音文本、字幕文本或剪辑时间轴；这些交给语音字幕和最终剪辑步骤处理。
- 发布到 RunningHub 后，应以实际导出的 API JSON / 节点 ID 为准重新校准映射。
