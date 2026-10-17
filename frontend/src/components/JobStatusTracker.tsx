import { useEffect, useState } from 'react'
import { Loader2, Clock } from 'lucide-react'

interface JobStatusTrackerProps { jobId: string; fileName: string }
export function JobStatusTracker({ jobId, fileName }: JobStatusTrackerProps) {
  const [seconds, setSeconds] = useState(0)
  useEffect(() => { const timer = setInterval(() => setSeconds((value) => value + 1), 1000); return () => clearInterval(timer) }, [])
  return (
    <div role="status" className="glass-panel-glow" style={{ padding: '1.5rem', marginBottom: '1.5rem' }}>
      <h2 style={{ display: 'flex', alignItems: 'center', gap: '.75rem', fontSize: '1.15rem' }}>
        <Loader2 className="spin-slow" size={24} /> Видео принято. Ожидаем результат анализа
      </h2>
      <p style={{ marginTop: '.75rem' }}>{fileName}</p>
      <p className="font-mono" style={{ overflowWrap: 'anywhere', color: 'var(--text-muted)' }}>Задача: {jobId}</p>
      <p style={{ marginTop: '.5rem' }}><Clock size={14} /> Ожидание: {Math.floor(seconds / 60)}:{String(seconds % 60).padStart(2, '0')}</p>
    </div>
  )
}
