<template>
  <section class="page" data-testid="report-sharing">
    <header class="page-head">
      <div>
        <h2>报告共享 · {{ reportId }}</h2>
        <p class="page-desc">
          同一份报告按接收团队分发：区域汇总读者只看聚合值，门店明细读者才可取得单店记录。
          授权撤销后，已交付结果与重新取数都会失效。
        </p>
      </div>
      <div class="page-actions">
        <RouterLink class="btn ghost" to="/report_template">返回报告模板</RouterLink>
        <button class="btn" data-testid="reload" type="button" @click="reloadAll">刷新</button>
      </div>
    </header>

    <p v-if="errorMessage" class="error-text" data-testid="page-error">{{ errorMessage }}</p>

    <div v-if="shares" class="report-brief">
      <strong>{{ shares.report_name }}</strong>
      <span class="muted">状态：{{ shares.status }}</span>
      <span class="muted">获权接收团队：{{ shares.readers.length }} 组</span>
    </div>

    <form class="share-bar" @submit.prevent="grantAccess">
      <label>
        <span>接收团队</span>
        <input v-model="newReader" data-testid="share-reader" placeholder="例如 大区汇总组" />
      </label>
      <label>
        <span>获准范围</span>
        <select v-model="newScope" data-testid="share-scope">
          <option v-for="preset in scopeOptions" :key="preset.value" :value="preset.value">
            {{ preset.label }}
          </option>
        </select>
      </label>
      <button class="btn primary" data-testid="share-submit" type="submit">新增共享</button>
      <button
        v-for="preset in shares?.presets ?? []"
        :key="`preset-${preset.reader}`"
        class="link"
        type="button"
        :data-testid="`preset-${preset.scope}`"
        @click="applyPreset(preset)"
      >
        用预置：{{ preset.reader }}（{{ preset.scope_label }}）
      </button>
    </form>

    <div class="reader-grid">
      <article
        v-for="reader in shares?.readers ?? []"
        :key="reader.reader"
        class="reader-card"
        :data-testid="`reader-${reader.reader}`"
        :data-state="reader.state"
      >
        <header class="reader-head">
          <strong>{{ reader.reader }}</strong>
          <span class="badge" :data-scope="reader.scope">
            {{ reader.scope_label }}{{ reader.state === 'revoked' ? ' · 已撤销' : '' }}
          </span>
        </header>
        <p class="muted">获准列：{{ reader.allowed_columns.join('、') }}</p>
        <p v-if="reader.suppressed_columns.length" class="muted">
          不可见列：{{ reader.suppressed_columns.join('、') }}
        </p>
        <div class="page-actions">
          <button
            class="btn"
            type="button"
            :data-testid="`fetch-${reader.reader}`"
            :disabled="busy === reader.reader || reader.state === 'revoked'"
            @click="fetchAs(reader)"
          >
            {{ busy === reader.reader ? '取数中…' : '按身份取数' }}
          </button>
          <button
            class="btn"
            type="button"
            :data-testid="`deliver-${reader.reader}`"
            :disabled="busy === reader.reader || reader.state === 'revoked'"
            @click="deliverAs(reader)"
          >
            交付并留存凭证
          </button>
          <button
            v-if="reader.state !== 'revoked'"
            class="link"
            type="button"
            :data-testid="`revoke-${reader.reader}`"
            @click="revokeAccess(reader.reader)"
          >
            撤销授权
          </button>
          <button
            v-else
            class="link"
            type="button"
            :data-testid="`restore-${reader.reader}`"
            @click="restoreAccess(reader)"
          >
            恢复授权
          </button>
        </div>

        <p v-if="reader.state === 'revoked'" class="muted" :data-testid="`revoked-note-${reader.reader}`">
          授权已撤销，重新取数会返回 403；此前交付的结果在凭证表中标记为不可用。
        </p>
        <div v-if="results[reader.reader]" class="reader-result" :data-testid="`result-${reader.reader}`">
          <p class="muted">
            切片 {{ results[reader.reader].slice_id }} · 粒度 {{ results[reader.reader].scope_label }}
            · {{ results[reader.reader].delivered_at }}
          </p>
          <table class="data-table compact">
            <thead>
              <tr>
                <th v-for="column in results[reader.reader].columns" :key="column">{{ column }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, index) in results[reader.reader].rows" :key="index">
                <td v-for="column in results[reader.reader].columns" :key="column">
                  {{ row[column] ?? '—' }}
                </td>
              </tr>
            </tbody>
          </table>
          <p class="totals" :data-testid="`totals-${reader.reader}`">
            合计：
            <span v-for="(value, metric) in results[reader.reader].aggregates" :key="metric">
              {{ metric }} {{ value }}
            </span>
          </p>
          <p class="muted">样本统计：{{ describeStats(results[reader.reader].sample_stats) }}</p>
        </div>
        <p
          v-if="errors[reader.reader]"
          class="error-text"
          :data-testid="`error-${reader.reader}`"
        >
          {{ errors[reader.reader] }}
        </p>
      </article>
      <p v-if="shares && !shares.readers.length" class="empty-state">
        该报告当前没有共享给任何接收团队
      </p>
    </div>

    <h3 class="section-title">交付凭证</h3>
    <table class="data-table">
      <thead>
        <tr>
          <th>交付编号</th>
          <th>接收团队</th>
          <th>获准范围</th>
          <th>交付时间</th>
          <th>报告</th>
          <th>指标合计</th>
          <th>可用性</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="receipt in receipts"
          :key="receipt.delivery_id"
          :data-testid="`receipt-${receipt.delivery_id}`"
        >
          <td>{{ receipt.delivery_id }}</td>
          <td>{{ receipt.reader }}</td>
          <td>{{ receipt.scope_label }}</td>
          <td>{{ receipt.delivered_at }}</td>
          <td>{{ receipt.report_id }} · {{ receipt.report_name }}</td>
          <td>
            <span v-for="(value, metric) in receipt.aggregates" :key="metric">
              {{ metric }} {{ value }}
            </span>
          </td>
          <td :data-state="verifications[receipt.delivery_id]?.grant.state">
            <span v-if="verifications[receipt.delivery_id]">
              {{ verifications[receipt.delivery_id].advisory }}
            </span>
            <span v-else class="muted">复核中…</span>
          </td>
        </tr>
        <tr v-if="!receipts.length">
          <td colspan="7" class="empty-state">还没有交付记录</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import {
  deliverReport,
  deliverSlice,
  loadDeliveries,
  loadReportShares,
  prepareReportSlice,
  revokeReportShare,
  shareReport,
  verifyDelivery,
} from '@/api/backend-service'
import type {
  DeliveryReceipt,
  DeliveryVerification,
  ReportReader,
  ReportShares,
  SliceDelivery,
} from '@/data/types'

const props = defineProps<{ reportId?: string }>()
const route = useRoute()
const reportId = Number(props.reportId ?? route.params.reportId)
// 数据服务里已发布的切片交付接口；默认指向「经营指标切片服务」。
const API_ID = 2

const shares = ref<ReportShares | null>(null)
const receipts = ref<DeliveryReceipt[]>([])
const verifications = ref<Record<string, DeliveryVerification>>({})
const results = ref<Record<string, SliceDelivery>>({})
const errors = ref<Record<string, string>>({})
const busy = ref('')
const errorMessage = ref('')
const newReader = ref('')
const newScope = ref('region_summary')
const scopeOptions = [
  { value: 'region_summary', label: '区域汇总（只看聚合值）' },
  { value: 'store_detail', label: '门店明细（可看单店记录）' },
]

function describeStats(stats: Record<string, string | number | boolean>) {
  return Object.entries(stats)
    .map(([key, value]) => `${key}=${value}`)
    .join(' · ')
}

function messageOf(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback
}

async function reloadAll() {
  errorMessage.value = ''
  try {
    shares.value = await loadReportShares(reportId)
    const payload = await loadDeliveries(reportId)
    receipts.value = payload.deliveries as unknown as DeliveryReceipt[]
    await verifyReceipts()
  } catch (error) {
    errorMessage.value = messageOf(error, '报告共享数据读取失败')
  }
}

async function verifyReceipts() {
  const entries = await Promise.all(
    receipts.value.map(async (receipt) => {
      try {
        return [receipt.delivery_id, await verifyDelivery(receipt.delivery_id)] as const
      } catch {
        return null
      }
    }),
  )
  verifications.value = Object.fromEntries(entries.filter((item) => item !== null))
}

async function grantAccess() {
  errorMessage.value = ''
  if (!newReader.value.trim()) {
    errorMessage.value = '请填写接收团队名称'
    return
  }
  try {
    shares.value = await shareReport(reportId, newReader.value.trim(), newScope.value)
    newReader.value = ''
  } catch (error) {
    errorMessage.value = messageOf(error, '共享授权失败')
  }
}

function applyPreset(preset: ReportShares['presets'][number]) {
  newReader.value = preset.reader
  newScope.value = preset.scope
}

async function restoreAccess(reader: ReportReader) {
  errorMessage.value = ''
  try {
    shares.value = await shareReport(reportId, reader.reader, reader.scope)
    await reloadAll()
  } catch (error) {
    errorMessage.value = messageOf(error, '恢复授权失败')
  }
}

async function revokeAccess(reader: string) {
  errorMessage.value = ''
  try {
    shares.value = await revokeReportShare(reportId, reader)
    delete results.value[reader]
    await reloadAll()
  } catch (error) {
    errorMessage.value = messageOf(error, '撤销授权失败')
  }
}

async function fetchAs(reader: ReportReader) {
  busy.value = reader.reader
  delete errors.value[reader.reader]
  try {
    const metricSlice = await prepareReportSlice(reportId, reader.reader)
    results.value[reader.reader] = await deliverSlice(API_ID, metricSlice.slice_id)
  } catch (error) {
    delete results.value[reader.reader]
    errors.value[reader.reader] = messageOf(error, '取数失败')
  } finally {
    busy.value = ''
  }
}

async function deliverAs(reader: ReportReader) {
  busy.value = reader.reader
  delete errors.value[reader.reader]
  try {
    const metricSlice = await prepareReportSlice(reportId, reader.reader)
    await deliverReport(reportId, reader.reader, metricSlice.slice_id)
    await reloadAll()
  } catch (error) {
    errors.value[reader.reader] = messageOf(error, '交付失败')
  } finally {
    busy.value = ''
  }
}

onMounted(reloadAll)
</script>

<style scoped>
.report-brief {
  display: flex;
  gap: 16px;
  align-items: baseline;
  padding: 10px 12px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  margin-bottom: 12px;
}
.muted { color: var(--muted); font-size: 12px; }
.share-bar { display: flex; flex-wrap: wrap; gap: 10px; align-items: flex-end; margin-bottom: 12px; }
.share-bar label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }
.share-bar input, .share-bar select { padding: 6px 10px; border: 1px solid var(--border); border-radius: 6px; }
.reader-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(380px, 1fr)); gap: 12px; }
.reader-card { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 12px; }
.reader-head { display: flex; justify-content: space-between; align-items: center; }
.badge { font-size: 12px; padding: 2px 8px; border-radius: 999px; background: #eef2ff; color: #1f6feb; }
.badge[data-scope='store_detail'] { background: #fff1e6; color: #b45309; }
.reader-result { margin-top: 10px; }
.compact th, .compact td { padding: 4px 8px; font-size: 12px; }
.totals { display: flex; gap: 12px; font-size: 13px; font-weight: 600; margin: 8px 0 4px; }
.section-title { font-size: 15px; margin: 18px 0 8px; }
</style>
