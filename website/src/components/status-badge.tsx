import { Badge } from '@/components/ui/badge'

/**
 * 状态徽标。颜色只表达「要不要处理」：待处理的显眼，处理完的收敛，
 * 不给每个状态一个新颜色——一屏几十行，颜色多了反而看不出重点。
 *
 * 类型定义在组件这一侧而不是某个 feature 的常量文件里：徽标是跨 feature 的
 * 通用组件，谁用它谁按这个形状给数据，不该反过来依赖某一个业务模块。
 */
export type StatusTone = 'pending' | 'active' | 'done' | 'muted'

export interface StatusMeta {
  label: string
  tone: StatusTone
}

const TONE_CLASS: Record<StatusTone, string> = {
  pending: 'bg-amber-100 text-amber-900 dark:bg-amber-400/15 dark:text-amber-200',
  active: 'bg-blue-100 text-blue-900 dark:bg-blue-400/15 dark:text-blue-200',
  done: 'bg-emerald-100 text-emerald-900 dark:bg-emerald-400/15 dark:text-emerald-200',
  muted: 'bg-muted text-muted-foreground',
}

export function StatusBadge({ status }: { status: StatusMeta }) {
  return <Badge className={TONE_CLASS[status.tone]}>{status.label}</Badge>
}
