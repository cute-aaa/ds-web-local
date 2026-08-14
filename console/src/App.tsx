import { NavLink, Routes, Route } from 'react-router-dom'
import McpPage from './pages/McpPage'
import SkillsPage from './pages/SkillsPage'
import CredentialsPage from './pages/CredentialsPage'
import ApprovalsPage from './pages/ApprovalsPage'
import ToolsPage from './pages/ToolsPage'
import SettingsPage from './pages/SettingsPage'
import ToolLogsPage from './pages/ToolLogsPage'

const nav = [
  { to: '/', label: 'MCP 服务', icon: '🔌', end: true },
  { to: '/skills', label: '技能', icon: '🧩' },
  { to: '/tool-logs', label: '工具日志', icon: '📜' },
  { to: '/credentials', label: '凭据', icon: '🔑' },
  { to: '/approvals', label: '审批', icon: '✅' },
  { to: '/tools', label: '工具总览', icon: '🛠️' },
  { to: '/settings', label: '设置', icon: '⚙️' },
]

export default function App() {
  return (
    <div className="flex h-screen bg-slate-50 text-slate-800">
      {/* 侧边导航 */}
      <aside className="w-56 shrink-0 bg-white border-r border-slate-200 flex flex-col">
        <div className="h-14 flex items-center gap-2 px-4 border-b border-slate-100">
          <span className="text-xl">🤖</span>
          <div>
            <div className="font-semibold leading-tight">DS Web Local</div>
            <div className="text-xs text-slate-400">本地 Agent 能力中台</div>
          </div>
        </div>
        <nav className="flex-1 p-2 space-y-1">
          {nav.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.end}
              className={({ isActive }) =>
                `flex items-center gap-2 px-3 py-2 rounded-lg text-sm transition ${
                  isActive ? 'bg-indigo-50 text-indigo-600 font-medium' : 'text-slate-600 hover:bg-slate-100'
                }`
              }
            >
              <span>{n.icon}</span>
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div className="p-3 text-xs text-slate-400 border-t border-slate-100">
          后端: localhost:8088
        </div>
      </aside>

      {/* 主内容 */}
      <main className="flex-1 overflow-auto">
        <Routes>
          <Route path="/" element={<McpPage />} />
          <Route path="/skills" element={<SkillsPage />} />
          <Route path="/credentials" element={<CredentialsPage />} />
          <Route path="/approvals" element={<ApprovalsPage />} />
          <Route path="/tools" element={<ToolsPage />} />
          <Route path="/tool-logs" element={<ToolLogsPage />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Routes>
      </main>
    </div>
  )
}
