/**
 * 表单底部的提交条与结果提示。
 *
 * 提交结果用页内提示而不是 alert：alert 会打断滚动位置，
 * 而且在 iOS Safari 上样式完全不可控，与整页的极简版面冲突。
 */
interface Props {
  submitting: boolean
  disabled?: boolean
  label: string
  /** 提交失败时服务端给的中文提示，直接展示 */
  error?: string
  onSubmit: () => void
}

export function SubmitBar({ submitting, disabled, label, error, onSubmit }: Props) {
  return (
    <div className="mt-10">
      {error && (
        // role=alert：提交失败要让读屏软件立刻播报，用户可能焦点还停在某个输入框上
        <p role="alert" className="mb-4 text-[13px] text-nero">
          {error}
        </p>
      )}
      <button
        type="button"
        disabled={submitting || disabled}
        onClick={onSubmit}
        className="w-full bg-nero py-4 text-[14px] tracking-meta text-bianco transition-opacity hover:opacity-85 disabled:opacity-40 sm:w-auto sm:px-16"
      >
        {submitting ? '提交中…' : label}
      </button>
    </div>
  )
}

interface DoneProps {
  title: string
  body: string
  actionLabel: string
  onAction: () => void
}

/** 提交成功后整屏替换成回执：表单已经没用了，留着只会让人以为要再交一次 */
export function SubmitDone({ title, body, actionLabel, onAction }: DoneProps) {
  return (
    <div className="py-20 text-center">
      <h2 className="text-[22px] tracking-display">{title}</h2>
      <p className="mt-4 whitespace-pre-line text-[14px] leading-loose text-grigio">{body}</p>
      <button
        type="button"
        onClick={onAction}
        className="mt-10 border border-nero px-12 py-3 text-[13px] tracking-meta transition-colors hover:bg-nero hover:text-bianco"
      >
        {actionLabel}
      </button>
    </div>
  )
}
