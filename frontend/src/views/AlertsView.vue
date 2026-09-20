<template>
  <div class="page">
    <div style="display: flex; justify-content: space-between; align-items: center; gap: 12px">
      <div>
        <h1 style="margin-bottom: 4px">越界告警</h1>
        <p class="muted" style="margin-top: 0">已记录指标超出阈值时在此列出，可点进对应 Run 详情</p>
      </div>
      <div style="display: flex; gap: 8px">
        <n-button @click="$router.push('/thresholds')">阈值配置</n-button>
        <n-button :loading="loading" @click="load">刷新</n-button>
      </div>
    </div>

    <div class="card">
      <n-data-table :columns="columns" :data="rows" :loading="loading" :bordered="false" />
      <p v-if="!loading && rows.length === 0" class="muted" style="margin-bottom: 0">
        暂无越界告警。
      </p>
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

const boundMap = {
  upper: { label: '超出上限', type: 'error' },
  lower: { label: '低于下限', type: 'warning' },
}

const columns = [
  {
    title: 'Run',
    key: 'run_name',
    render(row) {
      return h('div', [
        h('div', { style: 'font-weight:600' }, row.run_name || '（已删除）'),
        h('div', { class: 'muted', style: 'font-size:12px' }, row.project || ''),
      ])
    },
  },
  { title: '指标', key: 'metric_name' },
  {
    title: '值',
    key: 'value',
    render(row) {
      return h('span', { style: 'font-weight:600' }, String(row.value))
    },
  },
  { title: 'step', key: 'step', width: 70 },
  {
    title: '阈值',
    key: 'bound',
    render(row) {
      const m = boundMap[row.bound] || { label: row.bound, type: 'default' }
      return h('span', [
        h(NTag, { size: 'small', type: m.type }, { default: () => m.label }),
        ` ${row.bound === 'upper' ? '>' : '<'} ${row.threshold_value}`,
      ])
    },
  },
  { title: '记录人', key: 'actor' },
  {
    title: '时间',
    key: 'occurred_at',
    render(row) {
      return new Date(row.occurred_at).toLocaleString()
    },
  },
  {
    title: '操作',
    key: 'actions',
    render(row) {
      return h(
        NButton,
        { size: 'tiny', type: 'primary', onClick: () => router.push(`/runs/${row.run_id}`) },
        { default: () => 'Run 详情' },
      )
    },
  },
]

async function load() {
  loading.value = true
  try {
    rows.value = await listAlerts()
  } catch (e) {
    message.error(e.message || '加载失败')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>
