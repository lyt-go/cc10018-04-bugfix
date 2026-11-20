<template>
  <section class="page" data-module="derived_metric">
    <header class="page-head">
      <div>
        <h2>派生指标管理</h2>
        <p class="page-desc">维护基于原子指标的派生计算与口径版本控制，为派生指标提供可验证的数据工程流程。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">登记派生指标</button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">导出派生指标清单</button>
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
            <button class="link" type="button" :data-testid="`dependency-${row.id}`" @click="openDependency(row)">
              查看父指标依赖
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无派生指标数据，可先登记派生指标</td>
        </tr>
      </tbody>
    </table>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记派生指标</h3>
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

    <div v-if="dependencyDetail" class="modal-backdrop" data-testid="dependency-modal">
      <div class="modal">
        <h3>父指标依赖核对 · {{ dependencyDetail.metric_code }}</h3>
        <dl class="dependency-detail">
          <dt>父指标</dt>
          <dd>{{ dependencyDetail.parent_metric || '—（缺少有效父指标）' }}</dd>
          <dt>引用路径</dt>
          <dd data-testid="dependency-path">{{ dependencyDetail.dependency_path.join(' → ') }}</dd>
          <dt>校验结果</dt>
          <dd :data-testid="`dependency-status-${dependencyDetail.valid ? 'ok' : 'blocked'}`">
            {{ dependencyDetail.valid ? '依赖可用' : '依赖不可用' }}
          </dd>
        </dl>
        <table class="data-table">
          <thead>
            <tr><th>父指标编号</th><th>类型</th><th>状态</th><th>是否有效</th></tr>
          </thead>
          <tbody>
            <tr v-for="parent in dependencyDetail.parents" :key="parent.metric_code || 'empty'">
              <td>{{ parent.metric_code || '—' }}</td>
              <td>{{ parent.kind }}</td>
              <td>{{ parent.status ?? '不存在' }}</td>
              <td>{{ parent.valid ? '有效' : '无效' }}</td>
            </tr>
            <tr v-if="!dependencyDetail.parents.length">
              <td colspan="4" class="empty-state">未声明父指标</td>
            </tr>
          </tbody>
        </table>
        <ul v-if="dependencyDetail.issues.length" class="dependency-issues" data-testid="dependency-issues">
          <li v-for="issue in dependencyDetail.issues" :key="issue">{{ issue }}</li>
        </ul>
        <div class="page-actions">
          <button class="btn" type="button" data-testid="close-dependency" @click="dependencyDetail = null">关闭</button>
        </div>
      </div>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条派生指标记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

type DependencyParent = {
  metric_code: string
  kind: string
  exists: boolean
  status: string | null
  valid: boolean
}

type DependencyDetail = {
  metric_code: string
  parent_metric: string
  parents: DependencyParent[]
  dependency_path: string[]
  cycle: boolean
  path_cycle: string[]
  issues: string[]
  valid: boolean
}

const ENDPOINT = '/api/derived_metric'
const columns = ["指标编号", "父指标编号", "计算公式", "统计周期", "业务限定", "统计粒度", "精度位数", "口径版本"]
const actions = ["提交校验", "标记生效", "标记废弃"]
const statuses = ["草稿", "待校验", "已生效", "已废弃"]
const metricNames = ["派生指标总数", "待校验指标", "已生效指标"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})
const dependencyDetail = ref<DependencyDetail | null>(null)
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
    link.download = 'derived_metric.csv'
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
      throw new Error('派生指标动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '派生指标操作失败'
  }
}

async function openDependency(row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}`)
    if (!response.ok) throw new Error('派生指标详情读取失败')
    const payload = await response.json()
    if (!payload._dependency) throw new Error('详情未返回父指标依赖信息')
    dependencyDetail.value = payload._dependency as DependencyDetail
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '派生指标详情读取失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('派生指标列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '派生指标列表读取失败'
  }
}

onMounted(reload)
</script>
