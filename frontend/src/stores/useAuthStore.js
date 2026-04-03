import { create } from 'zustand'

/**
 * 认证状态管理 — 对接总系统 JWT 统一认证
 */
const useAuthStore = create((set, get) => ({
  token: null,
  user: null,
  isAuthenticated: false,

  initAuth: () => {
    // 1. 优先从 URL ?token=xxx 获取
    const urlParams = new URLSearchParams(window.location.search)
    const urlToken = urlParams.get('token')

    if (urlToken) {
      localStorage.setItem('token', urlToken)
      urlParams.delete('token')
      const cleanUrl = urlParams.toString()
        ? `${window.location.pathname}?${urlParams.toString()}`
        : window.location.pathname
      window.history.replaceState({}, '', cleanUrl)

      set({ token: urlToken, isAuthenticated: true })
      get().fetchUser(urlToken)
      return
    }

    // 2. 从 localStorage 恢复
    const savedToken = localStorage.getItem('token')
    if (savedToken && savedToken !== 'dev-token') {
      set({ token: savedToken, isAuthenticated: true })
      get().fetchUser(savedToken)
      return
    }

    set({ token: null, user: null, isAuthenticated: false })
  },

  fetchUser: async (token) => {
    try {
      const resp = await fetch('/api/auth/me', {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (resp.ok) {
        const user = await resp.json()
        set({ user, isAuthenticated: true })
      } else {
        get().logout()
      }
    } catch {
      console.warn('获取用户信息失败，保留 token 等待重试')
    }
  },

  login: (token, user) => {
    localStorage.setItem('token', token)
    set({ token, user, isAuthenticated: true })
  },

  logout: () => {
    localStorage.removeItem('token')
    localStorage.removeItem('user')
    set({ token: null, user: null, isAuthenticated: false })
  },
}))

export default useAuthStore
