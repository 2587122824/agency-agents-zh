# 片场 V2 RunningHub 图片/视频适配器实现

> 更新日期：2026-07-17
>
> 本阶段经用户确认，仅实现和注册适配器并使用本地假传输测试；未连接 RunningHub、未提交真实任务、未产生供应商费用。

## 1. 实现范围

- 新增独立 V2 `RunningHubAdapter`，不导入或调用 V1 适配器。
- 支持 `generate_keyframe` 与 `generate_i2v_clip` 两种明确工作类型。
- 新增可注入 `RunningHubTransport`；生产实现使用 `httpx`，测试全部使用内存假传输。
- 真实执行由 `V2_EXTERNAL_PROVIDER_EXECUTION_ENABLED` 控制，默认关闭。
- 凭据仍仅支持 `env://VARIABLE_NAME`，并同时受 `V2_CREDENTIAL_ENV_ALLOWLIST` 限制。
- 设置页把已注册但未授权真实执行的状态显示为“真实执行授权未开启”，不做网络探测。

## 2. 不可变执行合同

新创建的 WorkAttempt 使用 `production-work-request.v2`，冻结：

- Provider 地址、凭据引用、请求超时、轮询间隔和并发上限；
- RunningHub 工作流 ID、版本和完整 NodeInfoList；
- Shot 结构化输入、输出合同和视频规格；
- 唯一存储策略、允许 MIME、文件大小上限和本地根引用。

外部适配器不在执行时重新读取系统配置。旧 `production-work-request.v1` 缺少上述字段时明确阻断，不从数据库、V1 或其他配置版本补齐。

## 3. NodeInfoList 规则

适配器只解析配置中逐项声明的 `value_source`，当前支持：

- `shot.action`、`shot.composition`、`shot.duration_ms`
- `shot.face_visibility`、`shot.motion_requirement`、`shot.text_policy`
- `duration_ms`
- `source_image`
- `video_spec.width`、`video_spec.height`、`video_spec.fps`
- `video_spec.long_side`、`video_spec.frame_count`
- `seed`
- `literal:<JSON>`

不支持 `{{prompt}}`、`{{negative_prompt}}`、`{{reference_image}}` 等旧占位符。配置校验和执行前解析都会明确失败，不重写提示词、不猜测来源、不替换工作流。

I2V 必须声明且只声明一个 `source_image` 绑定，并消费必需父 WorkAttempt 响应清单中恰好一个本地图片输出。缺失、多个、类型错误、非本地 URI 或文件不存在均阻断；不会只取第一张继续执行。

## 4. 提交与恢复

外部执行采用持久化两阶段：

1. Worker 先把 Attempt 写为 `submitting` 并提交事务。
2. 适配器只提交一次。
3. 返回任务号后立即写入 `provider_task_id`，Attempt 进入 `submitted`。
4. 后续 Worker 周期只查询同一个任务号。
5. Worker 重启后继续轮询已保存任务号，不重新提交。
6. 若提交结果未知，或进程可能在任务号落库前中断，进入人工对账阻断。

没有自动重试、第二次付费提交、任务号猜测、状态修复或供应商降级。

## 5. 输出文件

成功结果下载到确定性目录：

`runtime://assets/providers/runninghub/<request_fingerprint>/output-<index>.<ext>`

响应清单记录本地 URI、MIME、SHA-256、字节数、素材类型、角色和供应商结果序号。输出仍需走现有显式注册、文件验证、QC 和人工审核流程；适配器不会自动登记、批准或跳过 QC。

## 6. 当前限制

- 真实执行默认关闭，尚未做 RunningHub 联网验证。
- 当前已发布的 V1 导入配置包含旧占位符，不能直接用于新适配器；必须由用户显式创建、校验并发布新的配置版本。
- 当前只连接本地 `v2.runtime.assets` 输出存储；OSS 未接入。
- 未实现 RunningHub 实际运行时长到实际 CostEvent 的记账。
- CosyVoice、FFmpeg、自动重试和人工对账命令不在本阶段范围。

## 7. 测试证据

- 默认关闭时零传输调用。
- 精确结构化 NodeInfoList 解析。
- 旧提示词占位符在传输前失败。
- I2V 单父图片验证和精确上传。
- 成功结果确定性下载、哈希与响应清单。
- Worker 先持久化任务号，重启后轮询且提交计数保持为一次。
- Provider 失败明确阻断；无自动重试和降级。
- 全部测试使用假传输，不访问网络。
