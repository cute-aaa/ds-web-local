import { useEffect, useState } from 'react'
import { credentialsApi, CredentialRef } from '../api/client'

export default function CredentialsPage() {
  const [refs, setRefs] = useState<CredentialRef[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [settingRef, setSettingRef] = useState<string | null>(null)
  const [value, setValue] = useState('')
  const [saving, setSaving] = useState(false)

  const refresh = async () => {
    try {
      const r = await credentialsApi.list()
      setRefs(r.refs)
      setError('')
    } catch (e: any) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { refresh() }, [])

  const save = async () => {
    if (!settingRef) return
    if (!value) { setError('请输入凭据值'); return }
    setSaving(true)
    try {
      await credentialsApi.set(settingRef, value)
      setSettingRef(null)
      setValue('')
      refresh()
    } catch (e: any) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  const remove = async (ref: string) => {
    if (!confirm(`清除凭据 ${ref}？该操作不可撤销。`)) return
    try { await credentialsApi.remove(ref); refresh() } catch (e: any) { setError(e.message) }
  }

  const badge = (r: CredentialRef) => {
    if (r.configured && r.source === 'env') {
      return <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-amber-100 text-amber-700">只读 · env</span>
    }
    if (r.configured) {
      return <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-emerald-100 text-emerald-700">已配置</span>
    }
    return <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-slate-100 text-slate-500">未配置</span>
  }

  return (
    <div className="p-6 max-w-5xl">
      <div className="flex items-center justify-between mb-5">
        <h1 className="text-xl font-semibold">凭据管理</h1>
      </div>

      <div className="mb-5 p-3 bg-indigo-50 text-indigo-700 rounded-lg text-sm leading-relaxed">
        配置只存引用不存值，值存储于本地 .credentials.yaml，控制台永不显示明文
      </div>

      {error && <div className="mb-4 p-3 bg-rose-50 text-rose-600 rounded-lg text-sm">{error}</div>}

      {loading ? (
        <div className="text-slate-400 text-sm">加载中...</div>
      ) : refs.length === 0 ? (
        <div className="p-8 text-center text-slate-400 border border-dashed border-slate-300 rounded-xl">
          暂无凭据引用
        </div>
      ) : (
        <div className="space-y-3">
          {refs.map((r) => (
            <div key={r.ref} className="p-4 bg-white rounded-xl border border-slate-200 shadow-sm">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  {badge(r)}
                  <span className="font-mono font-medium">{r.ref}</span>
                  {!r.configured && r.writable && (
                    <span className="text-sm text-slate-400">点击设置填写值</span>
                  )}
                </div>
                <div className="flex gap-2">
                  {r.writable ? (
                    <>
                      <ActionBtn onClick={() => { setSettingRef(r.ref); setValue(''); setError('') }}
                        label={r.configured ? '修改' : '设置'} />
                      {r.configured && <ActionBtn onClick={() => remove(r.ref)} label="清除" danger />}
                    </>
                  ) : (
                    <span className="text-xs text-amber-600">被环境变量遮蔽，只读</span>
                  )}
                </div>
              </div>

              {settingRef === r.ref && (
                <div className="mt-3 pt-3 border-t border-slate-100 space-y-3">
                  <div className="flex gap-2">
                    <input type="password" className={inputCls} value={value}
                      placeholder="输入凭据值（仅本次写入传输，不会回显）"
                      onChange={e => setValue(e.target.value)}
                      onKeyDown={e => { if (e.key === 'Enter') save() }} />
                    <button onClick={save} disabled={saving}
                      className="shrink-0 px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm hover:bg-indigo-700 disabled:opacity-50">
                      {saving ? '保存中...' : '保存'}
                    </button>
                    <button onClick={() => { setSettingRef(null); setValue('') }}
                      className="shrink-0 px-4 py-2 bg-slate-100 text-slate-600 rounded-lg text-sm hover:bg-slate-200">
                      取消
                    </button>
                  </div>
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
