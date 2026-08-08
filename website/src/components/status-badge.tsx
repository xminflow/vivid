import { Badge } from '@/components/ui/badge'
import type { StatusOption } from '@/features/antony/constants'

/**
 * 跟进状态徽标。颜色只表达「要不要处理」：待处理的显眼，处理完的收敛，
 * 不给每个状态一个新颜色——一屏几十行，颜色多了反而看不出重点。
 */
const TONE_CLASS: Record<StatusOption<string>['tone'], string> = {
  pending: 'bg-amber-100 text-amber-900 dark:bg-amber-400/15 dark:text-amber-200',
  active: 'bg-blue-100 text-blue-900 dark:bg-blue-400/15 dark:text-blue-200',
  done: 'bg-emerald-100 text-emerald-900 dark:bg-emerald-400/15 dark:text-emerald-200',
  muted: 'bg-muted text-muted-foreground',
}

export function StatusBadge({ status }: { status: StatusOption<string> }) {
  return <Badge className={TONE_CLASS[status.tone]}>{status.label}</Badge>
}
