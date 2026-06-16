# 剪辑节奏画面素材

用途：用 LTX-Video 2.3 生成适合后期配音、字幕和剪辑节奏的视频画面素材。

职责边界：

- ComfyUI 只负责生成画面素材。
- 不向 ComfyUI 传入配音文本、SRT 字幕或字幕样式。
- 配音音频、字幕、混音、硬字幕和最终导出交给 `20_语音字幕包装师`、`22_剪辑成片执行师` 或本地 FFmpeg。

关键 API 映射：

```text
2483.text  正向提示词
2612.text  负向提示词
2004.image 参考图 / 首帧图
4977.value bypass_i2v，必须为 false 才启用参考图
3159.strength 图生视频参考强度
```

文件：

```text
workflow_canvas.json
  可直接导入 ComfyUI 画布的剪辑节奏画面素材模板。
api_template.json
  API 格式参数模板，可上传到管理台“导入 API JSON 自动识别”。
runninghub_node_info_list_preset.json
  RunningHub nodeInfoList 初始映射，可复制到管理台“ComfyUI 节点映射 JSON”。
```
