import { useEffect, useState } from 'react'
import { mcpApi, MCPConfig } from '../api/client'

const statusColor: Record<string, string> = {
  running: 'bg-emerald-100 text-emerald-700',
  stopped: 'bg-slate-100 text-slate-500',
  failed: 'bg-rose-100 text-rose-700',
  starting: 'bg-amber-100 text-amber-700',
}

const emptyForm = { name: '', transport: 'stdio', command: '', args: '', url: '', auto_start: false, description: '' }

export default function McpPage() {
  const [services, setServices] = useState<MCPConfig[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState(emptyForm)

  const refresh = async () => {
    try {
      const r = await mcpApi.list()
      setServices(r.services)
      setError('')
    } catch (e: any) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { refresh() }, [])

  const act = async (fn: () => Promise<any>, label: string) => {
    try { await fn(); refresh() } catch (e: any) { setError(`${label}失败: ${e.message}`) }
  }

  const add = async () => {
    if (!form.name) { setError('请填写服务名'); return }
    try {
      await mcpApi.add({
        name: form.name,
        transport: form.transport,
        command: form.command || undefined,
        args: form.args ? form.args.split(/\s+/).filter(Boolean) : [],
        url: form.url || undefined,
        auto_start: form.auto_start,
        description: form.description,
      })
      setShowForm(false)
      setForm(emptyForm)
      refresh()
    } catch (e: any) { setError(e.message) }
  }

  return (
    <div className="p-6 max-w-5xl">
      <div className="flex items-center justify-between mb-5">
        <h1 className="text-xl font-semibold">MCP 服务器</h1>
        <button onClick={() => setShowForm(!showForm)}
          className="px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm hover:bg-indigo-700 transition">
          {showForm ? '取消' : '+ 添加 MCP 服务器'}
        </button>
      </div>

      {error && <div className="mb-4 p-3 bg-rose-50 text-rose-600 rounded-lg text-sm">{error}</div>}

      {/* 添加表单 */}
      {showForm && (
        <div className="mb-6 p-4 bg-white rounded-xl border border-slate-200 shadow-sm space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <Field label="服务名 *"><input className={inputCls} value={form.name}
              onChange={e => setForm({ ...form, name: e.target.value })} placeholder="如 filesystem" /></Field>
            <Field label="传输方式">
              <select className={inputCls} value={form.transport}
                onChange={e => setForm({ ...form, transport: e.target.value })}>
                <option value="stdio">stdio（本地进程）</option>
                <option value="sse">SSE</option>
                <option value="streamable-http">Streamable HTTP</option>
              </select>
            </Field>
          </div>
          {form.transport === 'stdio' ? (
            <div className="grid grid-cols-2 gap-3">
              <Field label="命令 *"><input className={inputCls} value={form.command}
                onChange={e => setForm({ ...form, command: e.target.value })} placeholder="如 npx / python / node" /></Field>
              <Field label="参数（空格分隔）"><input className={inputCls} value={form.args}
                onChange={e => setForm({ ...form, args: e.target.value })} placeholder="如 -y @modelcontextprotocol/server-filesystem C:/" /></Field>
            </div>
          ) : (
            <Field label="URL *"><input className={inputCls} value={form.url}
              onChange={e => setForm({ ...form, url: e.target.value })} placeholder="如 http://localhost:8000/sse" /></Field>
          )}
          <Field label="描述"><input className={inputCls} value={form.description}
            onChange={e => setForm({ ...form, description: e.target.value })} /></Field>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={form.auto_start}
              onChange={e => setForm({ ...form, auto_start: e.target.checked })} />
            启动后自动运行
          </label>
          <button onClick={add} className="px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm hover:bg-indigo-700">
            保存
          </button>
        </div>
      )}

      {/* 服务列表 */}
      {loading ? (
        <div className="text-slate-400 text-sm">加载中...</div>
      ) : services.length === 0 ? (
        <div className="p-8 text-center text-slate-400 border border-dashed border-slate-300 rounded-xl">
          暂无 MCP 服务器，点击右上角添加
        </div>
      ) : (
        <div className="space-y-3">
          {services.map((s) => (
            <div key={s.name} className="p-4 bg-white rounded-xl border border-slate-200 shadow-sm">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${statusColor[s.status || 'stopped']}`}>
                    {s.status || 'stopped'}
                  </span>
                  <span className="font-medium">{s.name}</span>
                  {s.description && <span className="text-sm text-slate-400">{s.description}</span>}
                </div>
                <div className="flex gap-2">
                  {s.status === 'running' ? (
                    <>
                      <ActionBtn onClick={() => act(() => mcpApi.restart(s.name), '重启')} label="重启" />
                      <ActionBtn onClick={() => act(() => mcpApi.stop(s.name), '停止')} label="停止" danger />
                    </>
                  ) : (
                    <ActionBtn onClick={() => act(() => mcpApi.start(s.name), '启动')} label="启动" />
                  )}
                  <ActionBtn onClick={() => act(() => mcpApi.remove(s.name), '删除')} label="删除" danger />
                </div>
              </div>
              <div className="mt-2 text-xs text-slate-500">
                <span className="font-medium">{s.transport}</span>
                {s.command && <span> · {s.command} {(s.args || []).join(' ')}</span>}
                {s.url && <span> · {s.url}</span>}
              </div>
              {(s.tools || []).length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1">
                  {(s.tools || []).map((t) => (
                    <span key={t} className="px-2 py-0.5 bg-slate-100 rounded text-xs text-slate-600 font-mono">{t}</span>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

const inputCls = 'w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:border-indigo-400'

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="block text-xs text-slate-500 mb-1">{label}</span>
      {children}
    </label>
  )
}

function ActionBtn({ onClick, label, danger }: { onClick: () => void; label: string; danger?: boolean }) {
  return (
    <button onClick={onClick}
      className={`px-3 py-1 rounded-lg text-xs font-medium transition ${
        danger ? 'bg-rose-50 text-rose-600 hover:bg-rose-100' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
      }`}>
      {label}
    </button>
  )
}
