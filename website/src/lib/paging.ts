// 分页列表的共用类型。服务端所有列表接口都是这一个形状（见 server/app/paging.py），
// 与具体是哪个小程序的哪张表无关，所以放在 lib 而不是某个 feature 下。

export interface PagedResult<T> {
  ok: true
  total: number
  page: number
  pageSize: number
  items: T[]
}

export type PageParams = {
  page: number
  pageSize: number
}
