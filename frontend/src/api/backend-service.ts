import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type {
  ActionResult,
  DeliveryList,
  DeliveryReceipt,
  DeliveryVerification,
  EntryRow,
  MetricSlice,
  OverviewResult,
  PageResult,
  ReportGeneration,
  ReportShares,
  SliceDelivery,
} from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

async function readJson<T>(response: Response, fallback: string): Promise<T> {
  if (!response.ok) {
    const payload = await response.json().catch(() => null)
    const detail = payload?.detail
    throw new Error(
      typeof detail === 'string' ? detail
        : detail?.message ? detail.message
          : `${fallback}（HTTP ${response.status}）`,
    )
  }
  return (await response.json()) as T
}

export async function loadReportShares(reportId: number): Promise<ReportShares> {
  return await readJson<ReportShares>(
    await request(`/api/cross/report-template/${reportId}/shares`), '报告共享读取失败')
}

export async function shareReport(
  reportId: number, reader: string, scope: string,
): Promise<ReportShares> {
  const query = new URLSearchParams({ reader, scope }).toString()
  const response = await request(`/api/cross/report-template/${reportId}/share?${query}`, {
    method: 'POST',
  })
  if (!response.ok) await readJson<unknown>(response, '共享授权失败')
  return await loadReportShares(reportId)
}

export async function revokeReportShare(reportId: number, reader: string): Promise<ReportShares> {
  const query = new URLSearchParams({ reader }).toString()
  const response = await request(`/api/cross/report-template/${reportId}/revoke?${query}`, {
    method: 'POST',
  })
  if (!response.ok) await readJson<unknown>(response, '撤销授权失败')
  return await loadReportShares(reportId)
}

export async function prepareReportSlice(
  reportId: number, reader: string,
): Promise<MetricSlice> {
  const query = new URLSearchParams({ reader }).toString()
  return await readJson<MetricSlice>(
    await request(`/api/cross/report-template/${reportId}/prepare-slice?${query}`, {
      method: 'POST',
    }),
    '受控切片生成失败',
  )
}

export async function generateReportForReader(
  reportId: number, reader: string,
): Promise<ReportGeneration> {
  const query = new URLSearchParams({ reader }).toString()
  return await readJson<ReportGeneration>(
    await request(`/api/cross/report-template/${reportId}/generate?${query}`),
    '报告生成失败',
  )
}

export async function deliverReport(
  reportId: number, reader: string, sliceId: string,
): Promise<DeliveryReceipt> {
  const query = new URLSearchParams({ reader, slice_id: sliceId }).toString()
  return await readJson<DeliveryReceipt>(
    await request(`/api/cross/report-template/${reportId}/deliver?${query}`, { method: 'POST' }),
    '报告交付失败',
  )
}

export async function deliverSlice(apiId: number, sliceId: string): Promise<SliceDelivery> {
  const query = new URLSearchParams({ slice_id: sliceId }).toString()
  return await readJson<SliceDelivery>(
    await request(`/api/cross/data-service/${apiId}/deliver-slice?${query}`, { method: 'POST' }),
    '数据服务交付失败',
  )
}

export async function loadDeliveries(reportId: number): Promise<DeliveryList> {
  return await readJson<DeliveryList>(
    await request(`/api/cross/report-template/${reportId}/deliveries`), '交付记录读取失败')
}

export async function verifyDelivery(deliveryId: string): Promise<DeliveryVerification> {
  return await readJson<DeliveryVerification>(
    await request(`/api/cross/deliveries/${deliveryId}/verify`), '交付凭证复核失败')
}
