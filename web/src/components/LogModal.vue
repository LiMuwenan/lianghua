<template>
  <div v-if="visible" class="modal-mask" @click.self="$emit('close')">
    <div class="modal-box">
      <div class="modal-head">
        <h3>任务日志</h3>
        <span class="info">{{ info }}</span>
        <span class="muted" v-if="status">{{ statusText }}</span>
        <button class="btn ghost" @click="$emit('close')">✕</button>
      </div>
      <pre ref="pre" class="log-content">{{ content || '(暂无日志，任务可能尚未产出日志文件)' }}</pre>
      <div class="modal-foot">
        <label class="row">
          <input type="checkbox" v-model="autoTail" /> 自动刷新
        </label>
        <button class="btn" @click="refresh">刷新日志</button>
        <span class="hint">自动刷新每秒一次；任务结束自动停止轮询</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, onBeforeUnmount } from 'vue'
import api from '../api'

const props = defineProps({
  visible: { type: Boolean, default: false },
  taskId: { type: [Number, String], default: null },
  info: { type: String, default: '' },
})
const emit = defineEmits(['close'])

const content = ref('')
let status = ref('')
const statusText = ref('')
const autoTail = ref(true)
const pre = ref(null)
let timer = null

const statusLabel = (s) =>
  ({ success: '（已结束）', failed: '（已结束）', aborted: '（已终止）', partial_failed: '（已结束）' }[s] || '')

async function refresh() {
  if (props.taskId == null) return
  try {
    const r = await api.get(`/api/tasks/${props.taskId}/log`)
    content.value = r.content
    status.value = r.status
    statusText.value = statusLabel(r.status)
    if (pre.value && autoTail.value) pre.value.scrollTop = pre.value.scrollHeight
    if (['success', 'failed', 'partial_failed', 'aborted'].includes(r.status)) {
      stop()
    }
  } catch (e) { /* 任务日志未就绪等，忽略 */ }
}

function start() {
  stop()
  refresh()
  timer = setInterval(refresh, 1000)
}
function stop() {
  if (timer) { clearInterval(timer); timer = null }
}

watch(() => props.visible, (v) => {
  if (v) { content.value = ''; status.value = ''; statusText.value = ''; start() }
  else stop()
})
onBeforeUnmount(stop)
</script>