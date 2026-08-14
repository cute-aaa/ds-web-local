import { useEffect, useState } from 'react'
import { skillsApi, Skill } from '../api/client'

const emptyForm = {
  name: '', description: '', when_to_use: '',
  modelInvocable: true, userInvocable: true,
  content: '', toolsJson: '', output_template: '',
}

const sourceBadge: Record<string, { cls: string; label: string }> = {
  bundled: { cls: 'bg-blue-100 text-blue-700', label: '内置' },
  user: { cls: 'bg-emerald-100 text-emerald-700', label: '自定义' },
  hermes: { cls: 'bg-purple-100 text-purple-700', label: '导入' },
  legacy: { cls: 'bg-slate-100 text-slate-500', label: '旧版' },
}

export default function SkillsPage() {
  const [skills, setSkills] = useState<Skill[]>([])
  const [error, setError] = useState('')
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState(emptyForm)
  const [editing, setEditing] = useState<string | null>(null)
  const [testResult, setTestResult] = useState('')
  const [detail, setDetail] = useState<{ name: string; skill: Skill } | null>(null)
  const [importOpen, setImportOpen] = useState(false)
  const [importSkills, setImportSkills] = useState<Skill[]>([])
  const [importing, setImporting] = useState<string | null>(null)

  const refresh = async () => {
    try { const r = await skillsApi.list(); setSkills(r.skills); setError('') }
    catch (e: any) { setError(e.message) }
  }

  useEffect(() => { refresh() }, [])

  const openEdit = async (name: string) => {
    try {
      const r = await skillsApi.load(name)
      const s = r.skill
      setEditing(name)
      setForm({
        name,
        description: s.description || '',
        when_to_use: s.when_to_use || '',
        modelInvocable: s.invocation?.model ?? true,
        userInvocable: s.invocation?.user ?? true,
        content: s.content || '',
        toolsJson: JSON.stringify(s.steps || s.tools || [], null, 2),
        output_template: s.output_template || '',
      })
      setShowForm(true)
    } catch (e: any) { setError(e.message) }
  }

  const save = async () => {
    if (!form.name) { setError('请填写技能名'); return }
    if (!editing && !form.content.trim()) { setError('新建技能时请填写正文（content）'); return }
    let tools: any[] = []
    if (form.toolsJson.trim()) {
      try { tools = JSON.parse(form.toolsJson) }
      catch { setError('工具步骤 JSON 格式错误'); return }
    }
    const body: Skill = {
      name: form.name,
      description: form.description,
      when_to_use: form.when_to_use || undefined,
      model_invocable: form.modelInvocable,
      user_invocable: form.userInvocable,
      content: form.content,
      tools,
      output_template: form.output_template || undefined,
    } as any
    try {
      if (editing) await skillsApi.update(editing, body)
      else await skillsApi.create(body)
      setShowForm(false); setEditing(null); setForm(emptyForm); refresh()
    } catch (e: any) { setError(e.message) }
  }

  const remove = async (name: string) => {
    if (!confirm(`删除技能 ${name}？`)) return
    try { await skillsApi.remove(name); refresh() } catch (e: any) { setError(e.message) }
  }

  const test = async (name: string) => {
    try { const r: any = await skillsApi.execute(name, {}); setTestResult(JSON.stringify(r, null, 2)) }
    catch (e: any) { setTestResult('执行失败: ' + e.message) }
  }

  const openImport = async () => {
    setImportOpen(true)
    setImporting(null)
    try {
      const r = await skillsApi.catalog()
      setImportSkills(r.skills.filter(s => s.source === 'hermes'))
      setError('')
    } catch (e: any) { setError(e.message) }
  }

  const doImport = async (name: string) => {
    setImporting(name)
    try {
      await skillsApi.importSkill('hermes', name)
      setImportOpen(false)
      setImportSkills([])
      refresh()
    } catch (e: any) { setError(e.message) } finally { setImporting(null) }
  }

  const openDetail = async (name: string) => {
    try { const r = await skillsApi.load(name); setDetail(r) } catch (e: any) { setError(e.message) }
  }

  return (
    <div className="p-6 max-w-5xl">
      <div className="flex items-center justify-between mb-5">
        <h1 className="text-xl font-semibold">技能管理</h1>
        <div className="flex gap-2">
          <button onClick={openImport}
            className="px-4 py-2 bg-slate-100 text-slate-600 rounded-lg text-sm hover:bg-slate-200 transition">
            ↓ 从 Hermes 导入
          </button>
          <button onClick={() => { setShowForm(!showForm); if (showForm) { setEditing(null); setForm(emptyForm) } }}
            className="px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm hover:bg-indigo-700 transition">
            {showForm ? '取消' : '+ 新建技能'}
          </button>
        </div>
      </div>

      {error && <div className="mb-4 p-3 bg-rose-50 text-rose-600 rounded-lg text-sm">{error}</div>}

      {showForm && (
        <div className="mb-6 p-4 bg-white rounded-xl border border-slate-200 shadow-sm space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <Field label="技能名 *（kebab-case）"><input className={inputCls} value={form.name} disabled={!!editing}
              onChange={e => setForm({ ...form, name: e.target.value })} placeholder="如 my-skill" /></Field>
            <Field label="描述 *"><input className={inputCls} value={form.description}
              onChange={e => setForm({ ...form, description: e.target.value })} /></Field>
          </div>
          <Field label="适用场景（when_to_use，告诉模型何时选用此技能）">
            <input className={inputCls} value={form.when_to_use}
              onChange={e => setForm({ ...form, when_to_use: e.target.value })} /></Field>
          <div className="flex gap-6">
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={form.modelInvocable}
                onChange={e => setForm({ ...form, modelInvocable: e.target.checked })} />
              模型可调用（model-invocable）
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={form.userInvocable}
                onChange={e => setForm({ ...form, userInvocable: e.target.checked })} />
              用户可调用（user-invocable）
            </label>
          </div>
          <Field label={editing ? '正文（content，Markdown）' : '正文 *（content，Markdown，新建必填）'}>
            <textarea className={inputCls + ' h-40 font-mono'} value={form.content}
              onChange={e => setForm({ ...form, content: e.target.value })}
              placeholder='# 技能说明&#10;&#10;在这里编写技能正文，指导模型如何使用该技能。' /></Field>
          <Field label="工具步骤（可选，JSON 数组，支持 $input.xxx 占位符）">
            <textarea className={inputCls + ' h-32 font-mono'} value={form.toolsJson}
              onChange={e => setForm({ ...form, toolsJson: e.target.value })}
              placeholder='[{"id":"w","name":"write_file","arguments":{"path":"out.txt","content":"$input.text"}}]' /></Field>
          <Field label="输出模板（可选，如 {{w.bytes}}）">
            <input className={inputCls} value={form.output_template}
              onChange={e => setForm({ ...form, output_template: e.target.value })} /></Field>
          <button onClick={save} className="px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm hover:bg-indigo-700">
            {editing ? '更新' : '保存'}
          </button>
        </div>
      )}

      {skills.length === 0 ? (
        <div className="p-8 text-center text-slate-400 border border-dashed border-slate-300 rounded-xl">
          暂无技能，点击右上角新建或从 Hermes 导入
        </div>
      ) : (
        <div className="space-y-3">
          {skills.map((s) => {
            const badge = sourceBadge[s.source || ''] || sourceBadge.legacy
            return (
              <div key={s.name} className="p-4 bg-white rounded-xl border border-slate-200 shadow-sm">
                <div className="flex items-center justify-between">
                  <button onClick={() => openDetail(s.name)} className="flex items-center gap-3 text-left group">
                    <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${badge.cls}`}>{badge.label}</span>
                    <span className="font-medium group-hover:text-indigo-600 transition">{s.name}</span>
                    {s.invocation && !s.invocation.model && (
                      <span className="px-2 py-0.5 rounded bg-slate-100 text-xs text-slate-500">仅用户</span>
                    )}
                    {s.invocation && !s.invocation.user && (
                      <span className="px-2 py-0.5 rounded bg-slate-100 text-xs text-slate-500">仅模型</span>
                    )}
                    {s.description && <span className="text-sm text-slate-400">{s.description}</span>}
                  </button>
                  <div className="flex gap-2">
                    <ActionBtn onClick={() => test(s.name)} label="测试执行" />
                    <ActionBtn onClick={() => openEdit(s.name)} label="编辑" />
                    <ActionBtn onClick={() => remove(s.name)} label="删除" danger />
                  </div>
                </div>
                {s.when_to_use && <div className="mt-2 text-xs text-slate-500">适用场景：{s.when_to_use}</div>}
              </div>
            )
          })}
        </div>
      )}

      {testResult && (
        <div className="mt-6 p-4 bg-slate-900 text-slate-100 rounded-xl">
          <div className="flex justify-between mb-2">
            <span className="text-sm font-medium">测试结果</span>
            <button onClick={() => setTestResult('')} className="text-slate-400 hover:text-white">关闭</button>
          </div>
          <pre className="text-xs overflow-auto whitespace-pre-wrap">{testResult}</pre>
        </div>
      )}

      {/* 详情视图 */}
      {detail && (
        <div className="fixed inset-0 z-40 bg-black/40 flex items-center justify-center p-6" onClick={() => setDetail(null)}>
          <div className="bg-white rounded-xl shadow-xl max-w-3xl w-full max-h-[85vh] flex flex-col" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between px-5 py-3 border-b border-slate-200">
              <div className="flex items-center gap-3">
                <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${sourceBadge[detail.skill.source || '']?.cls || sourceBadge.legacy.cls}`}>
                  {sourceBadge[detail.skill.source || '']?.label || sourceBadge.legacy.label}
                </span>
                <span className="font-semibold">{detail.name}</span>
              </div>
              <button onClick={() => setDetail(null)} className="text-slate-400 hover:text-slate-600">关闭</button>
            </div>
            <div className="flex-1 overflow-auto p-5 space-y-4">
              <div>
                <div className="text-xs text-slate-500 mb-1">描述</div>
                <div className="text-sm">{detail.skill.description}</div>
              </div>
              {detail.skill.when_to_use && (
                <div>
                  <div className="text-xs text-slate-500 mb-1">适用场景</div>
                  <div className="text-sm">{detail.skill.when_to_use}</div>
                </div>
              )}
              <div>
                <div className="text-xs text-slate-500 mb-1">正文（content 预览）</div>
                <pre className="p-3 bg-slate-50 border border-slate-200 rounded-lg text-xs whitespace-pre-wrap overflow-auto max-h-60">
                  {detail.skill.content || '（无正文）'}
                </pre>
              </div>
              <div>
                <div className="text-xs text-slate-500 mb-1">完整定义（含 steps/parameters）</div>
                <pre className="p-3 bg-slate-900 text-slate-100 rounded-lg text-xs overflow-auto whitespace-pre-wrap max-h-72">
                  {JSON.stringify(detail.skill, null, 2)}
                </pre>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 从 Hermes 导入 */}
      {importOpen && (
        <div className="fixed inset-0 z-40 bg-black/40 flex items-center justify-center p-6" onClick={() => setImportOpen(false)}>
          <div className="bg-white rounded-xl shadow-xl max-w-2xl w-full max-h-[80vh] flex flex-col" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between px-5 py-3 border-b border-slate-200">
              <span className="font-semibold">从 Hermes 导入技能</span>
              <button onClick={() => setImportOpen(false)} className="text-slate-400 hover:text-slate-600">关闭</button>
            </div>
            <div className="flex-1 overflow-auto p-4 space-y-2">
              {importSkills.length === 0 ? (
                <div className="p-8 text-center text-slate-400 border border-dashed border-slate-300 rounded-xl">
                  Hermes 源中暂无可用技能
                </div>
              ) : importSkills.map((s) => (
                <button key={s.name} onClick={() => doImport(s.name)} disabled={importing === s.name}
                  className="w-full text-left p-3 rounded-lg border border-slate-200 hover:border-indigo-400 hover:bg-indigo-50 transition disabled:opacity-50">
                  <div className="flex items-center justify-between">
                    <span className="font-medium">{s.name}</span>
                    <span className="text-xs text-indigo-600">{importing === s.name ? '导入中...' : '导入'}</span>
                  </div>
                  {s.description && <div className="mt-1 text-xs text-slate-500">{s.description}</div>}
                </button>
              ))}
            </div>
          </div>
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
