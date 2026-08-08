import type { ReactNode } from 'react'

/** 详情抽屉里的一行「字段名 / 值」。值可能很长（备注、地址），所以标签定宽、值换行 */
export function DetailField({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex gap-3 border-b border-border py-2 text-sm last:border-b-0">
      <span className="w-28 shrink-0 text-muted-foreground">{label}</span>
      <span className="min-w-0 flex-1 break-words">{children}</span>
    </div>
  )
}
