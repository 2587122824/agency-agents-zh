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
5. 最后由 `07_视频生成执行员` 生成视频提示词、配音字幕和剪辑制作包。
6. 游戏类工作流由 `08` 到 `14` 号员工协作，从项目编排、GDD、Unity 架构、关卡叙事、3D 美术技术、音频体验，到 Steam 发行测试制作包。
7. 软件市场机会分析工作流由 `15_软件市场需求分析师` 输出高潜力软件方向、MVP、获客渠道、商业化和风险验证。
8. AI 员工工作流平台设计由 `15` 到 `19` 号员工协作，输出平台定位、员工管理、工作流架构、技术架构和增长验证方案。

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

管理台默认启用 `my_memory`，每次运行会把这些文件追加到工作流输入中，用于保持账号定位、人物一致性和视觉风格统一。

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
- 可填写 `任务名称`，用于生成更容易识别的任务输出目录和历史任务标题；留空则继续使用工作流名。
- 输入原始内容需求。
- 管理台首屏保留核心输入，模型接口、记忆继承、生图配置、视频生成和参考图放在折叠配置区中，减少配置项堆叠。
- `系统状态` 页面可查看 Python、任务输出目录、知识库目录、动作工作区、Ollama 命令和 Ollama 模型服务状态。
- `模型接口配置` 里可以点击 `一键本地离线模式`，自动填好 Ollama、`local`、`http://127.0.0.1:11434/v1` 和 `qwen3:8b-q4_K_M`。
- `模型接口配置` 里可以设置 `模型超时`。本地 Ollama 推荐 900 秒；长流程或上下文很长时可以调到 1800 秒。
- 使用 Ollama 本地地址运行工作流前，管理台会先检测当前模型接口是否可用，失败时会阻止运行并显示错误。
- 如果某一步失败，管理台会自动打开该任务输出；失败步骤也会写入 `output.md` 和 `error.json`，便于定位。
- `系统状态` 页面内置首次启动向导，按一键启动、本地模型、连接测试、知识库、示例工作流顺序引导配置。
- 可在 `全自动生成` 中选择生成生产资产包；当前会自动整理 `production_manifest.json`、生图提示词、视频提示词、字幕、配音文本和剪辑清单，供后续 API 适配器继续执行。
- 选择 `auto`、`offline` 或 `openai` 执行模式。
- 在 `API Key` 输入框填入密钥；密钥只用于本次运行，不写入任务输出文件。
- 如果使用中转站，在 `中转站 Base URL` 输入框填入兼容 OpenAI 的地址，例如 `https://你的中转站域名/v1`；留空则使用官方地址。
- 通过分组模型下拉选择主力模型、轻量模型、推理模型、旧版兼容模型，或选择自定义模型名。
- 可在 `模型接口配置` 里选择本地模型预设，例如 Ollama、LM Studio、vLLM、Xinference；选择后会自动填入本地 Base URL、local API Key，并切换到自定义模型名。
- 可点击 `测试当前模型接口`，管理台会调用 OpenAI-compatible `/chat/completions` 做一次连通性测试。
- 可在 `记忆与继承` 里上传 `.md/.txt/.json/.csv` 到 `my_knowledge_base`，并选择是否把本地知识库追加到本次工作流输入。
- 在 `生图配置` 中选择生图工具、模型、尺寸、每镜头图片数、质量、风格、负面提示词和一致性重点；当前版本会把这些配置传给 `06_分镜生图设计师` 生成分镜和关键帧生图提示词，不直接调用生图平台 API。
- 在 `视频生成配置` 中选择视频工具、模型、画幅、时长和风格。工具下拉包含 Sora、Runway、Pika、Seedance、可灵、即梦、海螺、Luma 等；当前版本会把这些配置传给 `06_分镜生图设计师` 和 `07_视频生成执行员`，不直接调用视频平台 API。
- 可在 `视频生成配置` 中上传参考图，并标注用途和说明。选择图片后会显示本地缩略图预览；图片保存到 `my_reference_images/`，任务输入只记录本地路径和说明，供 `06_分镜生图设计师` 和 `07_视频生成执行员` 生成参考图使用方案。
- 在 `长期记忆` 中选择是否启用 `my_memory`，并可在 `继承历史任务` 中选择上一条任务来保持系列视频的人物和风格连续性。
- 管理台会把 API Key、Base URL、模型选择、生图配置和视频生成配置默认保存到当前浏览器的 `localStorage`，下次打开自动填回；可点击 `清除已保存配置` 删除。
- 一键运行并写入 `my_task_output`。
- 运行工作流时会显示进度条和每一步员工状态；后台通过 `/api/run-status` 查询当前运行状态。
- 查看历史任务、每一步的 `prompt.md`、`output.md` 和最终 `final_output.md`。
- 在 `任务输出` 中直接编辑并保存 `final_output.md` 或任一步骤的 `output.md`；编辑步骤输出后可点击 `重建最终汇总` 更新 `final_output.md`。
- 对某个步骤的 `output.md` 可点击 `重跑当前步骤`，系统会复用原工作流、原始输入和前置步骤输出，只覆盖该步骤并重建最终汇总。
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

选择 `全自动生成 -> 生成生产资产包` 后，任务目录会额外生成：

```text
production_manifest.json
auto_production.md
image_prompts/storyboard_image_prompts.md
generated_images/
video_prompts/video_generation_prompts.md
video_clips/
audio/voiceover.txt
subtitles.srt
edit_checklist.md
```

当前版本先生成可执行资产包和 manifest，不直接调用第三方生图或生视频 API。后续接入平台 API 时，适配器读取 `production_manifest.json` 中的工具、模型、提示词文件和输出目录继续执行。

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
- `07_视频生成执行员`：基于分镜生图方案生成视频模型提示词、TTS 配音稿、字幕草案和剪辑合成说明。
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

管理台现在支持在 `全自动生成 -> 调用 API 生成` 模式下调用 RunningHub 云端 ComfyUI 生图工作流。

使用方式：

1. 打开管理台 `http://127.0.0.1:8765`。
2. 在 `全自动生成` 中选择 `调用 API 生成`。
3. 在 `生图配置` 中选择 `RunningHub Cloud ComfyUI`。
4. 填入 `生图平台 API Key`。
5. Base URL 使用 `https://www.runninghub.cn/openapi/v2`。
6. 工作流接口使用 `/run/workflow/2048294089858228226`。
7. `nodeInfoList JSON` 默认可填 `[]`；如果 RunningHub 工作流需要改节点参数，可填 RunningHub 要求的节点参数数组，并可在字符串中使用 `{{prompt}}` 注入 06 号员工生成的提示词。

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
- 当前只接入生图；视频生成仍先输出制作包和提示词，后续再接视频 API。

## RunningHub 云端视频接入

管理台现在也支持在 `全自动生成 -> 调用 API 生成` 模式下调用 RunningHub AI App 视频接口。

使用方式：

1. 在 `视频生成配置` 中选择 `RunningHub AI App`。
2. 填入 `视频平台 API Key`。
3. Base URL 使用 `https://www.runninghub.cn/openapi/v2`。
4. Video Endpoint 使用 `/run/ai-app/2066043648160133122`。
5. `RunningHub Video nodeInfoList JSON` 默认可填 `[]`；如需把 07 号员工生成的视频提示词写入某个节点，可在 JSON 字符串中使用 `{{prompt}}`。

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

## 生图和生视频参数

管理台的生图/视频配置已经精简为常用参数优先。多数模型内部参数保留在代码和配置中，但默认不在页面展示。

生图常用参数：

```text
工具、模型、尺寸/画幅、每镜头图片数、风格、质量、负面提示词、一致性重点、API Key、Base URL
```

生图隐藏高级参数：

```text
Seed、Guidance/CFG、Steps、Denoise Strength、Sampler、LoRA / ControlNet / IP-Adapter / Face reference、RunningHub Endpoint、nodeInfoList JSON、实例规格、轮询超时
```

生视频常用参数：

```text
工具、模型、画幅、目标时长、风格、负面提示词、运动强度、镜头运动、API Key、Base URL
```

生视频隐藏高级参数：

```text
Seed、FPS、分辨率、Guidance、Frames、Image Strength、Camera Path / Shot Notes、Audio / Subtitle Notes、Advanced Model Params、RunningHub Video Endpoint、nodeInfoList JSON、轮询超时
```

隐藏字段仍保留默认值并继续写入 `image_config` 和 `video_config`，避免破坏 RunningHub 适配器。API Key 仍只用于本次请求，不写入任务输出文件。
