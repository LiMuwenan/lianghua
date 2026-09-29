<template>
  <div>
    <div class="page-head">
      <h2>数据管理</h2>
      <div class="row">
        <button class="btn" @click="load">刷新扫描</button>
        <button class="btn primary" @click="initStocks" :disabled="initLoading">
          {{ initLoading ? '初始化中…' : '初始化股票列表' }}
        </button>
      </div>
    </div>

    <p class="hint" style="margin-bottom: 14px">
      数据来源：平台内建摄取（baostock）。起始日期留空=按每股已入库日期断点续传（空库默认 2005-01-01），已获取的日期自动跳过。
    </p>

    <div v-if="!datasets.length" class="empty">暂无数据集，请检查 config/config.yaml 中的 datasets 声明。</div>

    <div v-for="d in datasets" :key="d.id" class="card">
      <h3 style="margin-bottom: 10px">{{ d.name }}</h3>
      <div class="row" style="margin-bottom: 6px"><span class="k">类型</span><span>{{ d.type || '—' }}</span></div>
      <div class="row" style="margin-bottom: 6px"><span class="k">股票数</span><span>{{ d.file_count }} 只</span></div>
      <div class="row" style="margin-bottom: 6px"><span class="k">最新数据日期</span><span>{{ d.latest_data_date || '—' }}</span></div>
      <div class="row" style="margin-bottom: 6px"><span class="k">滞后天数</span><span>{{ d.lag_days == null ? '—' : d.lag_days + ' 天' }}</span></div>
      <div class="row" style="margin-bottom: 14px">
        <span class="k">状态</span><StatusBadge :status="d.status" />
      </div>

      <div class="row">
        <span class="k">起始日期</span>
        <input type="date" v-model="startDates[d.id]" :title="'留空=按每股断点续传（空库默认 2005-01-01）'" />
        <button class="btn primary" @click="run(d)">获取数据</button>
        <span class="hint">留空自动续传，已获取日期自动跳过</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import api from '../api'
import StatusBadge from '../components/StatusBadge.vue'

const datasets = ref([])
const startDates = ref({})
const initLoading = ref(false)

async function load() {
  datasets.value = await api.get('/api/datasets')
}
async function run(d) {
  const q = startDates.value[d.id] ? `?start_date=${encodeURIComponent(startDates.value[d.id])}` : ''
  await api.post(`/api/datasets/${d.id}/run${q}`)
  alert(`已触发「${d.name}」获取数据，请在「任务」页查看进度`)
}
async function initStocks() {
  initLoading.value = true
  try {
    const r = await api.post('/api/stocks/init')
    alert(r.message || '股票列表初始化完成')
  } finally {
    initLoading.value = false
  }
}

onMounted(load)
</script>