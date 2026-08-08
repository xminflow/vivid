import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'

import type { PagedResult } from './types'
import type { PageParams } from './api'

interface PagedList<T, Q> {
  items: T[]
  total: number
  page: number
  pageSize: number
  loading: boolean
  filters: Q
  /** 改一个筛选条件；不立即请求，交给调用方决定（下拉选完就查，输入框等回车） */
  setFilter: <K extends keyof Q>(key: K, value: Q[K]) => void
  /** 带最新筛选条件重新查，并回到第一页 */
  search: () => void
  reset: () => void
  setPage: (page: number) => void
  setPageSize: (size: number) => void
}

/**
 * 分页列表的公共逻辑：两个列表页的取数、翻页、筛选、报错方式完全一样，
 * 差别只在调哪个接口、带哪些筛选条件。
 */
export function usePagedList<T, Q extends Record<string, string>>(
  fetcher: (params: Q & PageParams) => Promise<PagedResult<T>>,
  blank: Q,
): PagedList<T, Q> {
  const [items, setItems] = useState<T[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPageState] = useState(1)
  const [pageSize, setPageSizeState] = useState(20)
  const [loading, setLoading] = useState(false)
  const [filters, setFilters] = useState<Q>(blank)

  // 筛选条件放 ref 一份：setFilter 之后紧接着 search()，用 state 读到的还是上一次的值
  const filtersRef = useRef(filters)
  // 慢请求可能后返回。只认最后一次发出的请求，否则翻页快一点就会看到上一页的数据
  const requestId = useRef(0)

  const load = useCallback(
    async (nextPage: number, nextPageSize: number) => {
      const id = ++requestId.current
      setLoading(true)
      try {
        const body = await fetcher({
          ...filtersRef.current,
          page: nextPage,
          pageSize: nextPageSize,
        })
        if (id !== requestId.current) return
        setItems(body.items)
        setTotal(body.total)
      } catch (err) {
        if (id !== requestId.current) return
        // 出错时清空列表并弹提示：留着上一次的数据会让人以为筛选条件已经生效了
        setItems([])
        setTotal(0)
        toast.error(err instanceof Error ? err.message : String(err))
      } finally {
        if (id === requestId.current) setLoading(false)
      }
    },
    [fetcher],
  )

  useEffect(() => {
    void load(1, 20)
  }, [load])

  const setFilter = useCallback<PagedList<T, Q>['setFilter']>((key, value) => {
    filtersRef.current = { ...filtersRef.current, [key]: value }
    setFilters(filtersRef.current)
  }, [])

  // 改筛选条件后要回到第一页——停在第 5 页多半是空的，看着像没数据
  const search = useCallback(() => {
    setPageState(1)
    void load(1, pageSize)
  }, [load, pageSize])

  const reset = useCallback(() => {
    filtersRef.current = blank
    setFilters(blank)
    setPageState(1)
    void load(1, pageSize)
  }, [blank, load, pageSize])

  const setPage = useCallback(
    (next: number) => {
      setPageState(next)
      void load(next, pageSize)
    },
    [load, pageSize],
  )

  const setPageSize = useCallback(
    (next: number) => {
      setPageSizeState(next)
      setPageState(1)
      void load(1, next)
    },
    [load],
  )

  return {
    items,
    total,
    page,
    pageSize,
    loading,
    filters,
    setFilter,
    search,
    reset,
    setPage,
    setPageSize,
  }
}

/** 2026-08-05T14:03:22+08:00 → 2026-08-05 14:03 */
export function formatTime(iso: string): string {
  if (!iso) return '—'
  const d = new Date(iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}
