# 长视频通用 ComfyUI 工作流模板

这个目录用于给管理台接入“长视频通用 ComfyUI 素材/预览工作流”。

核心原则：

- 语音和字幕由 `20_语音字幕包装师` 统一产出。
- ComfyUI 不接收语音和 SRT，只负责关键帧、视频片段和 B-roll 等画面素材。
- 最终硬字幕、最终混音和最终导出默认交给 `22_剪辑成片执行师` 和剪辑工具。
- 长视频不要默认一次性让 ComfyUI 生成完整成片，优先按章节和素材片段拆开执行。

## 动态参数

模板只暴露稳定需要外部传入的字段：

- 正向提示词：`{{prompt}}`
- 负向提示词：`{{negative_prompt}}`
- 参考图：`{{reference_image}}`
- 完整制作包：`{{payload}}`

模型、采样器、分辨率、帧率、转场等复杂参数建议固定在真实 ComfyUI / RunningHub 工作流里维护。配音、字幕样式、音频混合和最终导出放到剪辑步骤。

## 文件说明

```text
long_video_universal_api_template.json
  ComfyUI API 格式参数模板。可直接上传到管理台“导入 ComfyUI API JSON”，用于自动识别可传参字段。

runninghub_node_info_list_preset.json
  RunningHub nodeInfoList 预设。可复制到管理台“ComfyUI 节点映射 JSON”。

payload_example.json
  长视频制作包示例。用于说明 21_ComfyUI素材编排师应输出什么结构。
```

## 推荐使用方式

1. 在 ComfyUI / RunningHub 中搭好真实工作流，固定模型、采样器、分辨率、帧率、转场和输出目录。
2. 导出 ComfyUI API JSON。
3. 在管理台“全自动生成 -> ComfyUI 全自动成片”里导入 API JSON。
4. 只勾选需要外部动态传入的字段，一般只需要：
   - 主提示词
   - 负向提示词
   - 参考图
   - 完整制作包 JSON
5. 把 `runninghub_node_info_list_preset.json` 作为初始映射，再按真实工作流节点 ID 调整。

## 长视频建议

更稳定的长视频方案：

1. 工作流员工生成章节、分镜、配音、SRT 和素材清单。
2. 本地优先生成 `audio/voiceover.wav` 和 `subtitles.srt`。
3. ComfyUI 生成关键帧、短视频片段和 B-roll 画面素材。
4. 最终剪辑成片交给 `22_剪辑成片执行师` 或专业剪辑工具。
5. 字幕错字、时间轴、样式和最终硬字幕在剪辑阶段处理，避免因为字幕小改动重跑高算力 ComfyUI。
