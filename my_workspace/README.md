# my_workspace

这是本地自定义自媒体工作区，用于维护自己的数字员工和内容工作流。

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
my_workflows/
  workflow_短视频全流程.json
  workflow_小红书图文.json
  workflow_开发外包.json
my_task_output/
my_memory/
  brand_profile.md
  character_bible.md
  style_guide.md
my_reference_images/
  .gitignore
```

## 使用顺序

1. 先选一个 `my_workflows` 下的工作流。
2. 按 workflow JSON 的 `steps` 顺序调用对应员工。
3. 每一步把上一位员工的输出交给下一位员工。
4. 视频类工作流先由 `06_分镜生图设计师` 生成分镜和关键帧生图方案。
5. 最后由 `07_视频生成执行员` 生成视频提示词、配音字幕和剪辑制作包。

## 自动化执行

离线生成提示词包：

```powershell
python my_workspace/run_flow.py --provider offline --workflow workflow_短视频全流程 --input "我要做一条抖音短视频，推广 AI 自动化开发服务，目标客户是中小企业老板。"
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

界面支持：

- 选择 `my_workflows` 里的工作流。
- 可填写 `任务名称`，用于生成更容易识别的任务输出目录和历史任务标题；留空则继续使用工作流名。
- 输入原始内容需求。
- 管理台首屏保留核心输入，模型接口、记忆继承、生图配置、视频生成和参考图放在折叠配置区中，减少配置项堆叠。
- 选择 `auto`、`offline` 或 `openai` 执行模式。
- 在 `API Key` 输入框填入密钥；密钥只用于本次运行，不写入任务输出文件。
- 如果使用中转站，在 `中转站 Base URL` 输入框填入兼容 OpenAI 的地址，例如 `https://你的中转站域名/v1`；留空则使用官方地址。
- 通过分组模型下拉选择主力模型、轻量模型、推理模型、旧版兼容模型，或选择自定义模型名。
- 在 `生图配置` 中选择生图工具、模型、尺寸、每镜头图片数、质量、风格、负面提示词和一致性重点；当前版本会把这些配置传给 `06_分镜生图设计师` 生成分镜和关键帧生图提示词，不直接调用生图平台 API。
- 在 `视频生成配置` 中选择视频工具、模型、画幅、时长和风格。工具下拉包含 Sora、Runway、Pika、Seedance、可灵、即梦、海螺、Luma 等；当前版本会把这些配置传给 `06_分镜生图设计师` 和 `07_视频生成执行员`，不直接调用视频平台 API。
- 可在 `视频生成配置` 中上传参考图，并标注用途和说明。选择图片后会显示本地缩略图预览；图片保存到 `my_reference_images/`，任务输入只记录本地路径和说明，供 `06_分镜生图设计师` 和 `07_视频生成执行员` 生成参考图使用方案。
- 在 `长期记忆` 中选择是否启用 `my_memory`，并可在 `继承历史任务` 中选择上一条任务来保持系列视频的人物和风格连续性。
- 管理台会把 API Key、Base URL、模型选择、生图配置和视频生成配置默认保存到当前浏览器的 `localStorage`，下次打开自动填回；可点击 `清除已保存配置` 删除。
- 一键运行并写入 `my_task_output`。
- 运行工作流时会显示进度条和每一步员工状态；后台通过 `/api/run-status` 查询当前运行状态。
- 查看历史任务、每一步的 `prompt.md`、`output.md` 和最终 `final_output.md`。
- 删除历史任务输出；删除只作用于 `my_task_output` 下对应的任务目录，不会删除工作流、员工或 `.gitignore`。

## 当前员工

- `01_需求拆解专员`：把想法拆成 brief。
- `02_短视频编导`：生成镜头、拍摄和剪辑方案。
- `03_口播脚本师`：生成口播稿、字幕和备选开头结尾。
- `04_标题封面优化师`：生成标题、封面文案、标签和关键词。
- `05_内容合规审核官`：检查广告法、平台、版权和事实风险。
- `06_分镜生图设计师`：生成分镜总表、关键帧生图提示词、参考图使用策略和连续性控制说明。
- `07_视频生成执行员`：基于分镜生图方案生成视频模型提示词、TTS 配音稿、字幕草案和剪辑合成说明。
