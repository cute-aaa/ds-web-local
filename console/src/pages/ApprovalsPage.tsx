import { useEffect, useState } from 'react'
import { approvalsApi, Approval } from '../api/client'

export default function ApprovalsPage() {
  const [pending, setPending] = useState<Approval[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})

  const refresh = async () => {
    try {
      const r = await approvalsApi.list()
      setPending(r.approvals.filter(a => a.status === 'pending'))
      setError('')
    } catch (e: any) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { refresh() }, [])

  const decide = async (a: Approval, approve: boolean) => {
    try {
      if (approve) await approvalsApi.approve(a.id)
      else await approvalsApi.reject(a.id)
      refresh()
    } catch (e: any) {
      setError(e.message)
    }
  }

  const fmtTime = (ts: number) => new Date(ts * 1000).toLocaleString()

  return (
    <div className="p-6 max-w-5xl">
      <div className="flex items-center justify-between mb-5">
        <h1 className="text-xl font-semibold">审批队列</h1>
        <button onClick={refresh}
          className="px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm hover:bg-indigo-700 transition">
          刷新
        </button>
      </div>

      {error && <div className="mb-4 p-3 bg-rose-50 text-rose-600 rounded-lg text-sm">{error}</div>}

      {loading ? (
        <div className="text-slate-400 text-sm">加载中...</div>
      ) : pending.length === 0 ? (
        <div className="p-8 text-center text-slate-400 border border-dashed border-slate-300 rounded-xl">
          暂无待审批调用
        </div>
      ) : (
        <div className="space-y-3">
          {pending.map((a) => (
            <div key={a.id} className="p-4 bg-white rounded-xl border border-slate-200 shadow-sm">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-amber-100 text-amber-700">待审批</span>
                  <span className="font-mono font-medium">{a.tool}</span>
                  <span className="text-xs text-slate-400">#{a.id}</span>
                  <span className="text-xs text-slate-400">{fmtTime(a.created_at)}</span>
                </div>
                <div className="flex gap-2">
                  <ActionBtn onClick={() => decide(a, true)} label="批准" primary />
                  <ActionBtn onClick={() => decide(a, false)} label="拒绝" danger />
                </div>
              </div>
              <button className="mt-2 text-xs text-indigo-600 hover:underline"
                onClick={() => setExpanded({ ...expanded, [a.id]: !expanded[a.id] })}>
                {expanded[a.id] ? '收起参数' : '展开参数 JSON'}
              </button>
              {expanded[a.id] && (
                <pre className="mt-2 p-3 bg-slate-900 text-slate-100 rounded-lg text-xs overflow-auto whitespace-pre-wrap">
                  {JSON.stringify(a.arguments, null, 2)}
                </pre>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function ActionBtn({ onClick, label, danger, primary }: { onClick: () => void; label: string; danger?: boolean; primary?: boolean }) {
  const cls = primary
    ? 'bg-indigo-50 text-indigo-600 hover:bg-indigo-100'
    : danger
      ? 'bg-rose-50 text-rose-600 hover:bg-rose-100'
      : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
  return (
    <button onClick={onClick}
      className={`px-3 py-1 rounded-lg text-xs font-medium transition ${cls}`}>
      {label}
    </button>
  )
}
