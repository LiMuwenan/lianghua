<template>
  <div>
    <div class="page-head">
      <h2>每日大盘汇总</h2>
      <div class="row">
        <button class="btn" @click="loadLatest">刷新</button>
        <button class="btn" @click="preview">预览</button>
        <button class="btn primary" @click="send">立即发送今日汇总</button>
      </div>
    </div>

    <p class="hint" style="margin-bottom: 14px">
      收盘数据齐备后自动汇总 ①核心指数 ②市场宽度 ③量能 ④估值 ⑤行业 ⑥个股热度，渲染 HTML 邮件发送。
      预览仅渲染不发送；发送走任务队列，可在「任务」页看日志。
    </p>

    <div class="card">
      <div class="row"><span class="k">最近任务</span><span>{{ latest.task_id || '—' }}</span></div>
      <div class="row"><span class="k">状态</span><StatusBadge :status="latest.status" /></div>
      <div class="row"><span class="k">交易日</span><span>{{ latest.result_summary.trade_date || '—' }}</span></div>
      <div class="row"><span class="k">发送</span><span>{{ emailText }}</span></div>
      <div class="row"><span class="k">归档</span><span>{{ (latest.output_files && latest.output_files[0]) || '—' }}</span></div>
    </div>

    <div v-if="previewHtml" class="card">
      <h3 style="margin-bottom: 10px">预览</h3>
      <iframe :srcdoc="previewHtml" style="width: 100%; height: 640px; border: 1px solid #e3e6ea; border-radius: 6px"></iframe>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import api from '../api'
import StatusBadge from '../components/StatusBadge.vue'

const latest = ref({ result_summary: {}, output_files: [] })
const previewHtml = ref('')

const emailText = computed(() => {
  const e = latest.value.result_summary && latest.value.result_summary.email
  if (!e) return '—'
  return e.sent ? `已发送 → ${(e.recipients || []).join(', ')}` : `未发送（${e.reason || '—'}）`
})

async function loadLatest() {
  latest.value = await api.get('/api/reports/daily/latest')
}
async function preview() {
  try {
    const r = await api.get('/api/reports/daily/preview')
    previewHtml.value = r.html
  } catch (e) {
    alert(e.message || '预览失败')
  }
}
async function send() {
  await api.post('/api/reports/daily/run')
  alert('已触发「每日大盘汇总」，请到「任务」页查看进度')
  await loadLatest()
}
onMounted(loadLatest)
</script>
