import React, { useRef, useState, useEffect } from 'react'
import { Play, Pause, RotateCcw, FastForward, Maximize, AlertTriangle, Eye } from 'lucide-react'
import type { Violation } from '../types/api'

interface VideoPlayerProps {
  videoUrl?: string
  violations: Violation[]
  selectedTimecode?: number | null
  onSeekViolation?: (timecode: number) => void
  onInspectEvidence?: (violation: Violation) => void
}

export const VideoPlayer: React.FC<VideoPlayerProps> = ({
  videoUrl,
  violations,
  selectedTimecode,
  onSeekViolation,
  onInspectEvidence,
}) => {
  const videoRef = useRef<HTMLVideoElement>(null)
  const [isPlaying, setIsPlaying] = useState(false)
  const [videoError, setVideoError] = useState(false)
  const [currentTime, setCurrentTime] = useState(0)
  const [duration, setDuration] = useState(60) // default fallback 60s for simulated
  const [playbackRate, setPlaybackRate] = useState(1)

  // Jump to timecode when externally selected (e.g. from violations list)
  useEffect(() => {
    if (selectedTimecode !== undefined && selectedTimecode !== null && videoRef.current) {
      videoRef.current.currentTime = selectedTimecode
      setCurrentTime(selectedTimecode)
    }
  }, [selectedTimecode])

  const togglePlay = () => {
    if (!videoRef.current) return
    if (isPlaying) {
      videoRef.current.pause()
    } else {
      void videoRef.current.play().catch(() => { setVideoError(true); setIsPlaying(false) })
    }
    setIsPlaying(!isPlaying)
  }

  const handleTimeUpdate = () => {
    if (videoRef.current) {
      setCurrentTime(videoRef.current.currentTime)
    }
  }

  const handleLoadedMetadata = () => {
    if (videoRef.current) {
      setDuration(videoRef.current.duration || 60)
    }
  }

  const handleSeek = (newTime: number) => {
    if (videoRef.current) {
      videoRef.current.currentTime = newTime
      setCurrentTime(newTime)
    }
    onSeekViolation?.(newTime)
  }

  const changeSpeed = (rate: number) => {
    setPlaybackRate(rate)
    if (videoRef.current) {
      videoRef.current.playbackRate = rate
    }
  }

  const formatTime = (seconds: number) => {
    const mins = Math.floor(seconds / 60)
    const secs = Math.floor(seconds % 60)
    const ms = Math.floor((seconds % 1) * 100)
    return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}.${String(ms).padStart(2, '0')}`
  }

  // Check if current time has an active incident nearby (+- 1.5s)
  const activeIncident = violations.find(
    (v) => Math.abs(currentTime - v.timecode_sec) <= 2.0
  )

  return (
    <div className="glass-panel" style={{ padding: '1.25rem', overflow: 'hidden' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.75rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <span style={{ fontSize: '0.9rem', fontWeight: 700, color: '#ffffff' }}>
            Видеомонитор кассовой зоны
          </span>
          <span className="badge badge-cyan font-mono" style={{ fontSize: '0.7rem' }}>
            HUD СИНХРОНИЗАЦИЯ
          </span>
        </div>

        {activeIncident && (
          <div className="badge badge-rose animate-fade-in" style={{ gap: '0.5rem' }}>
            <AlertTriangle size={14} />
            <span>ФИКСАЦИЯ ИНЦИДЕНТА: {activeIncident.rule_id || 'НАРУШЕНИЕ'}</span>
            {onInspectEvidence && (
              <button
                onClick={() => onInspectEvidence(activeIncident)}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: '0.2rem',
                  color: '#ffffff',
                  textDecoration: 'underline',
                  fontSize: '0.72rem',
                  marginLeft: '0.25rem',
                }}
              >
                <Eye size={12} />
                Кадры
              </button>
            )}
          </div>
        )}
      </div>

      {videoError && <p role="status" style={{ color: 'var(--text-muted)', marginBottom: '.75rem' }}>Браузер не смог воспроизвести это видео. Файл можно отправить на анализ.</p>}
      {/* Screen Frame */}
      <div style={{
        position: 'relative',
        width: '100%',
        aspectRatio: '16 / 9',
        backgroundColor: '#090d16',
        borderRadius: 'var(--radius-md)',
        overflow: 'hidden',
        border: activeIncident ? '2px solid var(--accent-rose)' : '1px solid var(--border-subtle)',
        transition: 'border 0.3s ease',
      }}>
        {videoUrl ? (
          <video
            ref={videoRef}
            src={videoUrl}
            style={{ width: '100%', height: '100%', objectFit: 'contain' }}
            onTimeUpdate={handleTimeUpdate}
            onLoadedMetadata={handleLoadedMetadata}
            onEnded={() => setIsPlaying(false)}
            onError={() => setVideoError(true)}
          />
        ) : (
          /* Simulated Canvas View when using Mock Cases without local video file */
          <div style={{
            width: '100%',
            height: '100%',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            background: 'radial-gradient(circle at center, #111a2e 0%, #070b14 100%)',
            position: 'relative',
          }}>
            <div style={{
              position: 'absolute',
              inset: 0,
              backgroundImage: 'linear-gradient(rgba(6, 182, 212, 0.05) 1px, transparent 1px), linear-gradient(90deg, rgba(6, 182, 212, 0.05) 1px, transparent 1px)',
              backgroundSize: '30px 30px',
            }} />

            <div style={{ zIndex: 2, textAlign: 'center', padding: '1rem' }}>
              <div style={{
                width: 60,
                height: 60,
                borderRadius: '50%',
                background: 'rgba(6, 182, 212, 0.1)',
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                marginBottom: '0.75rem',
                color: 'var(--accent-cyan)',
              }}>
                <Play size={28} />
              </div>
              <h4 style={{ fontSize: '1rem', fontWeight: 600, color: '#f8fafc' }}>
                Демонстрационный видеопоток KassaCV
              </h4>
              <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)', maxWidth: 420 }}>
                Кликните по таймкоду любого нарушения ниже для перехода к точным кадрам фиксации
              </p>
            </div>

            {/* Simulated HUD elements */}
            <div style={{
              position: 'absolute',
              top: 14,
              left: 14,
              fontFamily: 'var(--font-mono)',
              fontSize: '0.75rem',
              color: 'var(--accent-cyan)',
              background: 'rgba(9, 13, 22, 0.8)',
              padding: '0.2rem 0.5rem',
              borderRadius: '4px',
            }}>
              CAM-04 // 1920x1080 @ 25fps // ROI: POS_ZONE
            </div>
          </div>
        )}

        {/* Video Overlay Watermark & Live Timecode */}
        <div style={{
          position: 'absolute',
          bottom: 12,
          right: 12,
          fontFamily: 'var(--font-mono)',
          fontSize: '0.75rem',
          color: '#ffffff',
          background: 'rgba(0, 0, 0, 0.75)',
          padding: '0.25rem 0.6rem',
          borderRadius: '4px',
          border: '1px solid rgba(255, 255, 255, 0.1)',
          pointerEvents: 'none',
        }}>
          REC • {formatTime(currentTime)}
        </div>
      </div>

      {/* Timeline with Violation Markers */}
      <div style={{ marginTop: '0.9rem', position: 'relative' }}>
        {/* Scrubber track */}
        <div
          onClick={(e) => {
            const rect = e.currentTarget.getBoundingClientRect()
            const ratio = (e.clientX - rect.left) / rect.width
            handleSeek(ratio * duration)
          }}
          style={{
            position: 'relative',
            height: '10px',
            borderRadius: '5px',
            backgroundColor: 'rgba(255, 255, 255, 0.1)',
            cursor: 'pointer',
          }}
        >
          {/* Progress fill */}
          <div style={{
            position: 'absolute',
            top: 0,
            left: 0,
            height: '100%',
            width: `${(currentTime / duration) * 100}%`,
            background: 'linear-gradient(90deg, #06b6d4, #3b82f6)',
            borderRadius: '5px',
          }} />

          {/* Incident markers */}
          {violations.map((violation, idx) => {
            const markerPos = Math.min(Math.max((violation.timecode_sec / duration) * 100, 1), 99)
            return (
              <div
                key={idx}
                onClick={(e) => {
                  e.stopPropagation()
                  handleSeek(violation.timecode_sec)
                }}
                title={`${violation.rule_id || 'Инцидент'}: ${violation.type} (${violation.timecode_sec}s)`}
                style={{
                  position: 'absolute',
                  top: '-4px',
                  left: `${markerPos}%`,
                  transform: 'translateX(-50%)',
                  width: '18px',
                  height: '18px',
                  borderRadius: '50%',
                  background: 'var(--accent-rose)',
                  border: '2px solid #ffffff',
                  boxShadow: 'var(--shadow-glow-rose)',
                  cursor: 'pointer',
                  zIndex: 4,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                <div style={{ width: '4px', height: '4px', borderRadius: '50%', background: '#ffffff' }} />
              </div>
            )
          })}
        </div>
      </div>

      {/* Controls Bar */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        marginTop: '0.9rem',
        flexWrap: 'wrap',
        gap: '0.5rem',
      }}>
        {/* Left: Playback controls */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <button
            onClick={togglePlay}
            className="btn-primary"
            style={{ padding: '0.45rem 0.9rem', borderRadius: '8px' }}
          >
            {isPlaying ? <Pause size={16} /> : <Play size={16} />}
            <span>{isPlaying ? 'Пауза' : 'Воспроизведение'}</span>
          </button>

          <button
            onClick={() => handleSeek(Math.max(0, currentTime - 2))}
            className="btn-secondary"
            style={{ padding: '0.45rem 0.65rem' }}
            title="-2 секунды"
          >
            <RotateCcw size={15} />
          </button>

          <button
            onClick={() => handleSeek(Math.min(duration, currentTime + 2))}
            className="btn-secondary"
            style={{ padding: '0.45rem 0.65rem' }}
            title="+2 секунды"
          >
            <FastForward size={15} />
          </button>

          <span className="font-mono" style={{ fontSize: '0.85rem', color: 'var(--text-muted)', marginLeft: '0.5rem' }}>
            <strong style={{ color: '#ffffff' }}>{formatTime(currentTime)}</strong> / {formatTime(duration)}
          </span>
        </div>

        {/* Right: Speed & Fullscreen */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
          {[0.5, 1, 1.5, 2].map((speed) => (
            <button
              key={speed}
              onClick={() => changeSpeed(speed)}
              style={{
                fontSize: '0.75rem',
                padding: '0.25rem 0.55rem',
                borderRadius: '6px',
                background: playbackRate === speed ? 'rgba(6, 182, 212, 0.2)' : 'rgba(255, 255, 255, 0.05)',
                color: playbackRate === speed ? 'var(--accent-cyan)' : 'var(--text-muted)',
                border: `1px solid ${playbackRate === speed ? 'var(--accent-cyan)' : 'transparent'}`,
              }}
            >
              {speed}x
            </button>
          ))}

          <button
            onClick={() => {
              if (videoRef.current?.requestFullscreen) {
                videoRef.current.requestFullscreen()
              }
            }}
            className="btn-secondary"
            style={{ padding: '0.35rem 0.6rem', marginLeft: '0.3rem' }}
            title="Во весь экран"
          >
            <Maximize size={15} />
          </button>
        </div>
      </div>
    </div>
  )
}
