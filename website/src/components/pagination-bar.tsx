import { ChevronLeft, ChevronRight } from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

const PAGE_SIZES = [20, 50, 100]

interface Props {
  total: number
  page: number
  pageSize: number
  onPageChange: (page: number) => void
  onPageSizeChange: (size: number) => void
}

export function PaginationBar({
  total,
  page,
  pageSize,
  onPageChange,
  onPageSizeChange,
}: Props) {
  // 一条都没有时也当作 1 页，否则页码会显示成「第 1 / 0 页」
  const pageCount = Math.max(1, Math.ceil(total / pageSize))

  return (
    <div className="flex flex-wrap items-center justify-end gap-3 py-3 text-sm">
      <span className="mr-auto text-muted-foreground">共 {total.toLocaleString()} 条</span>
      <Select value={String(pageSize)} onValueChange={(value) => onPageSizeChange(Number(value))}>
        <SelectTrigger className="w-28" aria-label="每页条数">
          <SelectValue />
        </SelectTrigger>
        <SelectContent position="popper" align="start">
          {PAGE_SIZES.map((size) => (
            <SelectItem key={size} value={String(size)}>
              {size} 条/页
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <span className="text-muted-foreground">
        第 {page} / {pageCount} 页
      </span>
      <Button
        variant="outline"
        size="icon-sm"
        aria-label="上一页"
        disabled={page <= 1}
        onClick={() => onPageChange(page - 1)}
      >
        <ChevronLeft />
      </Button>
      <Button
        variant="outline"
        size="icon-sm"
        aria-label="下一页"
        disabled={page >= pageCount}
        onClick={() => onPageChange(page + 1)}
      >
        <ChevronRight />
      </Button>
    </div>
  )
}
