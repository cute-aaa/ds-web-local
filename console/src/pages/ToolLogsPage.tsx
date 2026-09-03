import { useCallback, useEffect, useMemo, useState } from 'react'

interface ToolLog {
  time: string
  caller_id: string
  tab_id?: string
  request_id?: string
  tool: string
  arguments: string
  status: string
  elapsed_ms: number
  result_preview: string
}

async function fetchLogs(callerId: string): Promise<ToolLog[]> {
  const q = callerId ? '?caller_id=' + encodeURIComponent(callerId) : ''
  const resp = await fetch('/api/bridge/tool_logs' + q)
  if (!resp.ok) throw new Error(resp.statusText)
  const data = await resp.json()
  return data?.logs || []
}

function statusLabel(s: string): { text: string; cls: string } {
  if (s === 'ok') return { text: '✓ 成功', cls: 'bg-green-100 text-green-700' }
  if (s === 'approval' || s === 'approval_required') return { text: '⏸ 审批', cls: 'bg-amber-100 text-amber-700' }
  return { text: '✗ 失败', cls: 'bg-red-100 text-red-700' }
}

export default function ToolLogsPage() {
  const [logs, setLogs] = useState<ToolLog[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [callerFilter, setCallerFilter] = useState('')

  const refresh = useCallback(async () => {
    try {
      setLogs(await fetchLogs(callerFilter))
      setError('')
    } catch (e: any) {
      setError('加载失败: ' + (e?.message || e))
    } finally {
      setLoading(false)
    }
  }, [callerFilter])

  useEffect(() => {
    refresh()
    const timer = setInterval(refresh, 5000) // 5s 自动刷新
    return () => clearInterval(timer)
  }, [refresh])

  // 过滤候选 caller：当前日志里的 + 当前选中值（避免切 profile 后下拉选项消失）
  const callerOptions = useMemo(() => {
    const set = new Set<string>((callerFilter ? [callerFilter] : []))
    for (const l of logs) if (l.caller_id) set.add(l.caller_id)
    return [...set].sort()
  }, [logs, callerFilter])

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-semibold text-gray-800">工具调用日志</h1>
        <div className="flex items-center gap-2">
          <select
            value={callerFilter}
            onChange={(e) => setCallerFilter(e.target.value)}
            className="px-2 py-1.5 rounded-lg border border-gray-300 text-sm bg-white"
            title="按调用方（浏览器 profile）筛选"
          >
            <option value="">全部调用方</option>
            {callerOptions.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
          <button
            onClick={refresh}
            className="px-3 py-1.5 rounded-lg bg-indigo-600 text-white text-sm hover:bg-indigo-700"
          >
            刷新
          </button>
          <span className="text-xs text-gray-500 self-center">5s 自动刷新 · 内存保留最近 300 条</span>
        </div>
      </div>

      {error && <div className="mb-4 p-3 rounded-lg bg-red-50 text-red-700 text-sm">{error}</div>}

      {!loading && logs.length === 0 && (
        <div className="p-10 text-center text-gray-400">
          暂无工具调用记录 —— 在 DeepSeek / MiMo 网页端触发一次工具调用后，这里会显示日志
          {callerFilter ? '（当前已按调用方筛选）' : ''}
        </div>
      )}

      <div className="space-y-2">
        {logs.map((l, i) => {
          const st = statusLabel(l.status)
          return (
            <div
              key={i}
              className={`rounded-xl border p-3 ${l.status === 'error' ? 'border-red-200 bg-red-50/50' : 'border-gray-200 bg-white'}`}
            >
              <div className="flex flex-wrap items-center gap-3 mb-1">
                <span className="text-xs text-gray-400 font-mono">{l.time}</span>
                <span className="text-sm font-semibold text-indigo-700 break-all">{l.tool}</span>
                <span className={`px-2 py-0.5 rounded-full text-xs ${st.cls}`}>{st.text}</span>
                <span className="text-xs text-gray-400">{l.elapsed_ms} ms</span>
                {l.caller_id && (
                  <span className="text-xs text-gray-500 font-mono px-1.5 py-0.5 rounded bg-gray-100">
                    caller: {l.caller_id}
                    {l.tab_id ? ` · ${l.tab_id}` : ''}
                  </span>
                )}
                {l.request_id && <span className="text-xs text-gray-400 font-mono">{l.request_id}</span>}
              </div>
              {l.arguments && (
                <div className="text-xs text-gray-600 bg-gray-50 rounded-lg p-2 mb-1 font-mono break-all whitespace-pre-wrap">
                  {l.arguments}
                </div>
              )}
              {l.result_preview && (
                <div className="text-xs text-gray-400 bg-gray-50/50 rounded-lg p-2 font-mono break-all whitespace-pre-wrap">
                  结果: {l.result_preview}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
