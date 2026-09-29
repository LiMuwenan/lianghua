<template>
  <div>
    <div class="page-head">
      <h2>脚本 / 策略 / 回测</h2>
      <button class="btn" @click="rescan">重新扫描登记</button>
    </div>

    <div v-if="!list.length" class="empty">暂无已登记脚本，点击右上「重新扫描登记」（需存在 *.py.manifest.yaml）。</div>

    <div v-for="s in list" :key="s.id" class="card">
      <div class="row" style="justify-content: space-between; margin-bottom: 6px">
        <h3>{{ s.name }}</h3>
        <span class="status-badge status-muted">{{ kindLabel[s.kind] || s.kind }}</span>
      </div>
      <div class="muted" style="margin-bottom: 6px">{{ s.script_path }}</div>
      <div class="muted" v-if="s.data_dep" style="margin-bottom: 10px">依赖数据：{{ s.data_dep }}</div>

      <div class="fields" v-if="paramKeys.length">
        <div class="field" v-for="k in paramKeys" :key="k">
          <label>{{ (schema[k] && schema[k].desc) || k }}（{{ k }}）</label>
          <input
            v-model="paramVals[s.id][k]"
            :type="inputType(schema[k] && schema[k].type)"
            :placeholder="String((schema[k] && schema[k].default) ?? '')"
            style="width: 280px; max-width: 100%"
          />
        </div>
      </div>
      <div class="muted" v-else style="margin-bottom: 10px">该脚本无参数</div>

      <button class="btn primary" @click="run(s)">运行（当前参数）</button>
    </div>

    <LogModal v-model:visible="logVisible" :task-id="logTaskId" :info="logInfo" />
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import api from '../api'
import LogModal from '../components/LogModal.vue'

const list = ref([])
const paramVals = reactive({})
const kindLabel = { script: '数据脚本', strategy: '策略', backtest: '回测' }
const logVisible = ref(false)
const logTaskId = ref(null)
const logInfo = ref('')

async function load() {
  list.value = await api.get('/api/strategies')
  list.value.forEach((s) => {
    if (!paramVals[s.id]) {
      const init = {}
      Object.keys(s.params_schema || {}).forEach((k) => {
        const meta = s.params_schema[k] || {}
        init[k] = meta.default != null ? String(meta.default) : ''
      })
      paramVals[s.id] = init
    }
  })
}
async function rescan() {
  await api.post('/api/strategies/scan')
  await load()
}

function inputType(t) {
  if (t === 'int' || t === 'float') return 'number'
  return 'text'
}
function parseParam(raw, type) {
  if (raw == null || raw === '') return ''
  if (type === 'int') return parseInt(raw)
  if (type === 'float') return parseFloat(raw)
  if (type === 'bool') return raw === 'true' || raw === 'True' || raw === '1'
  if (Array.isArray(type) || String(raw).startsWith('[')) {
    try { const a = JSON.parse(String(raw).replace(/'/g, '"')); return Array.isArray(a) ? a : raw } catch (e) { return raw }
  }
  return raw
}

async function run(s) {
  const params = {}
  Object.keys(paramVals[s.id] || {}).forEach((k) => {
    params[k] = parseParam(paramVals[s.id][k], s.params_schema[k] && s.params_schema[k].type)
  })
  const t = await api.post(`/api/strategies/${s.id}/run`, { params })
  logVisible.value = true
  logTaskId.value = t.id
  logInfo.value = `${t.ref_name} #${t.id}`
}

load()
</script>