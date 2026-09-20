<template>
  <div class="page">
    <div style="display: flex; justify-content: space-between; align-items: center; gap: 12px">
      <div>
        <h1 style="margin-bottom: 4px">指标阈值</h1>
        <p class="muted" style="margin-top: 0">
          按指标名配置上下限；保存后会对全部已记录指标重新评估并生成告警
        </p>
      </div>
      <n-button @click="$router.push('/alerts')">查看告警</n-button>
    </div>

    <div v-if="auth.role === 'researcher'" class="card" style="margin-bottom: 16px">
      <h3 style="margin-top: 0">{{ editing ? `编辑阈值：${form.metric_name}` : '新增 / 更新阈值' }}</h3>
      <div class="grid-2">
        <n-form-item label="指标名" :show-feedback="false">
          <n-input
            v-model:value="form.metric_name"
            :disabled="editing"
            placeholder="例如 loss / tm_score"
          />
        </n-form-item>
        <div style="display: flex; gap: 12px">
          <n-form-item label="下限（可空）" :show-feedback="false" style="flex: 1">
            <n-input-number v-model:value="form.lower_bound" clearable style="width: 100%" />
          </n-form-item>
          <n-form-item label="上限（可空）" :show-feedback="false" style="flex: 1">
            <n-input-number v-model:value="form.upper_bound" clearable style="width: 100%" />
          </n-form-item>
        </div>
      </div>
      <div style="display: flex; gap: 8px; margin-top: 8px">
        <n-button type="primary" :loading="busy" @click="save">保存阈值</n-button>
        <n-button v-if="editing" quaternary @click="resetForm">取消编辑</n-button>
      </div>
    </div>
    <div v-else class="card muted" style="margin-bottom: 16px">
      审计员只读：可查看阈值与告警，不可修改。
    </div>

    <div class="card">
      <n-data-table :columns="columns" :data="rows" :loading="loading" :bordered="false" />
    </div>
  </div>
</template>

<script setup>
import { h, onMounted, reactive, ref } from 'vue'
import { NButton, NPopconfirm, useMessage } from 'naive-ui'
import { deleteThreshold, listThresholds, upsertThreshold } from '../api/client'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const message = useMessage()
const rows = ref([])
const loading = ref(false)
const busy = ref(false)
const editing = ref(false)
const form = reactive({ metric_name: '', lower_bound: null, upper_bound: null })

function formatBound(v) {
  return v === null || v === undefined ? '—' : v
}

function formatTime(v) {
  return v ? new Date(v).toLocaleString() : '—'
}

function fillForm(row) {
  editing.value = true
  form.metric_name = row.metric_name
  form.lower_bound = row.lower_bound
  form.upper_bound = row.upper_bound
}

function resetForm() {
  editing.value = false
  form.metric_name = ''
  form.lower_bound = null
  form.upper_bound = null
}

const columns = [
  { title: '指标名', key: 'metric_name' },
  {
    title: '下限',
    key: 'lower_bound',
    render(row) {
      return formatBound(row.lower_bound)
    },
  },
  {
    title: '上限',
    key: 'upper_bound',
    render(row) {
      return formatBound(row.upper_bound)
    },
  },
  { title: '更新人', key: 'updated_by' },
  {
    title: '更新时间',
    key: 'updated_at',
    render(row) {
      return formatTime(row.updated_at)
    },
  },
]

if (auth.role === 'researcher') {
  columns.push({
    title: '操作',
    key: 'actions',
    render(row) {
      return h('div', { style: 'display:flex;gap:8px;align-items:center' }, [
        h(NButton, { size: 'tiny', onClick: () => fillForm(row) }, { default: () => '编辑' }),
        h(
          NPopconfirm,
          { onPositiveClick: () => remove(row.metric_name) },
          {
            trigger: () => h(NButton, { size: 'tiny', type: 'error', quaternary: true }, { default: () => '删除' }),
            default: () => `删除阈值 ${row.metric_name}？其关联告警将一并清除。`,
          },
        ),
      ])
    },
  })
}

async function load() {
  loading.value = true
  try {
    rows.value = await listThresholds()
  } catch (e) {
    message.error(e.message || '加载失败')
  } finally {
    loading.value = false
  }
}

async function save() {
  if (!form.metric_name.trim()) {
    message.warning('请填写指标名')
    return
  }
  if (form.lower_bound === null && form.upper_bound === null) {
    message.warning('上下限至少填写一项')
    return
  }
  busy.value = true
  try {
    await upsertThreshold(form.metric_name.trim(), {
      lower_bound: form.lower_bound,
      upper_bound: form.upper_bound,
    })
    message.success('阈值已保存，历史指标已重新评估')
    resetForm()
    await load()
  } catch (e) {
    message.error(e.message || '保存失败')
  } finally {
    busy.value = false
  }
}

async function remove(metricName) {
  try {
    await deleteThreshold(metricName)
    message.success('阈值已删除')
    if (editing.value && form.metric_name === metricName) resetForm()
    await load()
  } catch (e) {
    message.error(e.message || '删除失败')
  }
}

onMounted(load)
</script>
