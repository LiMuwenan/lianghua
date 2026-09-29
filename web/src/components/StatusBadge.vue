<template>
  <span class="status-badge" :class="cls">{{ label }}</span>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  status: { type: String, default: '' },
})

const map = {
  success: { cls: 'status-ok', label: '成功' },
  正常: { cls: 'status-ok', label: '正常' },
  running: { cls: 'status-warn', label: '运行中' },
  queued: { cls: 'status-warn', label: '排队中' },
  failed: { cls: 'status-err', label: '失败' },
  失败: { cls: 'status-err', label: '失败' },
  缺失: { cls: 'status-err', label: '缺失' },
  partial_failed: { cls: 'status-err', label: '部分失败' },
  部分失败: { cls: 'status-err', label: '部分失败' },
  aborted: { cls: 'status-aborted', label: '已终止' },
  未生成: { cls: 'status-muted', label: '未生成' },
}

const cls = computed(() => (map[props.status] || { cls: 'status-muted', label: props.status }).cls)
const label = computed(() => (map[props.status] || { cls: 'status-muted', label: props.status }).label)
</script>