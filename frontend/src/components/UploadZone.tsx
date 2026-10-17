import { useId, useRef, useState } from 'react'
import type { DragEvent } from 'react'
import { UploadCloud, Film, AlertCircle } from 'lucide-react'

interface UploadZoneProps { onFileSelected: (file: File) => void; isUploading: boolean }
const configuredMaxMb = Number(import.meta.env.VITE_MAX_UPLOAD_MB || 100)
const maxMb = Number.isFinite(configuredMaxMb) && configuredMaxMb >= 1 ? configuredMaxMb : 100

export function UploadZone({ onFileSelected, isUploading }: UploadZoneProps) {
  const [isDragging, setIsDragging] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const input = useRef<HTMLInputElement>(null)
  const inputId = useId()

  const processFile = (file: File) => {
    if (isUploading) return
    setError(null)
    if (!file.name.toLowerCase().endsWith('.mov')) { setError('Выберите файл с расширением .mov'); return }
    if (file.size === 0) { setError('Файл пуст'); return }
    if (file.size > maxMb * 1024 * 1024) { setError(`Размер файла превышает ${maxMb} МиБ`); return }
    onFileSelected(file)
  }
  const drop = (event: DragEvent) => {
    event.preventDefault()
    setIsDragging(false)
    if (isUploading) return
    if (event.dataTransfer.files.length !== 1) { setError('Загрузите один .mov файл за раз'); return }
    processFile(event.dataTransfer.files[0])
  }

  return (
    <section className="glass-panel" style={{ padding: '1.75rem' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: '1rem', flexWrap: 'wrap', marginBottom: '1rem' }}>
        <h2 style={{ fontSize: '1.15rem' }}>Загрузка видеозаписи кассовой зоны</h2>
        <span className="badge badge-cyan"><Film size={12} /> MOV до {maxMb} МиБ</span>
      </div>
      {error && <p role="alert" style={{ color: '#fda4af', marginBottom: '1rem' }}><AlertCircle size={16} /> {error}</p>}
      <label htmlFor={inputId} onDragOver={(event) => { event.preventDefault(); if (!isUploading) setIsDragging(true) }}
        onDragLeave={() => setIsDragging(false)} onDrop={drop}
        style={{ display: 'block', border: `2px dashed ${isDragging ? 'var(--accent-cyan)' : 'rgba(255,255,255,.15)'}`, borderRadius: 16, padding: '2.5rem 1rem', textAlign: 'center', cursor: isUploading ? 'not-allowed' : 'pointer' }}>
        <UploadCloud size={40} style={{ color: 'var(--accent-cyan)', marginBottom: '1rem' }} />
        <p>{isUploading ? 'Дождитесь завершения текущей задачи' : 'Перетащите .mov файл сюда или выберите его'}</p>
        <input id={inputId} ref={input} type="file" accept=".mov,video/quicktime" disabled={isUploading}
          style={{ display: 'block', maxWidth: '100%', margin: '1rem auto 0' }}
          onChange={(event) => { const file = event.target.files?.[0]; if (file) processFile(file); event.target.value = '' }} />
      </label>
    </section>
  )
}
