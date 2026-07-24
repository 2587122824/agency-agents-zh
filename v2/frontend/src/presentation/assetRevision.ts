import type { AssetRevisionRequest } from '../api/types'

export type AssetRevisionScope = AssetRevisionRequest['issue_scope']
export type AssetRevisionCode = AssetRevisionRequest['issue_code']

export interface AssetRevisionReasonOption {
  code: AssetRevisionCode
  label: string
}

export const assetRevisionReasons: Record<AssetRevisionScope, AssetRevisionReasonOption[]> = {
  storyboard: [
    { code: 'content_mismatch', label: '画面内容不对' },
    { code: 'action_mismatch', label: '人物动作不对' },
    { code: 'composition_mismatch', label: '构图需要调整' },
    { code: 'character_setup_mismatch', label: '人物设定不对' },
    { code: 'other', label: '其他分镜问题' },
  ],
  production: [
    { code: 'identity_inconsistent', label: '人物不一致' },
    { code: 'visual_artifact', label: '画面有瑕疵' },
    { code: 'composition_deviation', label: '构图偏离分镜' },
    { code: 'text_error', label: '画面文字错误' },
    { code: 'low_clarity', label: '清晰度不足' },
    { code: 'style_mismatch', label: '风格不符合' },
    { code: 'other', label: '其他生成问题' },
  ],
  editing: [
    { code: 'exclude_asset', label: '不要使用这条素材' },
    { code: 'shorten_clip', label: '缩短片段' },
    { code: 'reorder_clip', label: '调整顺序' },
    { code: 'replace_clip', label: '替换片段' },
    { code: 'other', label: '其他剪辑问题' },
  ],
}

export function assetRevisionReasonLabel(scope: AssetRevisionScope, code: AssetRevisionCode) {
  return assetRevisionReasons[scope].find(option => option.code === code)?.label ?? code
}

export function assetRevisionSummary(request: Pick<AssetRevisionRequest, 'issue_scope' | 'issue_code' | 'rationale'>) {
  const reason = assetRevisionReasonLabel(request.issue_scope, request.issue_code)
  return request.rationale ? `${reason}：${request.rationale}` : reason
}
