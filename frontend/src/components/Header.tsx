import { useEffect, useState } from 'react'
import { ShieldCheck, Sliders, Activity, Layers } from 'lucide-react'
import { ApiService } from '../services/api'
import type { JobHistoryItem } from '../types/api'

interface HeaderProps {
  onOpenSettings: () => void
  onOpenReport: () => void
  history: JobHistoryItem[]
  canExport: boolean
}

export function Header({ onOpenSettings, onOpenReport, history, canExport }: HeaderProps) {
  const [health, setHealth] = useState({ ok: false, message: 'Проверка связи...' })
  useEffect(() => {
    let active = true
    const check = async () => { const result = await ApiService.checkHealth(); if (active) setHealth(result) }
    void check()
    const timer = setInterval(check, 8000)
    return () => { active = false; clearInterval(timer) }
  }, [])
  return (
    <header style={{ padding: '1.25rem 0', marginBottom: '1.5rem', borderBottom: '1px solid var(--border-subtle)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', alignItems: 'center', gap: '1rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
          <ShieldCheck size={32} color="var(--accent-cyan)" />
          <div><h1 style={{ fontSize: '1.4rem' }}>SANA — анализ видео</h1><p style={{ color: 'var(--text-muted)' }}>Проверка событий кассовой зоны</p></div>
        </div>
        <div style={{ display: 'flex', gap: '.75rem', alignItems: 'center', flexWrap: 'wrap' }}>
          <button className="btn-secondary" onClick={onOpenSettings} title={health.message} style={{ color: health.ok ? 'var(--accent-emerald)' : '#fda4af' }}>
            <Sliders size={16} />{health.ok ? 'Сервер доступен' : 'Проверить соединение'}
          </button>
          <button className="btn-secondary" onClick={onOpenReport} disabled={!canExport}><Layers size={16} />Результат анализа</button>
        </div>
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '1.5rem', marginTop: '1rem', color: 'var(--text-muted)' }}>
        <span><Activity size={14} /> Завершено в этой вкладке: {history.length}</span>
        <span>События обнаружены: {history.filter((item) => item.verdict === 'violation').length}</span>
        <span>Без событий: {history.filter((item) => item.verdict === 'no_violation').length}</span>
      </div>
    </header>
  )
}
