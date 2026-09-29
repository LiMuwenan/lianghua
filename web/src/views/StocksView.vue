<template>
  <div>
    <div class="page-head">
      <h2>个股数据</h2>
      <button class="btn" @click="loadStocks">刷新股票列表</button>
    </div>

    <div class="card">
      <div class="field"><label>搜索股票（代码 / 名称）</label>
        <input type="text" v-model="keyword" @input="searchStocks" placeholder="如 sh600000 / 浦发银行" style="width: 320px" />
      </div>
      <div class="field"><label>选择股票</label>
        <select v-model="selectedCode" style="width: 320px" @change="loadKline">
          <option value="">— 请选择（名称可搜）—</option>
          <option v-for="s in filtered" :key="s.code" :value="s.code">{{ s.name }}（{{ s.code }}）</option>
        </select>
        <span class="hint" v-if="!stocks.length">股票列表为空：先点击右上「刷新股票列表」或到数据管理页「初始化股票列表」。</span>
      </div>
      <div class="row" style="gap: 12px; flex-wrap: wrap; margin-top: 10px">
        <div class="row"><span class="k">起始</span><input type="date" v-model="from" /></div>
        <div class="row"><span class="k">结束</span><input type="date" v-model="to" /></div>
        <button class="btn primary" @click="loadKline">查询</button>
      </div>
    </div>

    <div v-if="noData" class="empty">该股票在所选区间无数据（可能未摄取或不在股票列表）</div>
    <div v-else-if="loading" class="empty">加载中…</div>
    <div v-else-if="klines.raw.dates && klines.raw.dates.length" class="grid grid-3">
      <KlineChart title="不复权" :data="klines.raw" />
      <KlineChart title="前复权" :data="klines.qfq" />
      <KlineChart title="后复权" :data="klines.hfq" />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import api from '../api'
import KlineChart from '../components/KlineChart.vue'

const stocks = ref([])
const keyword = ref('')
const selectedCode = ref('')
const from = ref('')
const to = ref('')
const klines = ref({})
const loading = ref(false)
const noData = ref(false)

const filtered = computed(() => {
  const kw = keyword.value.trim()
  if (!kw) return stocks.value
  return stocks.value.filter((s) => s.code.includes(kw) || s.name.includes(kw))
})

async function loadStocks() {
  stocks.value = await api.get('/api/stocks?limit=2000')
}
function searchStocks() { /* 由 computed 处理 */ }

async function loadKline() {
  if (!selectedCode.value) return
  loading.value = true
  noData.value = false
  try {
    const q = new URLSearchParams()
    if (from.value) q.set('from', from.value)
    if (to.value) q.set('to', to.value)
    const r = await api.get(`/api/stocks/${selectedCode.value}/kline${q ? '?' + q.toString() : ''}`)
    const ok = !!(r.raw && r.raw.dates && r.raw.dates.length)
    klines.value = r
    noData.value = !ok
  } finally {
    loading.value = false
  }
}

onMounted(loadStocks)
</script>