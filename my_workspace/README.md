# my_workspace

这是本地自定义工作区，用于维护自己的数字员工和内容工作流。当前包含自媒体内容工作流，也新增了 Unity 3D 游戏上架 Steam 的游戏制作工作流。

## 目录

```text
my_custom_staff/
  01_需求拆解专员/
  02_短视频编导/
  03_口播脚本师/
  04_标题封面优化师/
  05_内容合规审核官/
  06_分镜生图设计师/
  07_视频生成执行员/
  20_语音字幕包装师/
  21_ComfyUI成片编排师/
  22_剪辑成片执行师/
  23_长视频策划编导/
  08_游戏项目编排制片人/
  09_游戏创意与系统设计师/
  10_Unity3D架构工程师/
  11_关卡叙事设计师/
  12_3D美术技术指导/
  13_游戏音频与体验设计师/
  14_Steam发行与测试经理/
  15_软件市场需求分析师/
  16_AI员工平台产品经理/
  17_AI员工平台工作流架构师/
  18_AI员工平台软件架构师/
  19_AI员工平台增长验证师/
my_workflows/
  workflow_短视频全流程.json
  workflow_长视频全流程.json
  workflow_小红书图文.json
  workflow_开发外包.json
  workflow_Unity3D游戏Steam上架.json
  workflow_软件市场机会分析.json
  workflow_AI员工工作流平台设计.json
my_task_output/
my_memory/
  brand_profile.md
  character_bible.md
  style_guide.md
my_reference_images/
  .gitignore
my_knowledge_base/
  .gitignore
my_local_models/
  local_model_presets.json
my_action_workspace/
  .gitignore
my_action_logs/
  .gitignore
my_deploy/
  OFFLINE_DEPLOY.md
```

## 使用顺序

1. 先选一个 `my_workflows` 下的工作流。
2. 按 workflow JSON 的 `steps` 顺序调用对应员工。
3. 每一步把上一位员工的输出交给下一位员工。
4. 视频类工作流先由 `06_分镜生图设计师` 生成分镜和关键帧生图方案。
5. 由 `07_视频生成执行员` 生成视频画面提示词和视频片段执行方案。
6. 由 `20_语音字幕包装师` 统一生成 TTS 配音稿、SRT 字幕草案、BGM、音效和混音建议。
7. 由 `21_ComfyUI素材编排师` 把 AI 图片、AI 视频、语音字幕参数和自动化要求整理成 ComfyUI / RunningHub 素材或预览参数。
8. 由 `22_剪辑成片执行师` 把脚本、素材片段、配音、SRT 字幕和封面整理成剪辑工具可执行的最终成片方案，负责最终硬字幕、最终混音和导出规格。
9. 长视频工作流额外由 `23_长视频策划编导` 负责章节结构、留存节奏和长视频素材规划。
10. 游戏类工作流由 `08` 到 `14` 号员工协作，从项目编排、GDD、Unity 架构、关卡叙事、3D 美术技术、音频体验，到 Steam 发行测试制作包。
11. 软件市场机会分析工作流由 `15_软件市场需求分析师` 输出高潜力软件方向、MVP、获客渠道、商业化和风险验证。
12. AI 员工工作流平台设计由 `15` 到 `19` 号员工协作，输出平台定位、员工管理、工作流架构、技术架构和增长验证方案。

## 自动化执行

离线生成提示词包：

```powershell
python my_workspace/run_flow.py --provider offline --workflow workflow_短视频全流程 --input "我要做一条抖音短视频，推广 AI 自动化开发服务，目标客户是中小企业老板。"
```

离线生成 Unity 3D Steam 游戏制作包：

```powershell
python my_workspace/run_flow.py --provider offline --workflow workflow_Unity3D游戏Steam上架 --input "我要做一款 Unity 3D 第三人称探索解谜游戏，上架 Steam。团队规模是单人或两人小团队，先做 20-30 分钟 Demo。"
```

离线生成软件市场机会分析：

```powershell
python my_workspace/run_flow.py --provider offline --workflow workflow_软件市场机会分析 --input "目标中国中小企业和个人开发者市场，团队1-2人，擅长Python、Web和AI API，希望找可MVP验证的软件方向。"
```

离线生成 AI 员工工作流平台设计：

```powershell
python my_workspace/run_flow.py --provider offline --workflow workflow_AI员工工作流平台设计 --input "我要做中小企业AI员工工作流平台，以my_custom_staff里的自定义员工为核心，能管理数字员工、运行工作流、查看任务输出，先自用跑通再销售。"
```

有 `OPENAI_API_KEY` 时自动调用模型：

```powershell
$env:OPENAI_API_KEY="你的 API Key"
$env:OPENAI_MODEL="gpt-4.1-mini"
python my_workspace/run_flow.py --workflow workflow_短视频全流程 --input "你的内容需求"
```

如果使用中转站，还需要设置 OpenAI-compatible Base URL：

```powershell
$env:OPENAI_API_KEY="你的中转站 Key"
$env:OPENAI_BASE_URL="https://你的中转站域名/v1"
python my_workspace/run_flow.py --provider openai --model gpt-5.5 --workflow workflow_短视频全流程 --input "你的内容需求"
```

也可以只对单次 CLI 运行传入 API Key：

```powershell
python my_workspace/run_flow.py --provider openai --api-key "你的 API Key" --base-url "https://你的中转站域名/v1" --model gpt-5.5 --workflow workflow_短视频全流程 --input "你的内容需求"
```

执行后会在 `my_task_output/task_时间戳_工作流名/` 下生成：

```text
input.md
workflow.json
step_01_*/system.md
step_01_*/prompt.md
step_01_*/output.md
...
final_output.md
run_summary.json
```

其中 `final_output.md` 是汇总后的最终产品文件。

`my_task_output` 默认忽略具体任务产物，避免把每次生成结果误提交到 Git；需要归档某次产物时，可以手动复制到其他目录或调整 `.gitignore`。

## 长期记忆与历史继承

长期记忆文件位于：

```text
my_memory/
  brand_profile.md
  character_bible.md
  style_guide.md
```

管理台默认把 `my_memory` 作为“视频输出长期记忆”，只注入到视频输出后段员工，例如分镜、生图/生视频素材、语音字幕、ComfyUI 素材编排和剪辑成片。它不会再默认影响需求拆解、脚本、游戏、软件分析等全部工作流。

如确实需要让所有员工都读取长期记忆，可在管理台选择 `全流程使用 my_memory（高级）`。

管理台还支持 `继承历史任务`：选择某个历史任务后，可以选择“只参考上次最终成品”，或选择“参考上次需求和最终成品”，作为本次任务的上下文。

## 可视化管理界面

启动本地管理台：

```powershell
python my_workspace/web_app.py
```

然后打开：

```text
http://127.0.0.1:8765
```

一站式本地启动：

```powershell
.\start_local.ps1
```

或直接双击项目根目录：

```text
start_local.bat
```

默认会检查并启动 Ollama，确认 `qwen3:8b-q4_K_M` 模型存在，启动管理台，并打开浏览器。第一次使用如果本机没有模型，会通过 `ollama pull qwen3:8b-q4_K_M` 下载模型。

默认模型目录已经指向项目内：

```text
runtime/models/
```

也就是说，用 `start_local.ps1` 启动时，Ollama 会优先使用项目内模型目录，而不是默认的 `C:\Users\<用户名>\.ollama\models`。

可指定模型：

```powershell
.\start_local.ps1 -Model "qwen3:8b"
```

界面支持：

- 选择 `my_workflows` 里的工作流。
- 点击 `游戏示例` 会自动切换到 `Unity3D游戏Steam上架` 工作流，并填入一份适合单人/小团队的 Unity 3D Steam 游戏需求。
- 点击 `长视频示例` 会自动切换到 `长视频全流程` 工作流，并填入一份 12-18 分钟 AI 员工工作流平台长视频示例需求。
- 可填写 `任务名称`，用于生成更容易识别的任务输出目录和历史任务标题；留空则继续使用工作流名。
- 输入原始内容需求。
- 管理台首屏保留核心输入、模型接口、记忆继承、全自动生成、ComfyUI 映射和参考图；生图/生视频提示词不再单独填写，由工作流员工自动生成。
- `系统状态` 页面可查看 Python、任务输出目录、知识库目录、动作工作区、Ollama 命令和 Ollama 模型服务状态。
- `模型接口配置` 里可以点击 `一键本地离线模式`，自动填好 Ollama、`local`、`http://127.0.0.1:11434/v1` 和 `qwen3:8b-q4_K_M`。
- `模型接口配置` 里可以设置 `模型超时`。本地 Ollama 推荐 900 秒；长流程或上下文很长时可以调到 1800 秒。
- 使用 Ollama 本地地址运行工作流前，管理台会先检测当前模型接口是否可用，失败时会阻止运行并显示错误。
- 如果某一步失败，管理台会自动打开该任务输出；失败步骤也会写入 `output.md` 和 `error.json`，便于定位。
- `系统状态` 页面内置首次启动向导，按一键启动、本地模型、连接测试、知识库、示例工作流顺序引导配置。
- 可在 `全自动生成` 中选择只生成视频制作包、生成视频加语音字幕制作包、调用生图/生视频 API，或预留 ComfyUI 自动化素材/预览路径；当前会自动整理 `production_manifest.json`、生图提示词、视频提示词、语音字幕包、ComfyUI 参数包、字幕、配音文本和剪辑清单，供后续 API 适配器继续执行。
- 可在 `全自动生成` 中启用 `VoxCPM2 本地仿声`，上传本人授权参考音频后，本地生成 `audio/voiceover.wav`；字幕和语音由 20 号员工维护，最终硬字幕和最终混音默认交给 22 号员工和剪辑工具。
- 可在 `全自动生成 -> 剪辑/预览工具` 中选择 `ffmpeg`。系统会优先查找 `runtime/ffmpeg/bin/ffmpeg.exe`，其次查找 `runtime/ffmpeg/ffmpeg.exe` 和系统 PATH；当任务目录里已有 `video_clips/` 视频、`generated_images/` 图片或 `audio/voiceover.wav` 时，会尝试输出 `final_video.mp4`。缺少 FFmpeg 或素材不足时会写入 `local_ffmpeg_manifest.json` 并跳过，不中断工作流。
- 可在 `全自动生成 -> ComfyUI 工作流库` 中配置 6 个默认槽位：图生图/文生图、图生视频、参考图保持一致性、B-roll 素材生成、字幕预览合成、音频字幕视频片段预览合成。下拉选择某个槽位会加载该槽位已保存的接口、节点映射、轮询时间和用途说明，保存后写入当前浏览器 `localStorage`，刷新页面仍会恢复；列表区只展示当前选中槽位；运行时会把当前槽位和库状态传给员工，但 API Key 不写入任务输出。
- 选择 `auto`、`offline` 或 `openai` 执行模式。
- 在 `API Key` 输入框填入密钥；密钥只用于本次运行，不写入任务输出文件。
- 如果使用中转站，在 `中转站 Base URL` 输入框填入兼容 OpenAI 的地址，例如 `https://你的中转站域名/v1`；留空则使用官方地址。
- 通过分组模型下拉选择主力模型、轻量模型、推理模型、旧版兼容模型，或选择自定义模型名。
- 可在 `模型接口配置` 里选择本地模型预设，例如 Ollama、LM Studio、vLLM、Xinference；选择后会自动填入本地 Base URL、local API Key，并切换到自定义模型名。
- 可点击 `测试当前模型接口`，管理台会调用 OpenAI-compatible `/chat/completions` 做一次连通性测试。
- 可在 `记忆与继承` 里上传 `.md/.txt/.json/.csv` 到 `my_knowledge_base`，并选择是否把本地知识库追加到本次工作流输入。
- 生图和生视频不再提供单独输入区；画面、镜头和生成提示词交给 `02_短视频编导`、`06_分镜生图设计师`、`07_视频生成执行员` 在工作流内生成。
- 可在 `参考图` 区域上传图片，并标注用途和说明。选择图片后会显示本地缩略图预览；图片保存到 `my_reference_images/`，任务输入只记录本地路径和说明，供 06、07、20、21 号员工生成参考图使用方案和 ComfyUI 参数映射。
- 在 `长期记忆` 中选择记忆作用域。默认只在视频输出阶段使用 `my_memory`，用于保持系列视频的人物、品牌和风格连续性；不要默认污染所有工作流。
- 管理台会把 API Key、Base URL、模型选择、ComfyUI 工作流库槽位配置、当前 ComfyUI 映射和参考图说明默认保存到当前浏览器的 `localStorage`，下次打开自动填回；可点击 `清除已保存配置` 删除。
- 一键运行并写入 `my_task_output`。
- 运行工作流时会显示进度条和每一步员工状态；后台通过 `/api/run-status` 查询当前运行状态。
- 页面操作按钮点击会立即在右上角显示“正在处理”的浮动提示，并在对应状态区域显示成功或错误结果；主标签页切换只更新当前页面，不弹浮动提示。
- 表单控件默认顶对齐；ComfyUI 节点映射区会保持导入文件和轮询超时控件为普通高度，避免被左侧 JSON 输入框拉伸；导入 API JSON 后识别出的节点参数会放进限高滚动面板，避免节点过多时把页面撑长。
- 查看历史任务、每一步的 `prompt.md`、`output.md` 和最终 `final_output.md`。
- 在 `任务输出` 中直接编辑并保存 `final_output.md` 或任一步骤的 `output.md`；编辑步骤输出后可点击 `重建最终汇总` 更新 `final_output.md`。
- 对某个步骤的 `output.md` 可点击 `重跑当前步骤`，系统会复用原工作流、原始输入和前置步骤输出，只覆盖该步骤并重建最终汇总。
- 对失败或中断的任务可点击 `继续任务`，系统会从第一个带 `error.json`、缺少 `output.md` 或输出为空的步骤继续执行，并写回同一个任务目录。
- 可点击 `导出产品包`，把任务结果整理到 `export_package/`；短视频会导出视频制作包、字幕、镜头清单和提示词 JSON，图文、游戏、软件分析和平台设计会导出对应交付文件。
- 运行前可选择 `产品类型`，自动匹配推荐工作流、任务名、示例需求、画幅和基础输出配置。
- 删除历史任务输出；删除只作用于 `my_task_output` 下对应的任务目录，不会删除工作流、员工或 `.gitignore`。
- 在 `数字员工管理` 中查看、新建、编辑、保存、删除 `my_custom_staff` 下的自定义员工；保存时会写入员工目录的 `agent.md` 和 `flow_rule.json`，并校验 `flow_rule.json` 是否是合法 JSON。
- 在 `工作流` 页面查看、新建、编辑、保存、删除 `my_workflows` 下的工作流 JSON；可用员工下拉列表组装步骤，调整步骤顺序，并同步到 `运行工作流` 下拉列表。

## Unity 3D Steam 游戏工作流

工作流文件：

```text
my_workflows/workflow_Unity3D游戏Steam上架.json
```

团队来源与职责：

- `08_游戏项目编排制片人`：来自 AgentsOrchestrator 与工作室制片人能力，负责项目范围、里程碑、质量门禁和风险降级。
- `09_游戏创意与系统设计师`：来自游戏设计师能力，负责 GDD、核心循环、机制、成长和调参假设。
- `10_Unity3D架构工程师`：来自 Unity 架构师能力，负责 Unity 工程结构、ScriptableObject 数据、组件和开发任务。
- `11_关卡叙事设计师`：来自关卡设计师与叙事设计师能力，负责灰盒关卡、流线、环境叙事和玩家引导。
- `12_3D美术技术指导`：来自技术美术和 Unity Shader Graph 美术师能力，负责美术风格、资产规格、材质、LOD 和性能预算。
- `13_游戏音频与体验设计师`：来自游戏音频工程师能力，负责音效事件、音乐状态、空间音频和交互反馈。
- `14_Steam发行与测试经理`：来自应用商店优化和测试能力，负责 Steam 商店页、发行素材、测试计划、性能验收和最终制作包。

推荐使用方式：

1. 启动管理台：`python my_workspace/web_app.py`。
2. 打开 `http://127.0.0.1:8765`。
3. 工作流选择 `Unity3D游戏Steam上架`，或点击 `游戏示例`。
4. 在 `原始需求` 里写清楚游戏类型、目标玩家、参考游戏、团队规模、周期、预算、是否已有素材。
5. 执行模式先用 `offline` 检查结构；确认方向后再填 API Key，用 `openai` 或 `auto` 生成更完整内容。
6. 运行后查看左侧任务输出，优先阅读 `final_output.md`，再按步骤查看每位员工的 `output.md`。

最终输出会给到一个游戏制作包，包括项目路线、GDD、Unity 工程架构、关卡白盒方案、3D 美术技术规范、音频体验方案、Steam 商店页和测试发行清单。它不会直接创建 Unity 工程或上传 Steam；它是用于指导后续开发和发行准备的制作蓝图。

## 软件市场机会分析工作流

工作流文件：

```text
my_workflows/workflow_软件市场机会分析.json
```

员工：

- `15_软件市场需求分析师`：来自趋势研究员、产品经理、反馈分析师、增长黑客、应用商店优化师、快速原型师和软件架构师能力，负责筛选高潜力软件方向。

推荐输入：

```text
目标市场：中国中小企业/个人创作者/跨境卖家/本地生活商家
团队规模：1-2 人
技术能力：Python、Web、AI API、微信生态
周期：2-6 周做 MVP
已有渠道：短视频账号、私域、行业客户、开源社区
避开方向：重监管、重硬件、重线下交付
```

输出会包含前十软件机会、目标用户、痛点、MVP形态、首批获客渠道、收费方式、风险、机会评分和第一周验证动作。

## AI 员工工作流平台设计

工作流文件：

```text
my_workflows/workflow_AI员工工作流平台设计.json
```

团队：

- `15_软件市场需求分析师`：分析平台市场机会和切入方向。
- `16_AI员工平台产品经理`：定义平台定位、MVP、用户旅程和员工管理需求。
- `17_AI员工平台工作流架构师`：设计员工、工作流、任务、上下文、输出和状态机。
- `18_AI员工平台软件架构师`：设计本地版管理台架构、API、页面、数据模型和未来 SaaS 演进。
- `19_AI员工平台增长验证师`：设计首批客户、Demo、话术、定价和 7/30/90 天验证计划。

当前管理台已经支持基础数字员工管理，数据源就是：

```text
my_custom_staff/<员工文件夹>/agent.md
my_custom_staff/<员工文件夹>/flow_rule.json
```

这套平台当前仍是本地文件版，适合先自用跑通和服务式交付；后续如果要面向客户开放账号、权限、计费和多人协作，需要再迁移到数据库、队列和用户系统。

## 全自动生产框架

选择 `全自动生成` 中任一非关闭模式后，任务目录会额外生成：

```text
production_manifest.json
auto_production.md
image_prompts/storyboard_image_prompts.md
generated_images/
video_prompts/video_generation_prompts.md
video_clips/
audio/audio_subtitle_package.md
audio/voiceover.txt
subtitles.srt
comfyui/comfyui_plan.md
comfyui/comfyui_payload.json
edit_checklist.md
```

执行模式含义：

```text
只生成视频制作包：低成本，只整理生图、生视频和合成材料。
生成视频 + 语音字幕制作包：增加 TTS、SRT 字幕、BGM、音效和混音建议，由 20 号员工统一维护。
调用生图/生视频 API：读取 RunningHub 等配置并调用已接入适配器。
ComfyUI 素材/预览草稿：预留高算力自动化路径，输出 ComfyUI 参数包，建议用于关键帧、B-roll、视频片段、字幕预览或自动化草稿；最终硬字幕、最终混音和最终导出仍以剪辑阶段为准。
```

当前版本会生成可执行资产包和 manifest；选择 `调用生图/生视频 API` 时会调用已接入的生图/生视频适配器。选择 `ComfyUI 素材/预览草稿` 且剪辑/预览工具为 `RunningHub / 云端 ComfyUI（素材/预览）`、密钥、接口地址和 ComfyUI 工作流接口都已填写时，会调用 ComfyUI 素材/预览适配器。

ComfyUI 素材/预览配置说明：

```text
ComfyUI 平台密钥：RunningHub 或云端 ComfyUI API Key，只用于本次请求，不写入输出文件。
ComfyUI 平台接口地址：例如 https://www.runninghub.cn/openapi/v2。
ComfyUI 素材/预览工作流接口：例如 /run/workflow/你的素材预览工作流ID 或 /run/ai-app/你的应用ID。
ComfyUI 节点映射 JSON：RunningHub nodeInfoList 数组，可使用 {{payload}}、{{prompt}}、{{voice_text}}、{{subtitle_srt}} 占位符。
ComfyUI 轮询超时：素材/预览任务通常更慢，默认 60 分钟。
```

可以在管理台直接导入 ComfyUI 导出的 API JSON。管理台会在浏览器本地识别 API JSON 中可覆盖的节点输入，例如 `CLIPTextEncode.text`、`LoadImage.image`、`KSampler.seed/steps/cfg/denoise`、`EmptySD3LatentImage.width/height`，然后让你勾选哪些字段要传参，并自动生成 `nodeInfoList`。保存当前工作流库槽位后，会清空本次导入文件和临时识别列表，只保留已经保存到槽位里的 `nodeInfoList`。

项目内置了一个长视频通用模板：

```text
my_workspace/comfyui_workflows/long_video_universal/
  long_video_universal_api_template.json
  runninghub_node_info_list_preset.json
  payload_example.json
  README.md
```

这个模板优先用于管理台参数识别和 RunningHub 映射预设：只把正向提示词、负向提示词、参考图、配音文本、字幕 SRT 和完整制作包暴露为动态参数；模型、采样器、分辨率、转场和素材生成逻辑建议固定在真实 ComfyUI / RunningHub 工作流里维护。字幕 SRT 可传给 ComfyUI 做预览或自动化草稿，最终硬字幕、最终混音和最终导出默认交给剪辑工具处理。

另外，项目内置了对应管理台 6 个 ComfyUI 工作流库槽位的画布模板：

```text
my_workspace/comfyui_workflows/workflow_library/
  01_image_z_image_turbo/
  02_ltx_video_2_3/
  03_reference_consistency/
  04_broll_material/
  05_subtitle_preview/
  06_audio_subtitle_video_preview/
```

每个槽位目录都包含：

```text
workflow_canvas.json
  可导入 ComfyUI 画布的模板。
api_template.json
  可上传到管理台“导入 API JSON 自动识别”的参数模板。
runninghub_node_info_list_preset.json
  RunningHub nodeInfoList 初始映射。
```

`01_image_z_image_turbo` 是 Z-Image Turbo 文生图/关键帧生图模板，不是图生图。需要参考图保持人物、产品或风格一致时，优先使用 `03_reference_consistency`，或在 ComfyUI 中搭 Z-Image Turbo + ControlNet / IP-Adapter / 图像编辑类节点后重新导出 API JSON。

生图默认使用 Z-Image Turbo；生视频、B-roll、字幕/音频预览素材默认使用 LTX-Video 2.3。先把 `workflow_canvas.json` 导入 ComfyUI 跑通，再从 ComfyUI / RunningHub 导出 API JSON 给管理台识别，最后保存到对应工作流库槽位。

注意：

```text
用于自动识别的是 ComfyUI API 格式 JSON，也就是节点以 "39"、"86" 这类 ID 为顶层 key 的文件。
普通画布工作流 JSON 主要用于导入 ComfyUI 继续编辑，不能直接用于自动生成 nodeInfoList。
```

ComfyUI 适配器会读取 `comfyui/comfyui_payload.json`，提交任务后轮询结果，并把返回的 mp4、音频、图片等结果下载到 `comfyui/` 目录。若缺少密钥、Base URL 或 endpoint，会标记为 skipped，不会发起请求。

## 本地 FFmpeg 自动成片

当前本地 FFmpeg 适配器已经接入自动生产管线。它不会替代 22 号员工的剪辑判断，但可以在素材已存在时生成一个可检查的本地成片草稿或最终导出文件。

查找顺序：

```text
runtime/ffmpeg/bin/ffmpeg.exe
runtime/ffmpeg/ffmpeg.exe
系统 PATH 中的 ffmpeg
```

项目根目录提供安装脚本：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install_ffmpeg_runtime.ps1
```

脚本会下载 Windows 静态 FFmpeg 包并安装到 `runtime/ffmpeg/`；该目录被 Git 忽略，不会提交二进制。

运行条件：

```text
全自动生成模式不是关闭
剪辑/预览工具选择 ffmpeg
任务目录里至少存在以下任一类素材：
- video_clips/*.mp4 / *.mov / *.mkv / *.webm
- generated_images/*.png / *.jpg / *.jpeg / *.webp
- audio/voiceover.wav / audio/voiceover.mp3
```

输出文件：

```text
final_video.mp4
local_ffmpeg_manifest.json
local_ffmpeg_command.txt
local_ffmpeg_stdout.txt
local_ffmpeg_stderr.txt
```

字幕目前保留为 sidecar `subtitles.srt`，由剪辑工具最终硬字幕烧录；本地 FFmpeg 先负责把可用视频/图片/音频合成为视频文件。若缺少 FFmpeg 或素材不足，`local_ffmpeg_manifest.json` 会记录 skipped 原因。

## 离线部署与动作执行

离线部署说明位于：

```text
my_deploy/OFFLINE_DEPLOY.md
```

## 最近管理台更新

- `start_local.ps1` 现在优先使用项目内 `runtime/ollama/ollama.exe`，并默认把模型目录设置为 `runtime/models`。
- 默认启动会重启旧的 Ollama/Web 监听进程，避免旧进程继续使用 C 盘模型目录或旧版管理台；需要保留旧进程时可加 `-KeepExistingOllama` 或 `-KeepExistingWeb`。
- `数字员工管理` 页面已优化为左侧员工库、右侧编辑器布局；左侧支持按员工名称、编号或角色搜索，员工卡片改为更紧凑的列表样式。

当前离线部署基础能力包括：

- 本地模型预设：`my_local_models/local_model_presets.json`。
- 本地知识库：`my_knowledge_base/`，运行时可追加到提示词。
- 受限动作执行：员工输出末尾可以提供 JSON 动作块，系统只执行 `mkdir`、`create_file`、`write_json`、`open_url`、`fetch_url`、`open_workspace_path`。
- 动作边界：文件动作只能写入 `my_action_workspace/`；浏览器动作只允许 http/https URL；打开本地路径时只能打开 `my_action_workspace/` 内部文件或文件夹。
- 动作日志：每次触发动作时，会在对应任务目录写入 `action_log.json`。

动作 JSON 示例：

```json
{
  "actions": [
    {
      "action": "mkdir",
      "params": {
        "path": "demo"
      }
    },
    {
      "action": "create_file",
      "params": {
        "path": "demo/readme.md",
        "content": "内容",
        "overwrite": false
      }
    },
    {
      "action": "open_url",
      "params": {
        "url": "https://example.com"
      }
    },
    {
      "action": "fetch_url",
      "params": {
        "url": "https://example.com",
        "path": "web/example.txt",
        "overwrite": true
      }
    }
  ]
}
```

当前不支持删除文件、运行 shell、修改任意系统路径、自动推送代码、控制鼠标键盘或直接调用付费媒体 API。后续要做完整执行型智能体，需要再加审批层、权限模型、动作队列和审计日志。

## 当前员工

- `01_需求拆解专员`：把想法拆成 brief。
- `02_短视频编导`：生成镜头、拍摄和剪辑方案。
- `03_口播脚本师`：生成口播稿、字幕和备选开头结尾。
- `04_标题封面优化师`：生成标题、封面文案、标签和关键词。
- `05_内容合规审核官`：检查广告法、平台、版权和事实风险。
- `06_分镜生图设计师`：生成分镜总表、关键帧生图提示词、参考图使用策略和连续性控制说明。
- `07_视频生成执行员`：基于分镜生图方案生成视频画面提示词、镜头清单和视频片段执行方案。
- `20_语音字幕包装师`：基于脚本和视频片段方案统一生成 TTS 配音稿、SRT 字幕、BGM、音效和混音建议。
- `21_ComfyUI素材编排师`：把生图、生视频、语音字幕参数和自动化要求整理成 ComfyUI / RunningHub 素材节点参数、预览方案和回退方案。
- `22_剪辑成片执行师`：把脚本、AI 素材片段、配音、SRT 字幕和封面整理成剪辑工具可执行的最终成片方案，负责最终硬字幕、最终混音和导出规格。
- `23_长视频策划编导`：把长视频主题拆成章节结构、开场钩子、中段留存、素材规划和剪辑节奏建议。
- `08_游戏项目编排制片人`：为 Unity 3D Steam 游戏建立项目路线、MVP、里程碑和质量门禁。
- `09_游戏创意与系统设计师`：生成 GDD 核心方案、机制规格、核心循环和调参假设。
- `10_Unity3D架构工程师`：生成 Unity 工程结构、ScriptableObject 数据、组件和开发任务。
- `11_关卡叙事设计师`：生成关卡流程、白盒规格、环境叙事和玩家引导方案。
- `12_3D美术技术指导`：生成 3D 美术资产、材质、Shader、LOD 和性能预算方案。
- `13_游戏音频与体验设计师`：生成音效事件、自适应音乐、空间音频和交互反馈方案。
- `14_Steam发行与测试经理`：生成 Steam 商店页、发行素材、测试计划和最终制作包。
- `15_软件市场需求分析师`：生成高潜力软件方向排行榜、MVP建议、获客渠道、商业化路径和验证计划。
- `16_AI员工平台产品经理`：定义 AI 员工工作流平台定位、MVP、核心旅程和路线图。
- `17_AI员工平台工作流架构师`：设计员工管理、工作流管理、任务状态机和上下文传递规则。
- `18_AI员工平台软件架构师`：生成平台技术架构、API、页面结构、数据模型和开发任务。
- `19_AI员工平台增长验证师`：生成首批客户、Demo脚本、销售话术、定价试探和增长验证计划。

## RunningHub 云端生图接入

早期版本支持在 `全自动生成 -> 调用 API 生成` 模式下单独调用 RunningHub 云端 ComfyUI 生图工作流。当前管理台已隐藏单独的生图配置入口，推荐使用 `全自动生成 -> ComfyUI 素材/预览草稿` 和 API JSON 节点映射统一调用。

使用方式：

1. 打开管理台 `http://127.0.0.1:8765`。
2. 在 `全自动生成` 中选择 `调用 API 生成`。
3. 在 `全自动生成` 中选择 ComfyUI 素材/预览草稿路径。
4. 填入 ComfyUI 平台密钥、接口地址和工作流接口。
5. 导入 ComfyUI API JSON，选择需要映射的节点参数。
6. 使用 `{{prompt}}`、`{{image_prompt}}`、`{{video_prompt}}`、`{{reference_image}}` 等占位符注入工作流员工生成的内容。

运行成功后，任务目录会额外生成：

```text
generated_images/runninghub_01.png
generated_images/cloud_image_manifest.json
generated_images/runninghub_submit_response.json
generated_images/runninghub_query_response.json
production_manifest.json
```

注意：

- API Key 只通过本次请求传给适配器，不写入 `production_manifest.json` 或任务输出。
- RunningHub 返回的结果 URL 通常 24 小时有效，适配器会立即下载到本地 `generated_images/`。
- 单独生图 API 入口不再作为普通管理台流程使用；统一走 ComfyUI/RunningHub 成片配置。

## RunningHub 云端视频接入

早期版本也支持在 `全自动生成 -> 调用 API 生成` 模式下调用 RunningHub AI App 视频接口。当前管理台已隐藏单独的视频配置入口，推荐统一走 ComfyUI/RunningHub 素材/预览配置。

使用方式：

1. 在 `全自动生成` 中选择 ComfyUI 素材/预览草稿路径。
2. 填入 ComfyUI 平台密钥、接口地址和工作流接口。
3. 导入 ComfyUI API JSON，选择视频相关节点参数。
4. 如需把员工生成的视频提示词写入某个节点，可在节点映射中使用 `{{video_prompt}}` 或 `{{prompt}}`。

运行成功后，任务目录会额外生成：

```text
video_clips/runninghub_video_01.mp4
video_clips/cloud_video_manifest.json
video_clips/runninghub_video_submit_response.json
video_clips/runninghub_video_query_response.json
```

注意：

- 视频 API Key 只通过本次请求传给适配器，不写入 `production_manifest.json` 或任务输出。
- 如果 RunningHub AI App 需要固定节点输入，先从 RunningHub 页面确认节点参数，再填入 `nodeInfoList JSON`。
- 当前框架负责提交、轮询、下载视频文件；生成质量取决于 RunningHub AI App 内部工作流配置。

## VoxCPM2 本地配音接入

管理台支持把 `20_语音字幕包装师` 生成的 `TTS 配音稿` 交给本机 VoxCPM2 生成配音音频。`20_语音字幕包装师` 同时维护字幕文本和 SRT 草案，避免配音文案和字幕不一致。

使用方式：

1. 先在本机单独安装并跑通 VoxCPM2。
2. 打开管理台 `http://127.0.0.1:8765`。
3. 在 `全自动生成` 中选择 `生成视频制作包 + 语音字幕包` 或 `ComfyUI 素材/预览草稿`。
4. `本地配音` 选择 `VoxCPM2 本地仿声`。
5. 上传本人或已授权的参考音频。
6. 可选填写参考音频原文。
7. 保持或修改 VoxCPM2 命令模板。
8. 运行工作流。

默认命令模板：

```text
voxcpm clone --text-file {text_file} --reference-audio {reference_audio} --output {output_file}
```

支持的占位符：

```text
{text}              配音文本内容
{text_file}         配音文本文件路径，默认 audio/voxcpm2_voice_text.txt
{reference_audio}   上传后的参考音频路径
{reference_text}    参考音频原文
{output_file}       输出音频路径，默认 audio/voiceover.wav
```

输出文件：

```text
audio/voiceover.txt
audio/voiceover.wav
audio/voxcpm2_voice_text.txt
audio/local_tts_manifest.json
audio/voxcpm2_stdout.txt
audio/voxcpm2_stderr.txt
```

注意：

- 参考音频会保存到 `my_voice_samples/`，该目录默认不提交到 Git。
- 本项目不内置 VoxCPM2 大模型，只负责调用本机已安装的 VoxCPM2 命令。
- VoxCPM2 具体 CLI 参数如果和默认模板不同，直接在管理台修改命令模板即可。
- 只使用本人声音或已获得授权的声音样本。

## 生图和生视频参数

管理台的生图/视频配置已经收敛为普通用户只提供：

```text
原始需求、参考图
```

具体生图/生视频提示词由 `02_短视频编导` 先根据原始需求设计内容和镜头方向，再由 `06_分镜生图设计师` 拆成关键帧方案，由 `07_视频生成执行员` 整理成视频生成包，并供 `21_ComfyUI素材编排师` 整理节点映射。AI 图片和 AI 视频只作为素材片段，语音和字幕由 `20_语音字幕包装师` 统一维护，最终硬字幕、混音和成片由 `22_剪辑成片执行师` 交给剪辑工具完成。

其他模型参数不要在管理台普通界面里堆叠，统一放在实际工作流里维护：

```text
生图：模型、尺寸、seed、steps、CFG、denoise、采样器、LoRA、ControlNet、IP-Adapter、负向词、RunningHub endpoint、nodeInfoList
生视频：模型、画幅、时长、FPS、分辨率、运动强度、镜头运动、首帧强度、负向词、RunningHub endpoint、nodeInfoList
```

这些高级字段仍保留在代码中，用于兼容旧设置和适配器结构；但默认不再暴露给普通用户填写，运行时也不会把单独的生图/生视频配置追加到员工输入。

`image_config` 和 `video_config` 仍保留空的兼容结构，避免破坏制作包和适配器代码；实际执行建议走 ComfyUI/RunningHub 素材/预览配置和 API JSON 节点映射。
