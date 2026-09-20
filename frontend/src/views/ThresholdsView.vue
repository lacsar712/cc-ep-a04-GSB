<template>
  <div class="page">
    <div style="display: flex; justify-content: space-between; align-items: center; gap: 12px">
      <div>
        <h1 style="margin-bottom: 4px">指标阈值</h1>
        <p class="muted" style="margin-top: 0">为指标名配置上下限；保存后会对历史已记录指标重新扫描并生成告警</p>
      </div>
      <n-button @click="$router.push('/alerts')">查看告警</n-button>
    </div>

    <div v-if="isResearcher" class="card" style="margin-bottom: 16px">
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
      <p class="muted" style="margin-bottom: 0; font-size: 12px">
        至少需设置上限或下限之一；下限不能大于上限。
      </p>
    </div>
    <div v-else class="card muted" style="margin-bottom: 16px">
      审计员只读：可查看阈值配置，不可修改。
    </div>

    <div class="card">
      <n-data-table :columns="columns" :data="rows" :loading="loading" :bordered="false" />
    </div>
  </div>
</template>

<script setup>
import { computed, h, onMounted, reactive, ref } from 'vue'
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

const isResearcher = computed(() => auth.role === 'researcher')

function fmtBound(v) {
  return v === null || v === undefined ? '—' : v
}

const columns = computed(() => {
  const cols = [
    { title: '指标名', key: 'metric_name' },
    {
      title: '下限',
      key: 'lower_bound',
      render(row) {
        return fmtBound(row.lower_bound)
      },
    },
    {
      title: '上限',
      key: 'upper_bound',
      render(row) {
        return fmtBound(row.upper_bound)
      },
    },
    { title: '更新人', key: 'updated_by' },
    {
      title: '更新时间',
      key: 'updated_at',
      render(row) {
        return new Date(row.updated_at).toLocaleString()
      },
    },
  ]
  if (isResearcher.value) {
    cols.push({
      title: '操作',
      key: 'actions',
      render(row) {
        return h('div', { style: 'display:flex;gap:8px' }, [
          h(
            NButton,
            { size: 'tiny', onClick: () => startEdit(row) },
            { default: () => '编辑' },
          ),
          h(
            NPopconfirm,
            { onPositiveClick: () => remove(row.metric_name) },
            {
              trigger: () =>
                h(NButton, { size: 'tiny', type: 'error', quaternary: true }, { default: () => '删除' }),
              default: () => `删除阈值 ${row.metric_name}？已有告警保留。`,
            },
          ),
        ])
      },
    })
  }
  return cols
})

function resetForm() {
  form.metric_name = ''
  form.lower_bound = null
  form.upper_bound = null
  editing.value = false
}

function startEdit(row) {
  form.metric_name = row.metric_name
  form.lower_bound = row.lower_bound
  form.upper_bound = row.upper_bound
  editing.value = true
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
  const name = form.metric_name.trim()
  if (!name) {
    message.warning('请填写指标名')
    return
  }
  if (form.lower_bound === null && form.upper_bound === null) {
    message.warning('至少需设置上限或下限之一')
    return
  }
  busy.value = true
  try {
    await upsertThreshold(name, {
      lower_bound: form.lower_bound,
      upper_bound: form.upper_bound,
    })
    message.success('阈值已保存，历史指标已重新扫描')
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
