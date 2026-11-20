<template>
  <section class="page" data-module="data_aggregate">
    <header class="page-head">
      <div>
        <h2>数据聚合管理</h2>
        <p class="page-desc">维护多维数据聚合的维度定义、函数组合与增量更新配置，为数据聚合提供可验证的数据工程流程。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">登记聚合配置</button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">导出数据聚合清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-testid="`row-${row.id}`" :data-status="String(row.status)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              :data-testid="`action-${row.id}-${action}`"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <button class="link" type="button" :data-testid="`plan-${row.id}`" @click="loadPlan(row)">
              生成计算计划
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无数据聚合数据，可先登记聚合配置</td>
        </tr>
      </tbody>
    </table>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记聚合配置</h3>
        <label v-for="field in columns" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>

    <section v-if="plan" class="plan-panel" data-testid="compute-plan">
      <div class="plan-head">
        <h3>计算计划 · {{ plan.aggregate_code ?? plan.aggregate_id }}</h3>
        <span class="plan-badge" :class="`mode-${plan.scope.mode}`" data-testid="plan-scope">
          {{ plan.scope.label }}
        </span>
        <span class="plan-badge" data-testid="plan-delivery">{{ plan.scope.delivery }}</span>
        <button class="btn ghost" type="button" data-testid="close-plan" @click="plan = null">收起</button>
      </div>
      <p class="plan-note" data-testid="plan-note">{{ plan.scope.note }}</p>
      <p v-if="plan.scope.conflict" class="dependency-issues" data-testid="plan-conflict">
        {{ plan.scope.conflict }}
      </p>
      <table v-if="plan.scope.delivery_options.length > 1" class="sub-table" data-testid="plan-delivery-options">
        <thead><tr><th>交付口径</th><th>范围</th><th>可按完整报表交付</th><th>说明</th></tr></thead>
        <tbody>
          <tr v-for="option in plan.scope.delivery_options" :key="option.mode">
            <td>{{ option.label }}</td>
            <td>{{ option.metrics.length ? option.metrics.join('、') : '—' }}</td>
            <td>{{ option.report_ready ? '是' : '否' }}</td>
            <td>{{ option.note }}</td>
          </tr>
        </tbody>
      </table>

      <table class="sub-table" data-testid="plan-steps">
        <thead>
          <tr><th>步骤</th><th>类型</th><th>对象</th><th>说明</th><th>依赖路径</th></tr>
        </thead>
        <tbody>
          <tr v-for="step in plan.steps" :key="step.step" :data-testid="`plan-step-${step.step}`">
            <td>{{ step.step }}</td>
            <td>{{ step.type }}</td>
            <td>{{ step.metric_code ?? step.domain_code ?? step.output_table }}</td>
            <td>{{ step.description }}</td>
            <td>{{ step.dependency_path?.join(' → ') ?? '—' }}</td>
          </tr>
        </tbody>
      </table>

      <h4>派生指标依赖校验报告</h4>
      <table class="sub-table" data-testid="dependency-report">
        <thead>
          <tr><th>指标</th><th>父指标</th><th>引用路径</th><th>可执行</th><th>问题</th></tr>
        </thead>
        <tbody>
          <tr
            v-for="item in plan.dependency_report.metrics"
            :key="item.metric_code"
            :class="{ 'plan-blocked-row': !item.available }"
            :data-testid="`dependency-row-${item.metric_code}`"
            :data-available="item.available ? 'true' : 'false'"
          >
            <td>{{ item.metric_code }}</td>
            <td>{{ item.parent || '—' }}</td>
            <td>{{ item.dependency_path.join(' → ') }}</td>
            <td>{{ item.available ? '是' : '否' }}</td>
            <td>{{ item.issues.length ? item.issues.join('；') : '—' }}</td>
          </tr>
        </tbody>
      </table>
      <p v-if="plan.dependency_report.cycles.length" class="dependency-issues" data-testid="plan-cycles">
        <span v-for="cycle in plan.dependency_report.cycles" :key="cycle.path.join('-')">
          循环依赖：{{ cycle.path.join(' → ') }}
        </span>
      </p>
    </section>

    <footer class="page-foot">
      <span>共 {{ total }} 条数据聚合记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

type PlanStep = {
  step: number
  type: string
  domain_code?: string
  metric_code?: string
  output_table?: string
  description: string
  dependency_path?: string[]
}

type DependencyMetric = {
  metric_code: string
  parent: string
  dependency_path: string[]
  available: boolean
  issues: string[]
}

type ComputePlan = {
  aggregate_id: number
  aggregate_code?: string
  executable: boolean
  complete: boolean
  scope: {
    mode: 'complete' | 'partial' | 'blocked'
    label: string
    delivery: string
    note: string
    conflict?: string
    delivery_options: {
      mode: string
      label: string
      metrics: string[]
      report_ready: boolean
      note: string
    }[]
  }
  steps: PlanStep[]
  dependency_report: {
    metrics: DependencyMetric[]
    cycles: { path: string[] }[]
  }
}

const ENDPOINT = '/api/data_aggregate'
const columns = ["配置编号", "数据来源", "聚合维度", "聚合函数", "过滤条件", "输出表名", "增量键", "更新策略"]
const actions = ["提交校验", "标记生效", "停用配置"]
const statuses = ["草稿", "待校验", "已生效", "已停用"]
const metricNames = ["聚合配置总数", "待校验配置", "已生效配置"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})
const plan = ref<ComputePlan | null>(null)
const stats = computed(() => [
  { label: metricNames[0], value: total.value },
  { label: metricNames[1], value: rows.value.filter((row) => row.status === statuses[1]).length },
  { label: metricNames[2], value: rows.value.filter((row) => row.status === statuses[statuses.length - 1]).length },
])

function resetFilters() {
  filters.value = {}
  void reload()
}

async function exportRows() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value).toString()
  try {
    const response = await request(`${ENDPOINT}/export?${query}`)
    if (!response.ok) throw new Error('导出失败，请稍后重试')
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = 'data_aggregate.csv'
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function openCreate() {
  createValues.value = Object.fromEntries(columns.map((field) => [field, '']))
  errorMessage.value = ''
  showCreate.value = true
}

async function submitCreate() {
  creating.value = true
  errorMessage.value = ''
  try {
    const response = await request(ENDPOINT, {
      method: 'POST', body: JSON.stringify(createValues.value),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '登记失败')
    showCreate.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  } finally {
    creating.value = false
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('数据聚合动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据聚合操作失败'
  }
}

async function loadPlan(row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/compute-plan`)
    if (!response.ok) throw new Error('计算计划生成失败')
    const payload = (await response.json()) as ComputePlan
    plan.value = { ...payload, aggregate_code: String(row['配置编号'] ?? row.id) }
  } catch (error) {
    plan.value = null
    errorMessage.value = error instanceof Error ? error.message : '计算计划生成失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('聚合配置列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据聚合列表读取失败'
  }
}

onMounted(reload)
</script>
