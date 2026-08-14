import { useEffect, useState } from 'react'
import { adminApi } from '../api/client'

export default function SettingsPage() {
  const [status, setStatus] = useState<any>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    adminApi.status().then(setStatus).catch((e) => setError(e.message))
  }, [])

  return (
    <div className="p-6 max-w-3xl">
      <h1 className="text-xl font-semibold mb-5">设置 / 系统状态</h1>
      {error && <div className="mb-4 p-3 bg-rose-50 text-rose-600 rounded-lg text-sm">{error}</div>}

      {status && (
        <div className="space-y-4">
          <Card title="系统信息">
            <Row k="服务名" v={status.server?.title} />
            <Row k="版本" v={status.server?.version} />
            <Row k="端口" v={status.server?.port} />
          </Card>
          <Card title="能力统计">
            <Row k="内置工具" v={status.builtin_tools} />
            <Row k="MCP 工具" v={status.mcp_tools} />
            <Row k="技能数" v={status.skills} />
          </Card>
          <Card title="MCP 服务状态">
            {Object.entries(status.services || {}).map(([name, s]: any) => (
              <Row key={name} k={name} v={s} />
            ))}
            {Object.keys(status.services || {}).length === 0 && (
              <div className="text-sm text-slate-400">暂无 MCP 服务</div>
            )}
          </Card>
          <Card title="网页版 DeepSeek 桥接说明">
            <ol className="text-sm text-slate-600 space-y-1 list-decimal list-inside">
              <li>启动后端：<code className="bg-slate-100 px-1 rounded">python backend/main.py</code></li>
              <li>安装 Tampermonkey 扩展</li>
              <li>导入 <code className="bg-slate-100 px-1 rounded">bridge/ds-bridge.user.js</code></li>
              <li>打开 chat.deepseek.com，脚本自动注入 role_card 并连接本地后端</li>
            </ol>
          </Card>
        </div>
      )}
    </div>
  )
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="p-4 bg-white rounded-xl border border-slate-200 shadow-sm">
      <h2 className="text-sm font-medium text-slate-500 mb-2">{title}</h2>
      {children}
    </div>
  )
}

function Row({ k, v }: { k: string; v: any }) {
  return (
    <div className="flex justify-between py-1 text-sm border-b border-slate-50 last:border-0">
      <span className="text-slate-500">{k}</span>
      <span className="text-slate-700">{String(v)}</span>
    </div>
  )
}
