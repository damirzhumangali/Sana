import React from 'react'
import { X, BookOpen, ExternalLink, CheckCircle2, AlertTriangle } from 'lucide-react'
import { RULES_CATALOG } from '../data/rulesCatalog'

interface RulesDrawerProps {
  isOpen: boolean
  onClose: () => void
}

export const RulesDrawer: React.FC<RulesDrawerProps> = ({ isOpen, onClose }) => {
  if (!isOpen) return null

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        backgroundColor: 'rgba(7, 11, 20, 0.75)',
        backdropFilter: 'blur(8px)',
        zIndex: 1100,
        display: 'flex',
        justifyContent: 'flex-end',
      }}
      onClick={onClose}
    >
      <div
        className="glass-panel"
        style={{
          width: '100%',
          maxWidth: '640px',
          height: '100%',
          borderRadius: 0,
          borderLeft: '1px solid var(--border-bright)',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
          boxShadow: '-10px 0 30px rgba(0, 0, 0, 0.8)',
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
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
            <div style={{
              width: 38,
              height: 38,
              borderRadius: '10px',
              background: 'rgba(6, 182, 212, 0.15)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'var(--accent-cyan)',
            }}>
              <BookOpen size={20} />
            </div>
            <div>
              <h3 style={{ fontSize: '1.15rem', fontWeight: 700, color: '#ffffff' }}>
                Каталог норм и правил
              </h3>
              <p style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>
                Юридическая база и алгоритмические критерии KassaCV
              </p>
            </div>
          </div>

          <button onClick={onClose} className="btn-secondary" style={{ padding: '0.4rem 0.6rem' }}>
            <X size={18} />
          </button>
        </div>

        {/* Content list */}
        <div style={{
          flex: 1,
          overflowY: 'auto',
          padding: '1.5rem',
          display: 'flex',
          flexDirection: 'column',
          gap: '1.25rem',
        }}>
          {RULES_CATALOG.map((rule) => (
            <div
              key={rule.rule_id}
              style={{
                background: 'rgba(12, 19, 34, 0.8)',
                border: '1px solid var(--border-subtle)',
                borderRadius: 'var(--radius-md)',
                padding: '1.25rem',
              }}
            >
              {/* Rule title & ID */}
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.75rem' }}>
                <span className="badge badge-cyan font-mono" style={{ fontSize: '0.8rem', padding: '0.2rem 0.6rem' }}>
                  {rule.rule_id}
                </span>
                <span style={{ fontSize: '0.75rem', color: 'var(--text-dim)' }}>
                  Верификация: {rule.verified_by} ({rule.verified_at})
                </span>
              </div>

              <h4 style={{ fontSize: '1.02rem', fontWeight: 700, color: '#ffffff', marginBottom: '0.75rem' }}>
                {rule.type}
              </h4>

              {/* Visual signs */}
              <div style={{ marginBottom: '0.75rem' }}>
                <div style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.35rem',
                  fontSize: '0.78rem',
                  fontWeight: 600,
                  color: 'var(--accent-rose)',
                  marginBottom: '0.25rem',
                }}>
                  <AlertTriangle size={14} />
                  ВИЗУАЛЬНЫЕ ПРИЗНАКИ НАРУШЕНИЯ:
                </div>
                <p style={{ fontSize: '0.82rem', color: 'var(--text-main)', lineHeight: 1.45, background: 'rgba(244, 63, 94, 0.05)', padding: '0.5rem 0.75rem', borderRadius: '6px' }}>
                  {rule.visual_signs}
                </p>
              </div>

              {/* What is NOT a violation */}
              <div style={{ marginBottom: '0.75rem' }}>
                <div style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.35rem',
                  fontSize: '0.78rem',
                  fontWeight: 600,
                  color: 'var(--accent-emerald)',
                  marginBottom: '0.25rem',
                }}>
                  <CheckCircle2 size={14} />
                  НЕ ЯВЛЯЕТСЯ НАРУШЕНИЕМ (ИСКЛЮЧЕНИЯ):
                </div>
                <p style={{ fontSize: '0.82rem', color: 'var(--text-main)', lineHeight: 1.45, background: 'rgba(16, 185, 129, 0.05)', padding: '0.5rem 0.75rem', borderRadius: '6px' }}>
                  {rule.not_a_violation}
                </p>
              </div>

              {/* Legal Reference & External link */}
              <div style={{
                marginTop: '1rem',
                paddingTop: '0.75rem',
                borderTop: '1px solid rgba(255, 255, 255, 0.06)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                flexWrap: 'wrap',
                gap: '0.5rem',
                fontSize: '0.78rem',
              }}>
                <div style={{ color: 'var(--text-muted)' }}>
                  Нормативный акт: <strong style={{ color: '#ffffff' }}>{rule.source}</strong> ({rule.clause})
                </div>

                <a
                  href={rule.source_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: '0.3rem',
                    color: 'var(--accent-cyan)',
                    textDecoration: 'none',
                    fontWeight: 600,
                  }}
                >
                  <span>Текст закона</span>
                  <ExternalLink size={13} />
                </a>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
