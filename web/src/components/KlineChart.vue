<template>
  <div class="card">
    <h3 style="margin-bottom: 8px">{{ title }}</h3>
    <div v-if="empty" class="empty">该股票在所选区间无数据</div>
    <div v-else ref="el" class="chart"></div>
  </div>
</template>

<script setup>
import { ref, watch, onMounted, onBeforeUnmount } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({
  title: { type: String, default: '' },
  data: { type: Object, default: () => ({}) },   // {dates, open, high, low, close, volume}
})

const el = ref(null)
let chart = null
let ro = null

const empty = ref(true)

function render() {
  const d = props.data || {}
  const dates = d.dates || []
  empty.value = !dates.length
  if (!chart) return
  if (!dates.length) return

  // K线数据：[open, close, low, high]（ECharts candlestick 排序），配合涨跌色
  const kData = dates.map((_, i) => [d.open[i], d.close[i], d.low[i], d.high[i]])
  const vols = (d.volume || []).map(Number)

  const option = {
    animation: false,
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    legend: { data: ['K线', '成交量'] },
    grid: [
      { left: 60, right: 20, top: 30, height: '56%' },
      { left: 60, right: 20, top: '72%', height: '18%' },
    ],
    xAxis: [
      { type: 'category', data: dates, boundaryGap: true, gridIndex: 0 },
      { type: 'category', data: dates, gridIndex: 1 },
    ],
    yAxis: [
      { scale: true, gridIndex: 0 },
      { scale: true, gridIndex: 1, axisLabel: { show: true } },
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1] },
      { type: 'slider', xAxisIndex: [0, 1], top: '93%', height: 18 },
    ],
    series: [
      {
        name: 'K线', type: 'candlestick', data: kData, xAxisIndex: 0, yAxisIndex: 0,
        itemStyle: {
          color: '#ef232a', color0: '#14b143',   // 阳红阴绿（国内）
          borderColor: '#ef232a', borderColor0: '#14b143',
        },
      },
      {
        name: '成交量', type: 'bar', data: vols, xAxisIndex: 1, yAxisIndex: 1,
        itemStyle: {
          color: (p) => (p.dataIndex > 0 && d.close[p.dataIndex] < d.close[p.dataIndex - 1] ? '#14b143' : '#ef232a'),
        },
      },
    ],
  }
  chart.setOption(option, true)
}

watch(() => props.data, render, { deep: true })

onMounted(() => {
  if (!el.value) return
  chart = echarts.init(el.value)
  ro = new ResizeObserver(() => chart && chart.resize())
  ro.observe(el.value)
  render()
})
onBeforeUnmount(() => {
  if (ro) ro.disconnect()
  if (chart) { chart.dispose(); chart = null }
})
</script>

<style scoped>
.chart { width: 100%; height: 380px; }
</style>