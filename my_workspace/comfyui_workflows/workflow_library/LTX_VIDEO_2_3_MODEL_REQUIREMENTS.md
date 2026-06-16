# LTX-Video 2.3 模型与运行要求

第二个到第六个模板都基于 LTX-Video 2.3 官方 ComfyUI 示例。你截图里的报错含义如下。

## 参考图错误

```text
Invalid image file: reference.png
```

这表示 `LoadImage` 节点指向了不存在的图片。解决方式：

1. 在 ComfyUI 里找到 `LoadImage` 节点。
2. 点击上传按钮，上传你的参考图。
3. 不要直接运行占位文件名。

模板现在把占位名改成：

```text
upload_reference_image_first.png
```

它只是提醒你“先上传参考图”，不是真实文件。

## 模型列表错误

```text
Value not in list: lora_name ... not in list
Value not in list: text_encoder ... not in list
```

这表示你的 ComfyUI 模型目录里没有模板指定的模型文件，或文件名/目录和模板不一致。解决方式：

1. 安装 `ComfyUI-LTXVideo` 自定义节点。
2. 下载 LTX-Video 2.3 所需模型。
3. 在 ComfyUI 节点下拉列表中选择你本机实际存在的文件名。
4. 跑通后再导出 API JSON，上传到管理台自动识别节点映射。

模板默认引用的文件名：

```text
checkpoints/ltx-2.3-22b-dev-fp8.safetensors
text_encoders/gemma_3_12B_it.safetensors
loras/ltx-2.3-22b-distilled-lora-384-1.1.safetensors
```

实际位置以你的 ComfyUI 节点下拉列表为准。模板不能保证你的机器已经有这些模型。

## 推荐流程

1. 先在本地 ComfyUI 里把画布模板跑通。
2. 确认所有节点都不再报 `Value not in list`。
3. 导出 API JSON。
4. 在管理台导入 API JSON 自动识别。
5. 保存到对应的 ComfyUI 工作流库槽位。
