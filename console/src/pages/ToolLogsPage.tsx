import { useCallback, useEffect, useState } from 'react'

interface ToolLog {
  time: string
  tool: string
  arguments: string
  status: string
  elapsed_ms: number
  result_preview: string
}

async function fetchLogs(): Promise<ToolLog[]> {
  const resp = await fetch('/api/bridge/tool_logs')
  if (!resp.ok) throw new Error(resp.statusText)
  const data = await resp.json()
  return data?.logs || []
}

export default function ToolLogsPage() {
  const [logs, setLogs] = useState<ToolLog[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const refresh = useCallback(async () => {
    try {
      setLogs(await fetchLogs())
      setError('')
    } catch (e: any) {
      setError('加载失败: ' + (e?.message || e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
    const timer = setInterval(refresh, 5000) // 5s 自动刷新
    return () => clearInterval(timer)
  }, [refresh])

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-semibold text-gray-800">工具调用日志</h1>
        <div className="flex gap-2">
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
          暂无工具调用记录 —— 在 DeepSeek 网页端触发一次工具调用后，这里会显示日志
        </div>
      )}

      <div className="space-y-2">
        {logs.map((l, i) => (
          <div
            key={i}
            className={`rounded-xl border p-3 ${l.status === 'error' ? 'border-red-200 bg-red-50/50' : 'border-gray-200 bg-white'}`}
          >
            <div className="flex items-center gap-3 mb-1">
              <span className="text-xs text-gray-400 font-mono">{l.time}</span>
              <span className="text-sm font-semibold text-indigo-700 break-all">{l.tool}</span>
              <span
                className={`px-2 py-0.5 rounded-full text-xs ${
                  l.status === 'ok' ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'
                }`}
              >
                {l.status === 'ok' ? '✓ 成功' : '✗ 失败'}
              </span>
              <span className="text-xs text-gray-400">{l.elapsed_ms} ms</span>
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
        ))}
      </div>
    </div>
  )
}
