# 离线部署说明

## 推荐启动顺序

1. 启动本地模型服务，例如 Ollama、LM Studio、vLLM 或 Xinference。
2. 在管理台 `模型接口配置` 中选择本地模型预设，或填写 OpenAI-compatible Base URL。
3. 点击 `测试模型连接`。
4. 上传本地知识库文件。
5. 运行工作流。

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
