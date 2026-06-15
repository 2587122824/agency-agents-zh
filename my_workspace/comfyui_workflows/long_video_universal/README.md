# 长视频通用 ComfyUI 工作流模板

这个目录用于给管理台接入“长视频通用成片”工作流。

重点不是把所有视频逻辑写死在管理台，而是把长视频制作中稳定需要外部传入的字段暴露出来：

- 正向提示词：`{{prompt}}`
- 负向提示词：`{{negative_prompt}}`
- 参考图：`{{reference_image}}`
- 配音文案：`{{voice_text}}`
- 字幕 SRT：`{{subtitle_srt}}`
- 完整制作包：`{{payload}}`

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

1. 在 ComfyUI / RunningHub 中先搭好自己的长视频工作流，固定模型、采样器、分辨率、帧率、转场、字幕样式、音频混合等复杂参数。
2. 导出 ComfyUI API JSON。
3. 在管理台“全自动生成 -> ComfyUI 全自动成片”里导入 API JSON。
4. 只勾选需要外部动态传入的字段，一般只需要：
   - 主提示词
   - 负向提示词
   - 参考图
   - 配音文案
   - 字幕 SRT
   - 完整制作包 JSON
5. 把 `runninghub_node_info_list_preset.json` 作为初始映射，再按你真实工作流节点 ID 调整。

## 长视频建议

长视频不要一次性让 ComfyUI 生成完整成片。更稳定的方案是：

1. 工作流员工生成章节、分镜、配音、字幕和素材清单。
2. ComfyUI 生成关键帧、短视频片段、B-roll、字幕预览或自动成片预览。
3. 最终剪辑成片仍交给 22_剪辑成片执行师或专业剪辑工具。

这样成本更可控，失败时也能只重跑某个片段。

