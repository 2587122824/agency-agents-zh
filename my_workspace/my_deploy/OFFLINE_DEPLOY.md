# 离线部署说明

## 推荐启动顺序

轻量一站式启动：

```powershell
.\start_local.ps1
```

或双击：

```text
start_local.bat
```

默认行为：

1. 查找 `ollama` 命令，或查找 `runtime/ollama/ollama.exe`。
2. 启动 Ollama 服务。
3. 检查默认模型 `qwen3:8b-q4_K_M`，如果缺失则执行 `ollama pull qwen3:8b-q4_K_M`。
4. 设置当前进程的 OpenAI-compatible 环境变量。
5. 启动 `my_workspace/web_app.py`。
6. 打开 `http://127.0.0.1:8765`。

默认模型目录：

```text
runtime/models/
```

`start_local.ps1` 会在启动 Ollama 前设置：

```powershell
$env:OLLAMA_MODELS="<项目根目录>\runtime\models"
```

这样一站式包可以把模型放在项目目录内，而不是依赖系统默认的 `C:\Users\<用户名>\.ollama\models`。

手动启动顺序：

1. 启动本地模型服务，例如 Ollama、LM Studio、vLLM 或 Xinference。
2. 在管理台 `系统状态` 页面检查 Python、目录写入权限和 Ollama 服务。
3. 在管理台 `模型接口配置` 中选择本地模型预设，或填写 OpenAI-compatible Base URL。
4. 点击 `测试当前模型接口`。
5. 上传本地知识库文件。
6. 运行工作流。

## 常用本地模型地址

```text
Ollama:    http://127.0.0.1:11434/v1
LM Studio: http://127.0.0.1:1234/v1
vLLM:      http://127.0.0.1:8000/v1
Xinference:http://127.0.0.1:9997/v1
```

## 动作执行边界

当前只支持这些动作：

```text
mkdir
create_file
write_json
```

所有动作都被限制写入：

```text
my_workspace/my_action_workspace/
```

暂不支持删除文件、运行 shell、推送代码、调用付费 API。此类动作后续需要审批层。
