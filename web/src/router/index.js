import { createRouter, createWebHashHistory } from 'vue-router'

const routes = [
  { path: '/', name: 'datasets', component: () => import('../views/DatasetsView.vue') },
  { path: '/stocks', name: 'stocks', component: () => import('../views/StocksView.vue') },
  { path: '/strategies', name: 'strategies', component: () => import('../views/StrategiesView.vue') },
  { path: '/tasks', name: 'tasks', component: () => import('../views/TasksView.vue') },
  { path: '/cron', name: 'cron', component: () => import('../views/CronView.vue') },
]

// 使用 hash 路由：后端静态托管无需额外 fallback，刷新与直接访问都稳定。
export default createRouter({
  history: createWebHashHistory(),
  routes,
})