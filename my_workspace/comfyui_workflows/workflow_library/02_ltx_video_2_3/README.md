# 图生视频（动态 I2V + OpenPose 预览）

用途：用 LTX-Video 根据提示词和参考图生成动态视频素材。默认使用参考图作为首帧 / 视觉参考。画布内保留可见的 `DWPreprocessor -> ResizeImageMaskNode -> PreviewImage` OpenPose/DWPose 预览分支，但不接最终 `SaveVideo` 输出。

为什么不默认启用单图 OpenPose：

- 单张参考图只能提取一个静态姿态。
- 如果把这个静态姿态作为整段视频的 control image，模型会被锁在同一个姿势上，容易出现“画面几乎不动”。
- 之前接入的 LTX pose-control workflow component 会输出带音频的视频，和本系统后续 TTS/FFmpeg 合成职责冲突，可能产生杂音。

默认模型：

- 生视频：LTX-Video
- 最终配音、字幕、混音：交给本系统 TTS / FFmpeg 步骤处理

文件：

```text
workflow_canvas.json
  可直接导入 ComfyUI 画布的 LTX 图生视频模板。
api_template.json
  API 格式参数模板，可上传到管理台“导入 API JSON 自动识别”。
runninghub_node_info_list_preset.json
  RunningHub nodeInfoList 初始映射，可复制到管理台“ComfyUI 节点映射 JSON”。
```

关键节点：

```text
2483.text      正向提示词
2612.text      负向提示词
2004.image     参考图 / 首帧图
4977.value     bypass_i2v，必须为 false 才启用参考图
3159.strength  图生视频参考强度，默认 1.0
4823/4852      SaveVideo，默认接原 LTX I2V 动态输出
6101           DWPreprocessor，OpenPose/DWPose 控制预览节点
6102           ResizeImageMaskNode，缩放姿态预览图
6103           PreviewImage，只预览姿态图，不接成片输出
```

如果确实需要 OpenPose / ControlNet：

- 不要用单张人物参考图控制整段视频；应使用姿态视频、姿态序列，或者逐镜头动作参考。
- 应单独维护 pose-to-video 模板，让 `DWPreprocessor` 从动作视频/帧序列提取 pose sequence，再接 LTX pose-control。
- RunningHub 的 `nodeInfoList` 只能给云端 workflow 已有节点传参，不能凭空新增 ControlNet/OpenPose 节点。
