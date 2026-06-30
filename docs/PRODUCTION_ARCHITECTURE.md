# 通用视频生产四层架构

本系统从“数字员工直接决定 ComfyUI/DAG 细节”调整为四层架构。第一版目标是先稳定抽象边界，同时保持旧 `image_prompts` / `video_prompts` 生产链可继续运行。

## 01 数字员工意图层

位置：

- `my_workspace/my_workflows/workflow_长视频全流程.json`
- `my_workspace/my_custom_staff/01_需求拆解专员/`
- `my_workspace/my_custom_staff/06_分镜生图设计师/`
- `my_workspace/my_custom_staff/07_视频生成执行员/`
- `my_workspace/my_custom_staff/20_语音字幕包装师/`
- `my_workspace/my_custom_staff/22_剪辑成片执行师/`

职责：

- 01 输出 `production_type`：`drama_story | product_promo | talking_avatar | asset_only | custom`。
- 06/07/20/22 输出 `production_intents.image/video/audio/package`。
- 员工保留旧兼容字段，但不再把 `workflow_id`、`workflow_mode`、`input_bindings` 当作长期主职责。

## 02 生产模板层

位置：

- `my_workspace/my_production_templates/production_templates.json`

职责：

- 按 `production_type` 提供默认生产模板。
- 声明每类 intent 推荐使用的能力、工作流 ID、工作流模式、输出槽位。
- 目前包含：
  - `drama_story`：漫剧剧情生产模板。
  - `product_promo`：带货短视频生产模板。
  - `talking_avatar`：数字人口播生产模板。
  - `asset_only`：素材生产模板。
  - `custom`：自定义保守模板。

原则：

- 模板只做“默认路由和推荐映射”，不写 RunningHub 节点 ID。
- 真实节点映射仍由工作流库和调试台配置负责。

## 03 意图编译层

位置：

- `my_workspace/my_codex_core/production_plan_compiler.py`

职责：

- 读取员工输出中的 `production_type` 和 `production_intents`。
- 选择生产模板。
- 把意图编译为：
  - `production_plan.json`
  - 兼容 `image_prompts`
  - 兼容 `video_prompts`
  - `visual_jobs`
  - `global_context`
- 保持旧字段可用，确保现有生产链不中断。

首期行为：

- `generate_three_frame_shot` 会展开为 start / middle / end 三张关键帧。
- `generate_three_frame_i2v_clip` 会自动绑定对应三帧的上游输出。
- `generate_talking_image` 会声明依赖 `local_tts`，最终 WAV 仍由系统运行阶段注入。
- 如果员工已经输出旧 `image_prompts` / `video_prompts`，编译器保留并去重合并。

## 04 执行调度适配层

位置：

- `my_workspace/my_codex_core/production_pipeline.py`
- `my_workspace/my_codex_core/production_graph.py`
- `my_workspace/my_codex_core/cloud_comfyui_adapter.py`
- `my_workspace/my_codex_core/local_tts_adapter.py`
- `my_workspace/my_codex_core/local_ffmpeg_adapter.py`

职责：

- `production_pipeline.py` 在自动生产时写出 `production_plan.json`。
- 继续写出 `comfyui_payload.json` 和 `production_graph.json`。
- `cloud_comfyui_adapter.py` 继续消费兼容 `image_prompts` / `video_prompts` 并展开 material jobs。
- TTS、BGM、字幕、FFmpeg 仍在 08 音画封装阶段执行。

## 当前边界

- 这一版不是完整替换调度器，而是把四层架构接入现有生产链。
- `production_plan_compiler.py` 暂时只做保守编译，不做跨任务优先级、显存调度或并行优化。
- ComfyUI 真实节点仍由工作流库 `nodeInfoList` 管理。
- 音频不进入 ComfyUI 分类；只有 `talking_image` 在执行阶段等待最终 WAV。

## 下一阶段建议

1. 在任务输出页展示 `production_plan.json` 的模板、意图和编译结果。
2. 为三套模板加入更细的镜头级流水线预设。
3. 把 `production_graph.json` 的生成从兼容 payload 进一步切到 `production_plan.visual_jobs`。
4. 增加模板调试器：输入一段员工输出，预览会编译出哪些 job。
