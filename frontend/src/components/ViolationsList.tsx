import React from 'react'
import { AlertOctagon, CheckCircle2, Clock, ShieldAlert, Image as ImageIcon, ChevronRight } from 'lucide-react'
import type { Violation, VerdictType } from '../types/api'

interface ViolationsListProps {
  verdict: VerdictType
  violations: Violation[]
  onSelectTimecode: (timecode: number) => void
  onInspectEvidence: (violation: Violation) => void
}

export const ViolationsList: React.FC<ViolationsListProps> = ({
  verdict,
  violations,
  onSelectTimecode,
  onInspectEvidence,
}) => {
  const isViolation = verdict === 'violation'

  return (
    <div className="glass-panel" style={{ padding: '1.5rem' }}>
      {/* Verdict Header Card */}
      <div style={{
        padding: '1.25rem 1.5rem',
        borderRadius: 'var(--radius-md)',
        background: isViolation
          ? 'linear-gradient(135deg, rgba(244, 63, 94, 0.15), rgba(15, 23, 42, 0.9))'
          : 'linear-gradient(135deg, rgba(16, 185, 129, 0.15), rgba(15, 23, 42, 0.9))',
        border: `1px solid ${isViolation ? 'var(--border-danger)' : 'rgba(16, 185, 129, 0.4)'}`,
        boxShadow: isViolation ? 'var(--shadow-glow-rose)' : '0 0 25px rgba(16, 185, 129, 0.25)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        flexWrap: 'wrap',
        gap: '1rem',
        marginBottom: '1.5rem',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
          <div style={{
            width: '48px',
            height: '48px',
            borderRadius: '12px',
            background: isViolation ? 'rgba(244, 63, 94, 0.2)' : 'rgba(16, 185, 129, 0.2)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: isViolation ? 'var(--accent-rose)' : 'var(--accent-emerald)',
          }}>
            {isViolation ? <AlertOctagon size={28} /> : <CheckCircle2 size={28} />}
          </div>
          <div>
            <div style={{
              fontSize: '1.15rem',
              fontWeight: 800,
              letterSpacing: '-0.01em',
              color: isViolation ? '#fda4af' : '#a7f3d0',
            }}>
              {isViolation
                ? `ОБНАРУЖЕНЫ ПРИЗНАКИ СОБЫТИЙ (${violations.length})`
                : 'НАРУШЕНИЙ НЕ ВЫЯВЛЕНО'}
            </div>
            <p style={{ fontSize: '0.82rem', color: 'var(--text-muted)' }}>
              {isViolation
                ? 'Проверьте события, описание и доказательные кадры'
                : 'По результатам анализа признаки проверяемых событий не обнаружены'}
            </p>
          </div>
        </div>

        <div className="badge font-mono" style={{
          background: isViolation ? 'rgba(244, 63, 94, 0.2)' : 'rgba(16, 185, 129, 0.2)',
          color: isViolation ? '#ffffff' : '#ffffff',
          padding: '0.4rem 0.8rem',
          fontSize: '0.8rem',
        }}>
          СТАТУС: {isViolation ? 'РЕВИЗИЯ ТРЕБУЕТСЯ' : 'УСПЕШНО'}
        </div>
      </div>

      {/* Violations List */}
      {isViolation && violations.length > 0 ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          {violations.map((violation, index) => {
            const confidencePercent = typeof violation.confidence === 'number'
              ? Math.round(violation.confidence * 100)
              : null

            return (
              <div
                key={index}
                style={{
                  background: 'rgba(12, 19, 34, 0.7)',
                  border: '1px solid var(--border-subtle)',
                  borderRadius: 'var(--radius-md)',
                  padding: '1.25rem',
                  transition: 'all 0.2s ease',
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.borderColor = 'rgba(244, 63, 94, 0.5)'
                  e.currentTarget.style.transform = 'translateY(-2px)'
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.borderColor = 'var(--border-subtle)'
                  e.currentTarget.style.transform = 'translateY(0)'
                }}
              >
                {/* Header row */}
                <div style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  flexWrap: 'wrap',
                  gap: '0.75rem',
                  marginBottom: '0.75rem',
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
                    <span className="badge badge-rose font-mono">
                      {violation.rule_id || `ИНЦИДЕНТ #${index + 1}`}
                    </span>
                    <h4 style={{ fontSize: '1rem', fontWeight: 700, color: '#ffffff' }}>
                      {violation.type}
                    </h4>
                  </div>

                  {/* Jump to timecode button */}
                  <button
                    onClick={() => onSelectTimecode(violation.timecode_sec)}
                    className="btn-secondary"
                    style={{
                      padding: '0.35rem 0.75rem',
                      fontSize: '0.78rem',
                      color: 'var(--accent-cyan)',
                      borderColor: 'rgba(6, 182, 212, 0.3)',
                    }}
                    title="Перейти к моменту в видео"
                  >
                    <Clock size={14} />
                    <span className="font-mono">
                      {String(Math.floor(violation.timecode_sec / 60)).padStart(2, '0')}:
                      {String(Math.floor(violation.timecode_sec % 60)).padStart(2, '0')}s
                    </span>
                    <ChevronRight size={14} />
                  </button>
                </div>

                {/* Description */}
                <p style={{
                  fontSize: '0.85rem',
                  color: 'var(--text-main)',
                  lineHeight: 1.5,
                  marginBottom: '1rem',
                  background: 'rgba(0,0,0,0.2)',
                  padding: '0.75rem 1rem',
                  borderRadius: 'var(--radius-sm)',
                  borderLeft: '3px solid var(--accent-rose)',
                }}>
                  {violation.description}
                </p>

                {/* Metadata & Legal Clause */}
                <div style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  flexWrap: 'wrap',
                  gap: '0.75rem',
                  paddingTop: '0.75rem',
                  borderTop: '1px solid rgba(255, 255, 255, 0.05)',
                  fontSize: '0.78rem',
                }}>
                  {/* Legal ground */}
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', color: 'var(--text-muted)' }}>
                    <ShieldAlert size={14} color="var(--accent-amber)" />
                    <span>
                      Основание: <strong style={{ color: '#e2e8f0' }}>{violation.source || 'см. описание события'}</strong>
                      {violation.clause && ` (${violation.clause})`}
                    </span>
                  </div>

                  {/* Confidence Bar */}
                  {confidencePercent !== null && (<div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                    <span style={{ color: 'var(--text-muted)' }}>AI Confidence:</span>
                    <div style={{
                      width: '60px',
                      height: '6px',
                      borderRadius: '3px',
                      backgroundColor: 'rgba(255, 255, 255, 0.1)',
                      overflow: 'hidden',
                    }}>
                      <div style={{
                        width: `${confidencePercent}%`,
                        height: '100%',
                        backgroundColor: confidencePercent > 90 ? 'var(--accent-emerald)' : 'var(--accent-amber)',
                      }} />
                    </div>
                    <span className="font-mono" style={{ fontWeight: 600, color: '#ffffff' }}>
                      {confidencePercent}%
                    </span>
                  </div>)}
                </div>

                {/* Evidence thumbnails */}
                {violation.evidence_frames && violation.evidence_frames.length > 0 && (
                  <div style={{ marginTop: '0.9rem' }}>
                    <div style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      marginBottom: '0.5rem',
                    }}>
                      <span style={{ fontSize: '0.75rem', fontWeight: 600, color: 'var(--text-muted)' }}>
                        ДОКАЗАТЕЛЬНАЯ БАЗА (КАДРЫ С ДЕТЕКЦИЕЙ):
                      </span>
                      <button
                        onClick={() => onInspectEvidence(violation)}
                        style={{
                          fontSize: '0.75rem',
                          color: 'var(--accent-cyan)',
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: '0.25rem',
                        }}
                      >
                        <ImageIcon size={13} />
                        Открыть в полном размере
                      </button>
                    </div>

                    <div style={{ display: 'flex', gap: '0.75rem', overflowX: 'auto', paddingBottom: '0.25rem' }}>
                      {violation.evidence_frames.map((frame, fIdx) => (
                        <div
                          key={fIdx}
                          onClick={() => onInspectEvidence(violation)}
                          style={{
                            position: 'relative',
                            width: '140px',
                            height: '80px',
                            borderRadius: '6px',
                            overflow: 'hidden',
                            border: '1px solid rgba(255, 255, 255, 0.15)',
                            cursor: 'pointer',
                            flexShrink: 0,
                          }}
                        >
                          <img
                            src={frame.image_base64.startsWith('data:') ? frame.image_base64 : `data:image/jpeg;base64,${frame.image_base64}`}
                            alt={`Evidence frame ${fIdx + 1}`}
                            style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                          />
                          <span style={{
                            position: 'absolute',
                            bottom: 2,
                            right: 4,
                            background: 'rgba(0,0,0,0.8)',
                            padding: '1px 4px',
                            borderRadius: '3px',
                            fontSize: '0.65rem',
                            fontFamily: 'var(--font-mono)',
                            color: '#ffffff',
                          }}>
                            {frame.timestamp_sec.toFixed(1)}s
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      ) : isViolation ? (
        <div style={{ textAlign: 'center', padding: '2rem', color: 'var(--text-muted)' }}>
          Список нарушений пуст
        </div>
      ) : (
        /* Clean Session Details */
        <div style={{
          padding: '1.5rem',
          borderRadius: 'var(--radius-md)',
          background: 'rgba(16, 185, 129, 0.05)',
          border: '1px solid rgba(16, 185, 129, 0.2)',
          textAlign: 'center',
        }}>
          <CheckCircle2 size={36} color="var(--accent-emerald)" style={{ marginBottom: '0.75rem' }} />
          <h4 style={{ fontSize: '1rem', fontWeight: 700, color: '#ffffff', marginBottom: '0.35rem' }}>
            Кассовая дисциплина полностью соответствует нормативам
          </h4>
          <p style={{ fontSize: '0.82rem', color: 'var(--text-muted)', maxWidth: 480, margin: '0 auto' }}>
            При анализе видеопотока не обнаружено фактов невыдачи чеков, оставления кассового узла без присмотра или присутствия посторонних лиц.
          </p>
        </div>
      )}
    </div>
  )
}
