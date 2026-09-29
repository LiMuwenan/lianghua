<template>
  <div class="app-shell">
    <header class="topbar">
      <h1 class="brand">量化交易平台 <span class="ver">P1</span></h1>
      <nav class="nav">
        <router-link to="/">数据管理</router-link>
        <router-link to="/stocks">个股数据</router-link>
        <router-link to="/strategies">策略/回测</router-link>
        <router-link to="/tasks">任务</router-link>
        <router-link to="/cron">调度</router-link>
      </nav>
      <span class="running-hint" v-if="runningTaskId">⏳ 正在运行任务 #{{ runningTaskId }}</span>
    </header>

    <main class="container">
      <router-view />
    </main>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
import api from './api'

// 全局轮询「当前运行任务」，供顶部提示 + 右滑需求
const runningTaskId = ref(null)
let timer = null

async function pollRunning() {
  try {
    const r = await api.get('/api/tasks/running')
    runningTaskId.value = r.task_id || null
  } catch (e) {
    runningTaskId.value = null
  }
}

onMounted(() => {
  pollRunning()
  timer = setInterval(pollRunning, 3000)
})
onBeforeUnmount(() => clearInterval(timer))
</script>