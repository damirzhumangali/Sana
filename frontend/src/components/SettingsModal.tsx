import { useState } from 'react'
import { X, RefreshCw } from 'lucide-react'
import { ApiService } from '../services/api'

interface SettingsModalProps { isOpen: boolean; onClose: () => void }

export function SettingsModal({ isOpen, onClose }: SettingsModalProps) {
  const [testing, setTesting] = useState(false)
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null)
  if (!isOpen) return null

  const check = async () => {
    setTesting(true)
    setResult(await ApiService.checkHealth())
    setTesting(false)
  }
  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(7,11,20,.88)', zIndex: 1200, display: 'grid', placeItems: 'center', padding: '1rem' }} onClick={onClose}>
      <div className="glass-panel" role="dialog" aria-modal="true" aria-labelledby="connection-title" style={{ padding: '1.5rem', maxWidth: 480 }} onClick={(event) => event.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: '2rem', marginBottom: '1rem' }}>
          <h2 id="connection-title">Соединение с сервером</h2>
          <button onClick={onClose} aria-label="Закрыть"><X size={20} /></button>
        </div>
        <p>Проверка доступности сервера загрузки и получения результатов.</p>
        {result && <p role="status" style={{ marginTop: '1rem', color: result.ok ? 'var(--accent-emerald)' : '#fda4af' }}>{result.message}</p>}
        <button className="btn-secondary" style={{ marginTop: '1rem' }} onClick={check} disabled={testing}>
          <RefreshCw size={16} />{testing ? 'Проверка...' : 'Проверить соединение'}
        </button>
      </div>
    </div>
  )
}
