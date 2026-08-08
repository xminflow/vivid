/**
 * 表单控件。预约页和服务申请页共用同一套外观：一行一个字段，
 * 标签在左、输入在右，行与行之间只用一道发丝线分隔——和小程序里的表单一致。
 *
 * 桌面端把标签移到输入框上方并允许两列排布，窄屏保持左右布局，
 * 由调用方通过 grid 控制列数，这里只管单个字段自己的样子。
 */
import type { ReactNode } from 'react'

interface RowProps {
  label: string
  required?: boolean
  /** 右下角的补充说明，比如字数计数 */
  hint?: ReactNode
  children: ReactNode
  /** 跨两列（多行文本、上传组用） */
  wide?: boolean
}

export function FieldRow({ label, required, hint, children, wide }: RowProps) {
  return (
    <div className={wide ? 'sm:col-span-2' : undefined}>
      <label className="block">
        <span className="flex items-baseline gap-1 text-[13px] tracking-meta text-grigio">
          {label}
          {required && <span aria-hidden="true">*</span>}
          {required && <span className="sr-only">（必填）</span>}
        </span>
        <div className="mt-2 border-b border-hairline pb-2 focus-within:border-nero">
          {children}
        </div>
      </label>
      {hint && <div className="mt-1 text-right text-[12px] text-grigio-chiaro">{hint}</div>}
    </div>
  )
}

// 文字颜色不放进来：日期控件未选值时要整体变灰，而同优先级的两个 Tailwind 颜色类
// 叠加时，胜负由它们在生成的 CSS 里的先后决定、跟写在 className 里的顺序无关，
// 叠加会静默失效。所以颜色由各控件自己给。
const inputClass = 'w-full bg-transparent text-[15px] placeholder:text-grigio-chiaro focus:outline-none'

interface TextProps {
  value: string
  onChange: (v: string) => void
  placeholder?: string
  maxLength?: number
  /** phone 用 tel，number 用 numeric——手机上会调出对应键盘 */
  inputMode?: 'text' | 'tel' | 'numeric' | 'decimal'
  type?: string
  autoComplete?: string
  /** type=date 时的最早可选日期，让日期选择器直接禁掉过去的日子 */
  min?: string
}

export function TextInput({
  value,
  onChange,
  placeholder,
  maxLength,
  inputMode = 'text',
  type = 'text',
  autoComplete,
  min,
}: TextProps) {
  // 日期控件没有 placeholder，未选值时浏览器自己显示「年-月-日」，用的是 input 的
  // color。不按有没有值切换的话，这行空字段会是墨黑，比旁边的浅灰 placeholder 抢眼
  const dateEmpty = type === 'date' && !value

  return (
    <input
      className={`${inputClass} ${dateEmpty ? 'text-grigio-chiaro' : 'text-nero'}`}
      type={type}
      inputMode={inputMode}
      value={value}
      maxLength={maxLength}
      placeholder={placeholder}
      autoComplete={autoComplete}
      min={min}
      onChange={(e) => onChange(e.target.value)}
    />
  )
}

interface NumberProps extends Omit<TextProps, 'inputMode' | 'type'> {
  unit?: string
}

export function NumberInput({ value, onChange, placeholder, unit }: NumberProps) {
  return (
    <div className="flex items-baseline gap-2">
      <input
        className={`${inputClass} text-nero`}
        // type=number 在部分浏览器里会有加减小箭头，还会把非法输入吞成空串导致
        // 无法回显，所以用 text + inputMode 调数字键盘，自己过滤字符
        type="text"
        inputMode="decimal"
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value.replace(/[^\d.]/g, ''))}
      />
      {unit && <span className="shrink-0 text-[13px] text-grigio">{unit}</span>}
    </div>
  )
}

// 下拉是自绘的，实现和原因见 Select.tsx。这里 re-export，
// 调用方不用关心它跟其它控件不是同一个文件
export { SelectInput } from './Select'

interface TextareaProps {
  value: string
  onChange: (v: string) => void
  placeholder?: string
  maxLength?: number
  rows?: number
}

export function TextareaInput({
  value,
  onChange,
  placeholder,
  maxLength,
  rows = 3,
}: TextareaProps) {
  return (
    <textarea
      className={`${inputClass} resize-none text-nero`}
      rows={rows}
      value={value}
      maxLength={maxLength}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
    />
  )
}
