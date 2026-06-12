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
my_workflows/
  workflow_短视频全流程.json
  workflow_小红书图文.json
  workflow_开发外包.json
my_task_output/
  task_001/
  task_002/
```

## 使用顺序

1. 先选一个 `my_workflows` 下的工作流。
2. 按 workflow JSON 的 `steps` 顺序调用对应员工。
3. 每一步把上一位员工的输出交给下一位员工。
4. 最后由 `05_内容合规审核官` 做发布前审核。

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
- 输入原始内容需求。
- 选择 `auto`、`offline` 或 `openai` 执行模式。
- 一键运行并写入 `my_task_output`。
- 查看历史任务、每一步的 `prompt.md`、`output.md` 和最终 `final_output.md`。

## 当前员工

- `01_需求拆解专员`：把想法拆成 brief。
- `02_短视频编导`：生成镜头、拍摄和剪辑方案。
- `03_口播脚本师`：生成口播稿、字幕和备选开头结尾。
- `04_标题封面优化师`：生成标题、封面文案、标签和关键词。
- `05_内容合规审核官`：检查广告法、平台、版权和事实风险。
