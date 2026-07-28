# V2 项目生产策略实现记录

> 完成日期：2026-07-28

## 目标

把视频生产方式从后续制作阶段前移到项目创建阶段。用户在首次智能体调用前做一次明确选择，此后所有智能体、影响分析和生产编译器都遵守同一个不可变合同。

## 合同

新增 `project-production-profile.v1`：

- `video_motion_strategy`: `three_frame | adaptive | start_end`
- `keyframe_strategy`: `adaptive | omni_reference`
- `enforcement`: 当前固定为 `required`
- 审计字段：版本号、选择人、必需帧角色、合同 hash、创建时间

当前能力矩阵：

| 维度 | 策略 | 状态 | 原因 |
|---|---|---|---|
| 视频运动 | `three_frame` | 可用、推荐 | 已有首中尾三帧真实工作流与 447/448/449 三父图绑定 |
| 视频运动 | `adaptive` | 可用 | 继续按冻结分镜能力要求匹配当前工作流 |
| 视频运动 | `start_end` | 禁用 | 尚无经过真实验证的首尾帧工作流 |
| 关键帧 | `adaptive` | 可用 | 使用当前人物一致性、风格参考或文本关键帧能力 |
| 关键帧 | `omni_reference` | 禁用 | 尚无 V2 多参考输入合同、节点绑定和真实成功证据 |

## 实现

- 项目创建 API 要求显式 `production_profile`；不可用模式返回结构化 409，不采用隐式默认。
- `ProjectProductionProfileVersion` 保存不可变版本。迁移 `20260728_39` 将历史项目显式回填为 `adaptive/adaptive` 和 `selected_by=migration`。
- Dashboard 创建表单展示两个策略维度、可用性、推荐项和禁用原因；未确认生产策略不能创建项目。
- profile ID、版本、事实和 hash 注入创作制片人、内容策划、分镜导演、制作规划、质量审核和剪辑助理的 Manifest。
- 生产影响分析和 ProductionSnapshot 冻结相同 profile，控制台投影显示策略、版本和 hash。

## 三帧确定性门禁

首中尾三帧项目不只依赖 Prompt：

1. 内容策划合同要求内容可拆成短镜头、单一主动作和三个连续状态。
2. 分镜导演必须为每个镜头设置 `multi_frame_required=true`，并提供 start/middle/end。
3. 制作规划调用前拒绝缺少三帧合同的镜头。
4. 制作规划只允许选择 `multi_frame_video_generation`。
5. 影响分析与生产编译再次检查三帧要求、槽位和父图输入。

任一层不一致即明确阻断，不降级为首帧、首尾帧或纯文本视频，不自动替换工作流。

## 配置与验证

- 配置 v57 已发布，v56 已退役。
- 5 个已启用文本智能体合同升级：创作制片人 v6/v16、内容策划 v3/v7、分镜导演 v3/v7、制作规划 v2/v3、剪辑助理 v2/v2。
- 质量审核代码合同升级为 v2/v2，但仍未发布视觉模型。
- 后端完整基线：`290 passed`。
- Python compileall、Alembic 迁移测试和前端生产构建通过。
- 未执行 RunningHub、CosyVoice 或其他付费生产调用。
