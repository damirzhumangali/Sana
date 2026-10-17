import React from 'react'
import { History, FileVideo, AlertOctagon, CheckCircle2, Trash2 } from 'lucide-react'
import type { JobHistoryItem } from '../types/api'

interface HistoryListProps {
  history: JobHistoryItem[]
  activeJobId?: string
  onSelectJob: (item: JobHistoryItem) => void
  onClearHistory: () => void
}

export const HistoryList: React.FC<HistoryListProps> = ({
  history,
  activeJobId,
  onSelectJob,
  onClearHistory,
}) => {
  if (history.length === 0) {
    return null
  }

  return (
    <div className="glass-panel" style={{ padding: '1.25rem', marginTop: '1.5rem' }}>
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        marginBottom: '1rem',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <History size={18} color="var(--accent-cyan)" />
          <h3 style={{ fontSize: '0.95rem', fontWeight: 700, color: '#ffffff' }}>
            История проверок ({history.length})
          </h3>
        </div>

        <button
          onClick={onClearHistory}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '0.3rem',
            color: 'var(--text-dim)',
            fontSize: '0.75rem',
          }}
          onMouseEnter={(e) => (e.currentTarget.style.color = 'var(--accent-rose)')}
          onMouseLeave={(e) => (e.currentTarget.style.color = 'var(--text-dim)')}
        >
          <Trash2 size={13} />
          Очистить
        </button>
      </div>

      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))',
        gap: '0.75rem',
      }}>
        {history.map((item) => {
          const isActive = item.jobId === activeJobId
          const isViolation = item.verdict === 'violation'

          return (
            <div
              key={item.id}
              onClick={() => onSelectJob(item)}
              style={{
                padding: '0.85rem 1rem',
                borderRadius: 'var(--radius-md)',
                background: isActive ? 'rgba(6, 182, 212, 0.12)' : 'rgba(12, 19, 34, 0.6)',
                border: `1px solid ${isActive ? 'var(--accent-cyan)' : 'var(--border-subtle)'}`,
                cursor: 'pointer',
                transition: 'all 0.2s ease',
              }}
              onMouseEnter={(e) => {
                if (!isActive) e.currentTarget.style.borderColor = 'rgba(255, 255, 255, 0.2)'
              }}
              onMouseLeave={(e) => {
                if (!isActive) e.currentTarget.style.borderColor = 'var(--border-subtle)'
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.4rem' }}>
                <span className={`badge ${isViolation ? 'badge-rose' : 'badge-emerald'}`} style={{ fontSize: '0.68rem', padding: '0.15rem 0.5rem' }}>
                  {isViolation ? (
                    <>
                      <AlertOctagon size={11} />
                      {item.violationsCount} {item.violationsCount === 1 ? 'инцидент' : 'инцидента'}
                    </>
                  ) : (
                    <>
                      <CheckCircle2 size={11} />
                      Чисто
                    </>
                  )}
                </span>
                <span style={{ fontSize: '0.7rem', color: 'var(--text-dim)' }}>
                  {new Date(item.createdAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                </span>
              </div>

              <div style={{
                display: 'flex',
                alignItems: 'center',
                gap: '0.4rem',
                fontSize: '0.84rem',
                fontWeight: 600,
                color: '#ffffff',
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
              }}>
                <FileVideo size={14} color="var(--accent-cyan)" />
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{item.fileName}</span>
              </div>

              <div style={{
                fontSize: '0.72rem',
                color: 'var(--text-muted)',
                fontFamily: 'var(--font-mono)',
                marginTop: '0.35rem',
              }}>
                ID: {item.jobId.slice(0, 18)}...
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
