const TOKEN_KEY = 'atelier.jwt'

export const getToken = () => localStorage.getItem(TOKEN_KEY)
export const setToken = (jwt: string) => localStorage.setItem(TOKEN_KEY, jwt)
export const clearToken = () => localStorage.removeItem(TOKEN_KEY)

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  headers.set('Content-Type', 'application/json')
  const jwt = getToken()
  if (jwt) headers.set('Authorization', `Bearer ${jwt}`)

  const res = await fetch(`/api${path}`, { ...init, headers })
  if (res.status === 401) {
    clearToken()
    if (location.pathname !== '/login') location.assign('/login')
  }
  if (!res.ok) throw new ApiError(res.status, await res.text())
  return res.json() as Promise<T>
}

export interface Project {
  id: string
  name: string
  kicker: string | null
  design_count: number
}
