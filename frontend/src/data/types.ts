/** 纯前端数据层的公共类型：与全栈版后端返回的结构保持一致，换回后端时页面不用改。 */

export type EntryRow = {
  id: number
  status: string
  pending: boolean
  abnormal: boolean
  [field: string]: string | number | boolean
}

export type ModuleMeta = {
  key: string
  name: string
  entity: string
  desc: string
  fields: string[]
  statuses: string[]
  actions: string[]
  actionTargets: Record<string, string>
  metrics: string[]
}

export type PageResult = {
  items: EntryRow[]
  total: number
  page: number
  size: number
}

export type ActionResult = {
  ok: boolean
  message: string
}

export type OverviewResult = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}

export type ReportReader = {
  reader: string
  scope: string
  scope_label: string
  allowed_columns: string[]
  suppressed_columns: string[]
  granted_at: string
  state: 'active' | 'revoked'
}

export type ReportShares = {
  report_id: number
  report_name: string
  status: string
  readers: ReportReader[]
  presets: { reader: string; scope: string; scope_label: string }[]
}

export type MetricSlice = {
  slice_id: string
  report_id: number
  report_name: string
  reader: string
  scope: string
  scope_label: string
  allowed_columns: string[]
  suppressed_columns: string[]
  aggregates: Record<string, number>
  sample_stats: Record<string, string | number | boolean>
  slice_digest: string
}

export type SliceDelivery = {
  api: string
  slice_id: string
  slice_digest: string
  report_id: number
  report_name: string
  report_period: string
  reader: string
  scope: string
  scope_label: string
  columns: string[]
  rows: Record<string, string | number>[]
  aggregates: Record<string, number>
  aggregation: Record<string, string>
  sample_stats: Record<string, string | number | boolean>
  delivered_at: string
}

export type DeliveryReceipt = {
  delivery_id: string
  delivered_at: string
  report_id: number
  report_name: string
  report_period: string
  reader: string
  scope: string
  scope_label: string
  allowed_columns: string[]
  suppressed_columns: string[]
  aggregates: Record<string, number>
  slice_id: string
  slice_digest: string
  delivery_digest: string
  sample_stats: Record<string, string | number | boolean>
}

export type DeliveryVerification = {
  delivery_id: string
  report_id: number
  report_name: string
  report_period: string
  reader: string
  scope: string
  scope_label: string
  allowed_columns: string[]
  suppressed_columns: string[]
  aggregates: Record<string, number>
  delivered_at: string
  delivery_digest: string
  grant: { state: string; usable: boolean; message: string; scope: string; granted_at: string }
  samples_unchanged: boolean
  advisory: string
}

export type DeliveryList = {
  report_id: number
  deliveries: {
    delivery_id: string
    delivered_at: string
    report_id: number
    reader: string
    scope: string
    scope_label: string
    aggregates: Record<string, number>
  }[]
}

export type ReportGeneration = {
  report_template: string
  period: string
  generated_at: string
  reader: string
  scope: string
  scope_label: string
  visible_columns: string[]
  sections: { metric: string; aggregate: Record<string, number> }[]
  sample_stats: Record<string, string | number | boolean>
  total_metrics: number
}
