<template>
  <div>
    <div class="page-head">
      <h2>定时调度</h2>
      <button class="btn primary" @click="openEdit(null)">新建定时任务</button>
    </div>

    <div v-if="!list.length" class="empty">暂无定时任务，点右上「新建定时任务」。</div>
    <table v-else class="table">
      <thead>
        <tr><th>名称</th><th>类型</th><th>cron 表达式</th><th>引用</th><th>状态</th><th>下次运行</th><th>最近运行</th><th>操作</th></tr>
      </thead>
      <tbody>
        <tr v-for="c in list" :key="c.id">
          <td>{{ c.name }}</td>
          <td>{{ kindLabel[c.kind] || c.kind }}</td>
          <td><code>{{ c.cron_expr }}</code></td>
          <td>{{ refLabel(c) }}</td>
          <td>
            <span class="status-badge" :class="c.enabled ? 'status-ok' : 'status-muted'">
              {{ c.enabled ? '启用' : '暂停' }}
            </span>
          </td>
          <td>{{ c.next_run_at ? fmt(c.next_run_at) : '—' }}</td>
          <td>{{ c.last_run_at ? fmt(c.last_run_at) : '—' }}</td>
          <td>
            <button class="btn ghost" @click="trigger(c)">执行</button>
            <button class="btn" @click="openEdit(c)">编辑</button>
            <button class="btn" @click="toggle(c)">{{ c.enabled ? '暂停' : '启用' }}</button>
            <button class="btn danger" @click="remove(c)">删除</button>
          </td>
        </tr>
      </tbody>
    </table>

    <!-- 新建/编辑弹窗 -->
    <div v-if="editVisible" class="modal-mask" @click.self="editVisible = false">
      <div class="modal-box" style="height: auto; max-height: 90vh; overflow: auto">
        <div class="modal-head">
          <h3>{{ editing ? `编辑：${editing.name}` : '新建定时任务' }}</h3>
          <button class="btn ghost" @click="editVisible = false">✕</button>
        </div>
        <div style="padding: 16px">
          <div class="field"><label>名称</label><input v-model="form.name" type="text" /></div>
          <div class="field"><label>类型</label>
            <select v-model="form.kind">
              <option value="ingest">数据更新（ingest）</option>
              <option value="strategy">策略/回测（strategy）</option>
            </select>
          </div>
          <div class="field"><label>引用对象（数据/策略）</label>
            <select v-model="form.ref_id">
              <option v-for="d in kind === 'ingest' ? datasets : strategies" :key="d.id" :value="d.id">
                {{ kind === 'ingest' ? d.name : d.name }}
              </option>
            </select>
          </div>
          <div class="field">
            <label>cron 表达式（分 时 日 月 周）</label>
            <input v-model="form.cron_expr" type="text" placeholder="如 0 17 * * *（每天 17:00）" />
          </div>
          <div class="hint" style="margin-bottom: 12px">例：<code>0 17 * * 1-5</code> 工作日 17:00；<code>0 9 * * 1</code> 每周一 09:00</div>
          <div class="field"><label>启动时启用</label>
            <select v-model="form.enabled"><option :value="true">是</option><option :value="false">否</option></select>
          </div>
          <div class="row" style="justify-content: flex-end; margin-top: 16px">
            <button class="btn" @click="editVisible = false">取消</button>
            <button class="btn primary" @click="save">保存</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, reactive, onMounted } from 'vue'
import api from '../api'

const list = ref([])
const datasets = ref([])
const strategies = ref([])
const editing = ref(null)
const editVisible = ref(false)
const kindLabel = { ingest: '数据更新', strategy: '策略/回测' }

const form = reactive({ name: '', kind: 'ingest', ref_id: null, cron_expr: '', enabled: true })
const kind = computed(() => form.kind || 'ingest')

const fmt = (s) => (s ? new Date(s).toLocaleString() : '—')

function refLabel(c) {
  const pool = c.kind === 'ingest' ? datasets.value : strategies.value
  const it = pool.find((x) => x.id === c.ref_id)
  return it ? it.name : `#${c.ref_id}`
}

async function load() {
  list.value = await api.get('/api/cron')
}
async function loadRefs() {
  datasets.value = await api.get('/api/datasets')
  strategies.value = await api.get('/api/strategies')
}

function openEdit(c) {
  editing.value = c
  form.name = c ? c.name : ''
  form.kind = c ? c.kind : 'ingest'
  form.ref_id = c ? c.ref_id : null
  form.cron_expr = c ? c.cron_expr : ''
  form.enabled = c ? c.enabled : true
  editVisible.value = true
}

async function save() {
  if (kind.value === 'ingest' && !datasets.value.length) return alert('尚无数据集可引用')
  if (kind.value === 'strategy' && !strategies.value.length) return alert('尚无策略可引用')
  const body = { name: form.name, kind: kind.value, ref_id: Number(form.ref_id), cron_expr: form.cron_expr, enabled: form.enabled }
  if (editing.value) await api.patch(`/api/cron/${editing.value.id}`, { name: form.name, cron_expr: form.cron_expr, enabled: form.enabled })
  else await api.post('/api/cron', body)
  editVisible.value = false
  await load()
}

async function toggle(c) {
  await api.patch(`/api/cron/${c.id}`, { enabled: !c.enabled })
  await load()
}
async function trigger(c) {
  await api.post(`/api/cron/${c.id}/trigger`)
  alert(`已触发「${c.name}」，请到「任务」页查看`)
  await load()
}
async function remove(c) {
  if (!confirm(`删除定时任务「${c.name}」？`)) return
  await api.del(`/api/cron/${c.id}`)
  await load()
}

onMounted(() => { loadRefs(); load() })
</script>