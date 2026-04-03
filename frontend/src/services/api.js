import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 60000,
})

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

api.interceptors.response.use(
  (response) => response.data,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('token')
      localStorage.removeItem('user')
      window.location.href = '/login'
    }
    return Promise.reject(error)
  }
)

// 认证
export const authApi = {
  login: (username, password) =>
    api.post('/auth/login', { username, password }),
  getMe: () => api.get('/auth/me'),
  init: () => api.post('/auth/init'),
}

// 对话
export const chatApi = {
  getConversations: () => api.get('/chat/conversations'),
  deleteConversation: (id) => api.delete(`/chat/conversations/${id}`),
  getMessages: (id) => api.get(`/chat/conversations/${id}/messages`),
  sendMessage: (data) => api.post('/chat/send', data),
  uploadFile: (file) => {
    const fd = new FormData()
    fd.append('file', file)
    return api.post('/chat/upload', fd)
  },
  sendMessageStream: async (data, onChunk, signal) => {
    const token = localStorage.getItem('token')
    const response = await fetch('/api/chat/send/stream', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify(data),
      signal,
    })
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const parsed = JSON.parse(line.slice(6))
              onChunk(parsed)
            } catch (e) { /* ignore */ }
          }
        }
      }
    } catch (e) {
      if (e.name === 'AbortError') return
      throw e
    }
  },
}

// 知识库
export const knowledgeApi = {
  listDocuments: (params) => api.get('/knowledge/documents', { params }),
  uploadDocument: (file) => {
    const fd = new FormData()
    fd.append('file', file)
    return api.post('/knowledge/upload', fd)
  },
  importDirectory: (dirPath) => api.post('/knowledge/import-directory', null, { params: { dir_path: dirPath } }),
  deleteDocument: (id) => api.delete(`/knowledge/documents/${id}`),
  toggleDocument: (id) => api.patch(`/knowledge/documents/${id}/toggle`),
  listTemplates: (params) => api.get('/knowledge/templates', { params }),
  searchTemplates: (params) => api.get('/knowledge/templates/search', { params }),
  getCategories: () => api.get('/knowledge/templates/categories'),
  getStats: () => api.get('/knowledge/stats'),
}

// 系统设置
export const settingsApi = {
  getLLMConfig: () => api.get('/settings/llm'),
  updateLLMConfig: (data) => api.put('/settings/llm', data),
  testLLMConnection: (data) => api.post('/settings/llm/test', data),
  getNVDConfig: () => api.get('/settings/nvd'),
  updateNVDConfig: (data) => api.put('/settings/nvd', data),
  getSystemInfo: () => api.get('/settings/system-info'),
  listUsers: () => api.get('/settings/users'),
  updateUser: (id, data) => api.put(`/settings/users/${id}`, data),
}

// 导出
export const exportApi = {
  exportWord: (data) => api.post('/export/word', data, { responseType: 'blob' }),
  exportPdf: (data) => api.post('/export/pdf', data, { responseType: 'blob' }),
  exportExcel: (data) => api.post('/export/excel', data, { responseType: 'blob' }),
}

export default api
