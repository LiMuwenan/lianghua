// REST API 封装（替代旧 web/js/api.js）
const base = ''

async function request(url, options = {}) {
  const res = await fetch(base + url, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try {
      const body = await res.json()
      const d = Array.isArray(body.detail) ? body.detail.map((x) => x.msg).join('; ') : body.detail
      if (d) detail = d
    } catch (e) { /* 忽略解析失败 */ }
    throw new Error(detail)
  }
  if (res.status === 204) return null
  return res.json()
}

const api = {
  get: (url) => request(url),
  post: (url, body = {}) => request(url, { method: 'POST', body: JSON.stringify(body) }),
  patch: (url, body = {}) => request(url, { method: 'PATCH', body: JSON.stringify(body) }),
  del: (url) => request(url, { method: 'DELETE' }),
}

export default api