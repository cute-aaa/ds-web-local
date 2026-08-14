import { useEffect, useState } from 'react'
import { adminApi } from '../api/client'

export default function ToolsPage() {
  const [tools, setTools] = useState<any[]>([])
  const [skills, setSkills] = useState<string[]>([])
  const [error, setError] = useState('')

  useEffect(() => {
    adminApi.tools().then((r) => { setTools(r.tools); setSkills(r.skills) }).catch((e) => setError(e.message))
  }, [])

  const builtin = tools.filter((t) => t.source === 'builtin')
  const mcp = tools.filter((t) => t.source === 'mcp')

  return (
    <div className="p-6 max-w-5xl">
      <h1 className="text-xl font-semibold mb-5">工具总览</h1>
      {error && <div className="mb-4 p-3 bg-rose-50 text-rose-600 rounded-lg text-sm">{error}</div>}

      <Section title={`内置工具（${builtin.length}）`}>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
          {builtin.map((t) => (
            <ToolCard key={t.name} name={t.name} desc={t.description} tag="内置" />
          ))}
        </div>
      </Section>

      <Section title={`MCP 工具（${mcp.length}）`}>
        {mcp.length === 0 ? (
          <div className="text-slate-400 text-sm">暂无 MCP 工具，去「MCP 服务」页添加服务器</div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            {mcp.map((t) => (
              <ToolCard key={t.name} name={t.name} desc={t.description} tag="MCP" />
            ))}
          </div>
        )}
      </Section>

      <Section title={`技能（${skills.length}）`}>
        {skills.length === 0 ? (
          <div className="text-slate-400 text-sm">暂无技能，去「技能」页添加</div>
        ) : (
          <div className="flex flex-wrap gap-2">
            {skills.map((s) => (
              <span key={s} className="px-3 py-1 bg-indigo-50 text-indigo-600 rounded-lg text-sm">{s}</span>
            ))}
          </div>
        )}
      </Section>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mb-6">
      <h2 className="text-sm font-medium text-slate-500 mb-3">{title}</h2>
      {children}
    </div>
  )
}

function ToolCard({ name, desc, tag }: { name: string; desc: string; tag: string }) {
  return (
    <div className="p-3 bg-white rounded-lg border border-slate-200">
      <div className="flex items-center gap-2">
        <span className="font-mono text-sm font-medium">{name}</span>
        <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
          tag === 'MCP' ? 'bg-purple-100 text-purple-600' : 'bg-slate-100 text-slate-500'
        }`}>{tag}</span>
      </div>
      <div className="mt-1 text-xs text-slate-500">{desc}</div>
    </div>
  )
}
