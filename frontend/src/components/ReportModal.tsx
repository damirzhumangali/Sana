import React, { useState } from 'react'
import { X, Printer, Download } from 'lucide-react'
import type { JobDone } from '../types/api'

interface ReportModalProps {
  jobData: JobDone | null
  fileName: string
  isOpen: boolean
  onClose: () => void
}

export const ReportModal: React.FC<ReportModalProps> = ({
  jobData,
  fileName,
  isOpen,
  onClose,
}) => {
  const [generatedAt] = useState(() => new Date().toLocaleString('ru-RU'))
  if (!isOpen || !jobData) return null

  const handlePrint = () => {
    window.print()
  }

  const handleExportJson = () => {
    const dataStr = 'data:text/json;charset=utf-8,' + encodeURIComponent(JSON.stringify(jobData, null, 2))
    const downloadAnchor = document.createElement('a')
    downloadAnchor.setAttribute('href', dataStr)
    downloadAnchor.setAttribute('download', `sana_audit_report_${jobData.job_id}.json`)
    document.body.appendChild(downloadAnchor)
    downloadAnchor.click()
    downloadAnchor.remove()
  }

  const isViolation = jobData.verdict === 'violation'

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        backgroundColor: 'rgba(7, 11, 20, 0.85)',
        backdropFilter: 'blur(10px)',
        zIndex: 1300,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '1.5rem',
      }}
      onClick={onClose}
    >
      <div
        className="glass-panel"
        style={{
          width: '100%',
          maxWidth: '820px',
          maxHeight: '90vh',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
          border: '1px solid var(--border-bright)',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div style={{
          padding: '1.25rem 1.75rem',
          borderBottom: '1px solid var(--border-subtle)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}>
          <div>
            <h3 style={{ fontSize: '1.15rem', fontWeight: 700, color: '#ffffff' }}>
              Результат визуального анализа
            </h3>
            <p style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>
              Сформировано системой SANA AI Computer Vision
            </p>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <button onClick={handlePrint} className="btn-secondary" style={{ padding: '0.45rem 0.85rem', fontSize: '0.8rem' }}>
              <Printer size={15} />
              Печать (PDF)
            </button>
            <button onClick={handleExportJson} className="btn-secondary" style={{ padding: '0.45rem 0.85rem', fontSize: '0.8rem' }}>
              <Download size={15} />
              JSON
            </button>
            <button onClick={onClose} className="btn-secondary" style={{ padding: '0.45rem 0.65rem' }}>
              <X size={16} />
            </button>
          </div>
        </div>

        {/* Printable Content Body */}
        <div style={{
          flex: 1,
          overflowY: 'auto',
          padding: '2rem',
          background: 'rgba(12, 19, 34, 0.95)',
          color: '#f8fafc',
          display: 'flex',
          flexDirection: 'column',
          gap: '1.5rem',
        }}>
          {/* Metadata Block */}
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
            gap: '1rem',
            padding: '1rem',
            borderRadius: 'var(--radius-md)',
            background: 'rgba(255, 255, 255, 0.03)',
            border: '1px solid var(--border-subtle)',
            fontSize: '0.82rem',
          }}>
            <div>
              <span style={{ color: 'var(--text-muted)' }}>Идентификатор задачи:</span>
              <div className="font-mono" style={{ fontWeight: 600, color: 'var(--accent-cyan)' }}>{jobData.job_id}</div>
            </div>
            <div>
              <span style={{ color: 'var(--text-muted)' }}>Анализируемый файл:</span>
              <div style={{ fontWeight: 600 }}>{fileName}</div>
            </div>
            <div>
              <span style={{ color: 'var(--text-muted)' }}>Дата и время формирования:</span>
              <div>{generatedAt}</div>
            </div>
            <div>
              <span style={{ color: 'var(--text-muted)' }}>Итоговый вердикт:</span>
              <div style={{
                fontWeight: 700,
                color: isViolation ? 'var(--accent-rose)' : 'var(--accent-emerald)',
              }}>
                {isViolation ? 'НАРУШЕНИЯ ВЫЯВЛЕНЫ' : 'НАРУШЕНИЙ НЕ ВЫЯВЛЕНО'}
              </div>
            </div>
          </div>

          {/* Violations Table */}
          {isViolation && jobData.violations.length > 0 ? (
            <div>
              <h4 style={{ fontSize: '0.95rem', fontWeight: 700, marginBottom: '0.75rem', color: '#ffffff' }}>
                Перечень зафиксированных инцидентов ({jobData.violations.length})
              </h4>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                {jobData.violations.map((v, i) => (
                  <div
                    key={i}
                    style={{
                      padding: '1rem',
                      borderRadius: 'var(--radius-sm)',
                      background: 'rgba(244, 63, 94, 0.05)',
                      border: '1px solid rgba(244, 63, 94, 0.2)',
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.35rem' }}>
                      <span className="badge badge-rose font-mono">{v.rule_id || `ИНЦИДЕНТ #${i + 1}`}</span>
                      <span className="font-mono" style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>
                        Таймкод: {v.timecode_sec} сек.
                      </span>
                    </div>
                    <div style={{ fontSize: '0.92rem', fontWeight: 600, color: '#ffffff', marginBottom: '0.35rem' }}>
                      {v.type}
                    </div>
                    <p style={{ fontSize: '0.82rem', color: 'var(--text-muted)', lineHeight: 1.45, marginBottom: '0.5rem' }}>
                      {v.description}
                    </p>
                    <div style={{ fontSize: '0.75rem', color: 'var(--accent-amber)' }}>
                      Основание: {v.source || 'см. описание события'} {v.clause ? `(${v.clause})` : ''}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--accent-emerald)' }}>
              По результатам анализа признаки проверяемых событий не обнаружены.
            </div>
          )}

          {/* Signatures */}
          <div style={{
            marginTop: '2rem',
            paddingTop: '1.5rem',
            borderTop: '1px solid var(--border-subtle)',
            display: 'flex',
            justifyContent: 'space-between',
            fontSize: '0.8rem',
            color: 'var(--text-dim)',
          }}>
            <div>
              События требуют проверки человеком.
            </div>
            <div>
              SANA — результат визуального анализа
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
