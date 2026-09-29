<template>
  <div>
    <div class="page-head">
      <h2>任务运行记录</h2>
      <button class="btn" @click="load">刷新</button>
    </div>

    <div v-if="!list.length" class="empty">暂无任务记录</div>
    <table v-else class="table">
      <thead>
        <tr>
          <th>ID</th><th>名称</th><th>类型</th><th>状态</th><th>退出码</th>
          <th>开始时间</th><th>结束时间</th><th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="t in list" :key="t.id">
          <td>{{ t.id }}</td>
          <td>{{ t.ref_name }}</td>
          <td>{{ t.kind }}</td>
          <td><StatusBadge :status="t.status" /></td>
          <td>{{ t.exit_code == null ? '—' : t.exit_code }}</td>
          <td>{{ fmt(t.started_at) }}</td>
          <td>{{ fmt(t.finished_at) }}</td>
          <td>
            <button class="btn ghost" @click="openLog(t)">日志</button>
            <button v-if="t.status === 'running'" class="btn danger" @click="terminate(t)">终止</button>
          </td>
        </tr>
      </tbody>
    </table>

    <LogModal v-model:visible="logVisible" :task-id="logTaskId" :info="logInfo" />
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import api from '../api'
import StatusBadge from '../components/StatusBadge.vue'
import LogModal from '../components/LogModal.vue'

const list = ref([])
const logVisible = ref(false)
const logTaskId = ref(null)
const logInfo = ref('')

const fmt = (s) => (s ? new Date(s).toLocaleString() : '—')

async function load() {
  list.value = await api.get('/api/tasks?limit=100')
}
async function openLog(t) {
  logVisible.value = true
  logTaskId.value = t.id
  logInfo.value = `${t.ref_name} #${t.id}`
}
async function terminate(t) {
  if (!confirm(`确认终止任务 #${t.id}？`)) return
  await api.post(`/api/tasks/${t.id}/terminate`)
  alert(`已请求终止任务 #${t.id}`)
  load()
}

onMounted(load)
</script>