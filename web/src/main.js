// 应用入口：挂载 Vue 应用并接入路由。
import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import './styles.css'

createApp(App).use(router).mount('#app')