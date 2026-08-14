// API 客户端（相对路径，Vite dev 代理 /api -> localhost:8088）

async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!resp.ok) {
    const body = await resp.json().catch(() => null)
    throw new Error(body?.error?.message || resp.statusText)
  }
  return resp.json()
}

export interface MCPReconnect {
  enabled?: boolean
  initial_delay_ms?: number
  max_delay_ms?: number
  max_attempts?: number
}

export interface MCPConfig {
  name: string
  transport: string
  command?: string
  args?: string[]
  url?: string
  env?: Record<string, string>
  auto_start?: boolean
  timeout?: number
  description?: string
  status?: string
  tools?: string[]
  reconnect?: MCPReconnect
  tool_call_timeout_ms?: number
  fail_on_startup_error?: boolean
}

export interface SkillInvocation {
  model: boolean
  user: boolean
}

export interface Skill {
  name: string
  description?: string
  when_to_use?: string
  invocation?: SkillInvocation
  source?: string // bundled | user | hermes | legacy
  content?: string
  prompt?: string
  steps?: any[]
  tools?: any[]
  output_template?: string
  parameters?: Record<string, any>
}

export interface CredentialRef {
  ref: string
  configured: boolean
  source: string | null // env | file | null
  writable: boolean
}

export interface Approval {
  id: string
  tool: string
  arguments: any
  created_at: number
  status: 'pending' | 'approved' | 'rejected'
  decided_at?: number | null
}

export const mcpApi = {
  list: () => api<{ services: MCPConfig[] }>('/api/mcp'),
  add: (body: any) => api('/api/mcp', { method: 'POST', body: JSON.stringify(body) }),
  remove: (name: string) => api(`/api/mcp/${name}`, { method: 'DELETE' }),
  start: (name: string) => api(`/api/mcp/${name}/start`, { method: 'POST', body: '{}' }),
  stop: (name: string) => api(`/api/mcp/${name}/stop`, { method: 'POST', body: '{}' }),
  restart: (name: string) => api(`/api/mcp/${name}/restart`, { method: 'POST', body: '{}' }),
  tools: (name: string) => api<{ tools: any[] }>(`/api/mcp/${name}/tools`),
}

export const skillsApi = {
  list: () => api<{ skills: Skill[]; digest: string; complete: boolean }>('/api/skills'),
  catalog: () => api<{ skills: Skill[]; digest: string; complete: boolean }>('/api/skills/catalog'),
  load: (name: string) => api<{ name: string; skill: Skill }>(`/api/skills/${name}/load`),
  importSkill: (source_dir: string, name: string) =>
    api(`/api/skills/import`, { method: 'POST', body: JSON.stringify({ source_dir, name }) }),
  create: (body: Skill) => api('/api/skills', { method: 'POST', body: JSON.stringify(body) }),
  update: (name: string, body: Skill) => api(`/api/skills/${name}`, { method: 'PUT', body: JSON.stringify(body) }),
  remove: (name: string) => api(`/api/skills/${name}`, { method: 'DELETE' }),
  execute: (name: string, inputs: any) => api(`/api/skills/${name}/execute`, { method: 'POST', body: JSON.stringify({ inputs }) }),
}

export const credentialsApi = {
  list: () => api<{ refs: CredentialRef[] }>('/api/credentials'),
  set: (ref: string, value: string) =>
    api(`/api/credentials/${encodeURIComponent(ref)}`, { method: 'PUT', body: JSON.stringify({ value }) }),
  remove: (ref: string) => api(`/api/credentials/${encodeURIComponent(ref)}`, { method: 'DELETE' }),
}

export const approvalsApi = {
  list: () => api<{ approvals: Approval[] }>('/api/approvals'),
  approve: (id: string) => api(`/api/approvals/${id}/approve`, { method: 'POST', body: '{}' }),
  reject: (id: string) => api(`/api/approvals/${id}/reject`, { method: 'POST', body: '{}' }),
}

export const adminApi = {
  status: () => api<any>('/api/admin/status'),
  tools: () => api<{ tools: any[]; skills: string[] }>('/api/bridge/tools'),
}
