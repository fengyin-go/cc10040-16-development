// 浏览器渲染层验证：真实挂载共享页，走通「两组各取一次 → 撤权 → 刷新重取」。
// 运行前先起后端（127.0.0.1:8015）：npm run test:render
import { mount, flushPromises } from '@vue/test-utils'
import { afterAll, beforeEach, describe, expect, it } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import ReportShareView from '@/views/ReportShareView.vue'

const API = 'http://127.0.0.1:8015'
const nativeFetch = globalThis.fetch

function realFetch(path: string, init?: RequestInit) {
  return nativeFetch(`${API}${path}`, init)
}

async function mountPage(reportId: number) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/report_template/:reportId/sharing', component: ReportShareView, props: true },
      { path: '/report_template', component: { template: '<div />' } },
    ],
  })
  await router.push(`/report_template/${reportId}/sharing`)
  await router.isReady()
  const wrapper = mount(ReportShareView, {
    props: { reportId: String(reportId) },
    global: { plugins: [router] },
  })
  await waitFor(() => wrapper.find('[data-testid="share-reader"]').exists())
  return wrapper
}

// 真实网络请求需要等事件循环轮转，flushPromises 只等微任务。
async function waitFor(check: () => boolean, label = 'condition', timeoutMs = 8000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    await flushPromises()
    await new Promise((resolve) => setTimeout(resolve, 25))
    if (check()) {
      await flushPromises()
      return
    }
  }
  throw new Error(`等待超时：${label}`)
}

async function restoreAll(reportId: number) {
  for (const [reader, scope] of [
    ['大区汇总组', 'region_summary'],
    ['门店运营组', 'store_detail'],
  ] as const) {
    await realFetch(
      `/api/cross/report-template/${reportId}/share?${new URLSearchParams({ reader, scope })}`,
      { method: 'POST' },
    )
  }
}

describe('报告共享页', () => {
  // 用例会撤销授权；结束后恢复默认共享，避免影响后续手工验证。
  afterAll(async () => {
    await restoreAll(1)
  })

  beforeEach(() => {
    // 页面用相对路径请求；浏览器里由 vite 代理到后端，这里补全为直连后端。
    globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString()
      return nativeFetch(url.startsWith('http') ? url : `${API}${url}`, init)
    }) as typeof fetch
  })

  it('两组各取一次、撤权后刷新，只剩汇总组能取到聚合数据', async () => {
    const reportId = 1
    await restoreAll(reportId)

    let wrapper = await mountPage(reportId)
    // eslint-disable-next-line no-console

    // 第一组：区域汇总读者取数
    await wrapper.get('[data-testid="fetch-大区汇总组"]').trigger('click')
    await waitFor(() => wrapper.find('[data-testid="result-大区汇总组"]').exists(), '汇总组取数')
    const summary = wrapper.get('[data-testid="result-大区汇总组"]')
    const summaryHeaders = summary.findAll('th').map((th) => th.text())
    expect(summaryHeaders).toContain('区域')
    expect(summaryHeaders).not.toContain('门店编号')
    expect(summaryHeaders).not.toContain('门店名称')
    // 西南只有一家门店，被整组裁去，页面上既没有该行也没有可反推的合计
    expect(summary.text()).not.toContain('西南')
    expect(summary.get('[data-testid="totals-大区汇总组"]').element.textContent).not.toContain('895900')
    const summaryTotals = summary.get('[data-testid="totals-大区汇总组"]').text()

    // 第二组：门店明细读者取数
    await wrapper.get('[data-testid="fetch-门店运营组"]').trigger('click')
    await waitFor(() => wrapper.find('[data-testid="result-门店运营组"]').exists(), '明细组取数')
    const detail = wrapper.get('[data-testid="result-门店运营组"]')
    const detailHeaders = detail.findAll('th').map((th) => th.text())
    expect(detailHeaders).toContain('门店编号')
    expect(detail.text()).toContain('成都春熙路店')
    const detailTotals = detail.get('[data-testid="totals-门店运营组"]').text()

    // 两组交付并留存凭证
    await wrapper.get('[data-testid="deliver-大区汇总组"]').trigger('click')
    await waitFor(
      () => wrapper.findAll('[data-testid^="receipt-"]').length >= 1, '汇总组交付凭证')
    await wrapper.get('[data-testid="deliver-门店运营组"]').trigger('click')
    await waitFor(
      () => wrapper.findAll('[data-testid^="receipt-"]').length >= 2, '明细组交付凭证')

    // 撤销门店运营组
    await wrapper.get('[data-testid="revoke-门店运营组"]').trigger('click')
    await waitFor(
      () => wrapper.get('[data-testid="reader-门店运营组"]').attributes('data-state') === 'revoked',
      '撤销生效',
    )

    // 浏览器刷新：重新挂载页面
    wrapper.unmount()
    wrapper = await mountPage(reportId)
    await waitFor(
      () => wrapper.get('[data-testid="reader-门店运营组"]').attributes('data-state') === 'revoked',
      '刷新后撤销状态',
    )
    await waitFor(
      () => wrapper.findAll('[data-testid^="receipt-"]').length >= 2, '刷新后凭证表',
    )
    await waitFor(
      () => wrapper.findAll('[data-testid^="receipt-"] td[data-state="revoked"]').length >= 1,
      '凭证可用性判定',
    )

    // 撤销后的门店运营组无法重新取数
    expect(wrapper.find('[data-testid="result-门店运营组"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="fetch-门店运营组"]').attributes('disabled')).toBeDefined()

    // 汇总组刷新后重新取数，结果与原交付一致
    await wrapper.get('[data-testid="fetch-大区汇总组"]').trigger('click')
    await waitFor(() => wrapper.find('[data-testid="result-大区汇总组"]').exists(), '刷新后取数')
    const refreshed = wrapper.get('[data-testid="totals-大区汇总组"]').text()
    expect(refreshed).toBe(summaryTotals)
    expect(refreshed).not.toBe(detailTotals)

    // 凭证表的可用性判定：汇总组可用，门店运营组不可用
    const receipts = wrapper.findAll('[data-testid^="receipt-"]')
    const states = receipts.map((row) => row.findAll('td')[6].attributes('data-state'))
    expect(states).toContain('active')
    expect(states).toContain('revoked')
    const revokedRow = receipts.find((row) => row.text().includes('门店运营组'))
    expect(revokedRow?.text()).toContain('不可再用')
    wrapper.unmount()
  }, 30000)
})
