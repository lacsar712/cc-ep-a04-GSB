<template>
  <div class="page">
    <div style="display: flex; justify-content: space-between; align-items: center; gap: 12px">
      <div>
        <h1 style="margin-bottom: 4px">指标告警</h1>
        <p class="muted" style="margin-top: 0">已记录指标越界时在此列出，可跳转对应 Run 详情</p>
      </div>
      <n-button @click="$router.push('/thresholds')">管理阈值</n-button>
    </div>

    <div class="card" style="margin-bottom: 16px">
      <div class="grid-2">
        <n-form-item label="指标名" :show-feedback="false">
          <n-input v-model:value="metricName" clearable placeholder="例如 loss" />
        </n-form-item>
        <n-form-item label="Run ID" :show-feedback="false">
          <n-input v-model:value="runId" clearable placeholder="按 Run 过滤" class="mono" />
        </n-form-item>
      </div>
      <n-button style="margin-top: 8px" @click="load">筛选</n-button>
    </div>

    <div class="card">
      <n-data-table :columns="columns" :data="rows" :loading="loading" :bordered="false" />
    </div>
  </div>
</template>

<script setup>
import { h, onMounted, ref } from 'vue'
import { NButton, NTag, useMessage } from 'naive-ui'
import { useRouter } from 'vue-router'
import { listAlerts } from '../api/client'

const router = useRouter()
const message = useMessage()
const rows = ref([])
const loading = ref(false)
const metricName = ref('')
const runId = ref('')

const directionMap = {
  above: { type: 'error', label: '高于上限' },
  below: { type: 'warning', label: '低于下限' },
}

function thresholdText(row) {
  const parts = []
  if (row.lower_bound !== null && row.lower_bound !== undefined) parts.push(`下限 ${row.lower_bound}`)
  if (row.upper_bound !== null && row.upper_bound !== undefined) parts.push(`上限 ${row.upper_bound}`)
  return parts.join(' · ') || '—'
}

const columns = [
  {
    title: '时间',
    key: 'created_at',
    render(row) {
      return new Date(row.created_at).toLocaleString()
    },
  },
  {
    title: 'Run',
    key: 'run_name',
    render(row) {
      return `${row.project} / ${row.run_name}`
    },
  },
  { title: '指标', key: 'metric_name' },
  {
    title: '值',
    key: 'value',
    render(row) {
      return `${row.value} (step ${row.step})`
    },
  },
  {
    title: '阈值',
    key: 'threshold',
    render(row) {
      return thresholdText(row)
    },
  },
  {
    title: '越界',
    key: 'direction',
    render(row) {
      const m = directionMap[row.direction] || { type: 'default', label: row.direction }
      return h(NTag, { type: m.type, size: 'small' }, { default: () => m.label })
    },
  },
  {
    title: '操作',
    key: 'actions',
    render(row) {
      return h(
        NButton,
        { size: 'tiny', onClick: () => router.push(`/runs/${row.run_id}`) },
        { default: () => 'Run 详情' },
      )
    },
  },
]

async function load() {
  loading.value = true
  try {
    const params = {}
    if (metricName.value.trim()) params.metric_name = metricName.value.trim()
    if (runId.value.trim()) params.run_id = runId.value.trim()
    rows.value = await listAlerts(params)
  } catch (e) {
    message.error(e.message || '加载失败')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>
