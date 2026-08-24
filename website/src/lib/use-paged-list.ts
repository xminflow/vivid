import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'

import type { PageParams, PagedResult } from './paging'

interface PagedList<T, Q, R> {
  items: T[]
  total: number
  /**
   * 最近一次响应的完整报文，请求失败或还没回来时是 null。
   *
   * 列表接口除了 items/total 之外还带别的字段时用它——案例审核队列的 counts
   * 就是这么来的：四个分页的数量随列表一起下发，前端不为一个角标再发一次请求。
   */
  response: R | null
  page: number
  pageSize: number
  loading: boolean
  filters: Q
  /** 改一个筛选条件；不立即请求，交给调用方决定（下拉选完就查，输入框等回车） */
  setFilter: <K extends keyof Q>(key: K, value: Q[K]) => void
  /** 带最新筛选条件重新查，并回到第一页 */
  search: () => void
  /**
   * 重查当前页，筛选条件和页码都不动。删掉一条记录后用它刷新。
   * 删的是本页最后一条且不在第一页时会自动退一页——否则会停在一张空表上，
   * 看着像「删了一条，剩下的全没了」
   */
  reload: () => void
  reset: () => void
  setPage: (page: number) => void
  setPageSize: (size: number) => void
}

/**
 * 分页列表的公共逻辑：两个列表页的取数、翻页、筛选、报错方式完全一样，
 * 差别只在调哪个接口、带哪些筛选条件。
 */
export function usePagedList<
  T,
  Q extends Record<string, string>,
  R extends PagedResult<T> = PagedResult<T>,
>(fetcher: (params: Q & PageParams) => Promise<R>, blank: Q): PagedList<T, Q, R> {
  const [items, setItems] = useState<T[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPageState] = useState(1)
  const [pageSize, setPageSizeState] = useState(20)
  const [loading, setLoading] = useState(false)
  const [filters, setFilters] = useState<Q>(blank)
  const [response, setResponse] = useState<R | null>(null)

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
        setResponse(body)
      } catch (err) {
        if (id !== requestId.current) return
        // 出错时清空列表并弹提示：留着上一次的数据会让人以为筛选条件已经生效了
        setItems([])
        setTotal(0)
        setResponse(null)
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

  const setFilter = useCallback<PagedList<T, Q, R>['setFilter']>((key, value) => {
    filtersRef.current = { ...filtersRef.current, [key]: value }
    setFilters(filtersRef.current)
  }, [])

  // 改筛选条件后要回到第一页——停在第 5 页多半是空的，看着像没数据
  const search = useCallback(() => {
    setPageState(1)
    void load(1, pageSize)
  }, [load, pageSize])

  // 调用时 items 还是删除前的那一份，所以「只剩一条」就等于「删完这页就空了」
  const reload = useCallback(() => {
    const target = items.length <= 1 && page > 1 ? page - 1 : page
    setPageState(target)
    void load(target, pageSize)
  }, [items.length, load, page, pageSize])

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
    response,
    page,
    pageSize,
    loading,
    filters,
    setFilter,
    search,
    reload,
    reset,
    setPage,
    setPageSize,
  }
}
