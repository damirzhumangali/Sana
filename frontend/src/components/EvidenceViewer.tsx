import React, { useState } from 'react'
import { X, ChevronLeft, ChevronRight, Download, ShieldAlert, Clock } from 'lucide-react'
import type { Violation } from '../types/api'

interface EvidenceViewerProps {
  violation: Violation | null
  onClose: () => void
}

export const EvidenceViewer: React.FC<EvidenceViewerProps> = ({ violation, onClose }) => {
  const [activeFrameIndex, setActiveFrameIndex] = useState(0)

  if (!violation || !violation.evidence_frames || violation.evidence_frames.length === 0) {
    return null
  }

  const frames = violation.evidence_frames
  const currentFrame = frames[activeFrameIndex] || frames[0]

  const handlePrev = () => {
    setActiveFrameIndex((prev) => (prev > 0 ? prev - 1 : frames.length - 1))
  }

  const handleNext = () => {
    setActiveFrameIndex((prev) => (prev < frames.length - 1 ? prev + 1 : 0))
  }

  const imageSrc = currentFrame.image_base64.startsWith('data:')
    ? currentFrame.image_base64
    : `data:image/jpeg;base64,${currentFrame.image_base64}`

  const downloadFrame = () => {
    const a = document.createElement('a')
    a.href = imageSrc
    a.download = `evidence_${violation.rule_id || 'incident'}_${currentFrame.timestamp_sec.toFixed(1)}s.jpg`
    a.click()
  }

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        backgroundColor: 'rgba(7, 11, 20, 0.88)',
        backdropFilter: 'blur(12px)',
        zIndex: 1000,
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
          maxWidth: '920px',
          maxHeight: '92vh',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
          border: '1px solid var(--border-bright)',
          boxShadow: '0 25px 50px -12px rgba(0, 0, 0, 0.8)',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div style={{
          padding: '1rem 1.5rem',
          borderBottom: '1px solid var(--border-subtle)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
            <span className="badge badge-rose font-mono">
              {violation.rule_id || 'INCIDENT'}
            </span>
            <h3 style={{ fontSize: '1.05rem', fontWeight: 700, color: '#ffffff' }}>
              Доказательная база: {violation.type}
            </h3>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <button
              onClick={downloadFrame}
              className="btn-secondary"
              style={{ padding: '0.4rem 0.75rem', fontSize: '0.8rem' }}
              title="Скачать кадр"
            >
              <Download size={14} />
              Сохранить
            </button>
            <button
              onClick={onClose}
              className="btn-secondary"
              style={{ padding: '0.4rem 0.6rem' }}
              title="Закрыть"
            >
              <X size={16} />
            </button>
          </div>
        </div>

        {/* Image Stage */}
        <div style={{
          position: 'relative',
          backgroundColor: '#05070d',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          minHeight: '380px',
          maxHeight: '520px',
          overflow: 'hidden',
        }}>
          <img
            src={imageSrc}
            alt="AI Evidence"
            style={{
              maxWidth: '100%',
              maxHeight: '500px',
              objectFit: 'contain',
              borderRadius: '4px',
            }}
          />

          {/* Navigation Arrows if multiple frames */}
          {frames.length > 1 && (
            <>
              <button
                onClick={handlePrev}
                style={{
                  position: 'absolute',
                  left: '1rem',
                  top: '50%',
                  transform: 'translateY(-50%)',
                  background: 'rgba(0, 0, 0, 0.65)',
                  border: '1px solid rgba(255, 255, 255, 0.2)',
                  borderRadius: '50%',
                  width: '40px',
                  height: '40px',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  color: '#ffffff',
                }}
              >
                <ChevronLeft size={22} />
              </button>

              <button
                onClick={handleNext}
                style={{
                  position: 'absolute',
                  right: '1rem',
                  top: '50%',
                  transform: 'translateY(-50%)',
                  background: 'rgba(0, 0, 0, 0.65)',
                  border: '1px solid rgba(255, 255, 255, 0.2)',
                  borderRadius: '50%',
                  width: '40px',
                  height: '40px',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  color: '#ffffff',
                }}
              >
                <ChevronRight size={22} />
              </button>
            </>
          )}

          {/* Frame Counter Badge */}
          <div style={{
            position: 'absolute',
            bottom: '12px',
            left: '14px',
            background: 'rgba(0,0,0,0.75)',
            padding: '0.25rem 0.6rem',
            borderRadius: '4px',
            fontSize: '0.75rem',
            fontFamily: 'var(--font-mono)',
            color: '#ffffff',
            border: '1px solid rgba(255,255,255,0.1)',
          }}>
            Кадр {activeFrameIndex + 1} из {frames.length} • {currentFrame.timestamp_sec.toFixed(2)}s
          </div>
        </div>

        {/* Footer info */}
        <div style={{
          padding: '1.25rem 1.5rem',
          borderTop: '1px solid var(--border-subtle)',
          backgroundColor: 'rgba(12, 19, 34, 0.6)',
        }}>
          <p style={{ fontSize: '0.86rem', color: '#f8fafc', lineHeight: 1.5, marginBottom: '0.5rem' }}>
            {violation.description}
          </p>

          <div style={{ display: 'flex', alignItems: 'center', gap: '1.5rem', fontSize: '0.78rem', color: 'var(--text-muted)' }}>
            <span style={{ display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
              <Clock size={14} color="var(--accent-cyan)" />
              Таймкод события: <strong style={{ color: '#ffffff' }}>{currentFrame.timestamp_sec.toFixed(2)} сек.</strong>
            </span>
            {violation.source && (
              <span style={{ display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
                <ShieldAlert size={14} color="var(--accent-amber)" />
                Основание: <strong style={{ color: '#ffffff' }}>{violation.source}</strong> {violation.clause && `(${violation.clause})`}
              </span>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
