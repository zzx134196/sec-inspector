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
  sendMessageStream: function (data, onChunk, signal) {
    var token = localStorage.getItem('token')

    function processSSEText(text, onChunk) {
      var lines = text.split('\n')
      for (var i = 0; i < lines.length; i++) {
        var line = lines[i]
        if (line.indexOf('data: ') === 0) {
          try {
            var parsed = JSON.parse(line.slice(6))
            onChunk(parsed)
          } catch (e) { /* ignore */ }
        }
      }
    }

    // 优先使用 fetch + ReadableStream（现代浏览器）
    if (typeof ReadableStream !== 'undefined' && typeof fetch !== 'undefined') {
      return fetch('/api/chat/send/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': 'Bearer ' + token,
        },
        body: JSON.stringify(data),
        signal: signal,
      }).then(function (response) {
        if (!response.body || typeof response.body.getReader !== 'function') {
          return response.text().then(function (text) {
            processSSEText(text, onChunk)
          })
        }
        var reader = response.body.getReader()
        var decoder = new TextDecoder()
        var buffer = ''
        function read() {
          return reader.read().then(function (result) {
            if (result.done) return
            buffer += decoder.decode(result.value, { stream: true })
            var lines = buffer.split('\n')
            buffer = lines.pop() || ''
            for (var i = 0; i < lines.length; i++) {
              if (lines[i].indexOf('data: ') === 0) {
                try {
                  var parsed = JSON.parse(lines[i].slice(6))
                  onChunk(parsed)
                } catch (e) { /* ignore */ }
              }
            }
            return read()
          })
        }
        return read()
      }).catch(function (e) {
        if (e && e.name === 'AbortError') return
        throw e
      })
    }

    // XHR 回退（不支持 ReadableStream 的旧浏览器）
    return new Promise(function (resolve, reject) {
      var xhr = new XMLHttpRequest()
      var lastIndex = 0
      xhr.open('POST', '/api/chat/send/stream', true)
      xhr.setRequestHeader('Content-Type', 'application/json')
      xhr.setRequestHeader('Authorization', 'Bearer ' + token)

      if (signal) {
        signal.addEventListener('abort', function () {
          xhr.abort()
        })
      }

      xhr.onprogress = function () {
        var newText = xhr.responseText.substring(lastIndex)
        lastIndex = xhr.responseText.length
        processSSEText(newText, onChunk)
      }

      xhr.onload = function () {
        var remaining = xhr.responseText.substring(lastIndex)
        if (remaining) processSSEText(remaining, onChunk)
        resolve()
      }

      xhr.onerror = function () { reject(new Error('网络请求失败')) }
      xhr.onabort = function () { resolve() }
      xhr.send(JSON.stringify(data))
    })
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
