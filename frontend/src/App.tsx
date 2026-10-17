import { useEffect, useRef, useState } from 'react'
import { Header } from './components/Header'
import { UploadZone } from './components/UploadZone'
import { VideoPlayer } from './components/VideoPlayer'
import { ViolationsList } from './components/ViolationsList'
import { JobStatusTracker } from './components/JobStatusTracker'
import { EvidenceViewer } from './components/EvidenceViewer'
import { SettingsModal } from './components/SettingsModal'
import { ReportModal } from './components/ReportModal'
import { HistoryList } from './components/HistoryList'
import { ApiService } from './services/api'
import type { JobDone, JobHistoryItem, Violation, JobStatus } from './types/api'

function pause(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    signal.throwIfAborted()
    const onAbort = () => { clearTimeout(timer); reject(signal.reason) }
    const timer = setTimeout(() => { signal.removeEventListener('abort', onAbort); resolve() }, ms)
    signal.addEventListener('abort', onAbort, { once: true })
  })
}

export function App() {
  // Keep video evidence in this tab only; backend retention governs server copies.
  const [history, setHistory] = useState<JobHistoryItem[]>([])
  const [activeJobId, setActiveJobId] = useState('')
  const [currentJobData, setCurrentJobData] = useState<JobDone | null>(null)
  const [currentFileName, setCurrentFileName] = useState('')
  const [currentVideoUrl, setCurrentVideoUrl] = useState<string>()
  const [jobStatus, setJobStatus] = useState<JobStatus>('idle')
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [selectedTimecode, setSelectedTimecode] = useState<number | null>(null)
  const [inspectingViolation, setInspectingViolation] = useState<Violation | null>(null)
  const [isSettingsOpen, setIsSettingsOpen] = useState(false)
  const [isReportOpen, setIsReportOpen] = useState(false)
  const work = useRef<{ controller: AbortController | null; urls: Set<string> }>({ controller: null, urls: new Set() })

  useEffect(() => {
    const state = work.current
    return () => { state.controller?.abort(); state.urls.forEach((url) => URL.revokeObjectURL(url)); state.urls.clear() }
  }, [])

  const handleFileSelected = async (file: File) => {
    work.current.controller?.abort()
    const controller = new AbortController()
    work.current.controller = controller
    const videoUrl = URL.createObjectURL(file)
    work.current.urls.add(videoUrl)
    setCurrentVideoUrl(videoUrl)
    setCurrentFileName(file.name)
    setCurrentJobData(null)
    setInspectingViolation(null)
    setSelectedTimecode(null)
    setActiveJobId('')
    setErrorMessage(null)
    setJobStatus('uploading')

    try {
      const { job_id } = await ApiService.uploadVideo(file, controller.signal)
      controller.signal.throwIfAborted()
      setActiveJobId(job_id)
      setJobStatus('processing')
      const deadline = Date.now() + 330_000
      while (Date.now() < deadline) {
        const response = await ApiService.pollJob(job_id, controller.signal)
        controller.signal.throwIfAborted()
        if (response.status === 'done') {
          setCurrentJobData(response)
          setJobStatus('done')
          setHistory((previous) => [{
            id: job_id, jobId: job_id, createdAt: Date.now(), fileName: file.name,
            fileSizeBytes: file.size, videoUrl, status: 'done', verdict: response.verdict,
            violationsCount: response.violations.length, jobData: response,
          }, ...previous])
          return
        }
        if (response.status === 'failed') {
          setErrorMessage(`${response.error.message} (${response.error.code})`)
          setJobStatus('failed')
          return
        }
        await pause(1500, controller.signal)
      }
      throw new Error('Время ожидания ответа истекло. Сохраните идентификатор задачи для повторной проверки.')
    } catch (error) {
      if (controller.signal.aborted) return
      setErrorMessage(error instanceof Error ? error.message : 'Ошибка обработки запроса')
      setJobStatus('failed')
    } finally {
      if (work.current.controller === controller) work.current.controller = null
    }
  }

  const handleSelectHistoryItem = (item: JobHistoryItem) => {
    if (jobStatus === 'uploading' || jobStatus === 'processing') return
    setActiveJobId(item.jobId)
    setCurrentFileName(item.fileName)
    setCurrentVideoUrl(item.videoUrl)
    setCurrentJobData(item.jobData || null)
    setJobStatus(item.status)
    setErrorMessage(null)
    setSelectedTimecode(null)
    setInspectingViolation(null)
  }

  const handleClearHistory = () => {
    setHistory([])
    for (const url of work.current.urls) {
      if (url !== currentVideoUrl) { URL.revokeObjectURL(url); work.current.urls.delete(url) }
    }
  }

  return (
    <div className="app-container">
      <Header onOpenSettings={() => setIsSettingsOpen(true)} onOpenReport={() => setIsReportOpen(true)} history={history} canExport={currentJobData !== null} />
      {errorMessage && (
        <div role="alert" style={{ padding: '1rem 1.25rem', color: '#fda4af', border: '1px solid rgba(244,63,94,.4)', borderRadius: 12, marginBottom: '1rem' }}>
          <strong>Ошибка: </strong>{errorMessage}
          {activeJobId && <div className="font-mono" style={{ marginTop: '.5rem' }}>Задача: {activeJobId}</div>}
        </div>
      )}
      {jobStatus === 'uploading' && <p role="status" style={{ marginBottom: '1rem' }}>Загрузка файла на сервер...</p>}
      {jobStatus === 'processing' && <JobStatusTracker key={activeJobId} jobId={activeJobId} fileName={currentFileName} />}
      <div className="workspace-grid">
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
          <VideoPlayer key={currentVideoUrl || 'empty'} videoUrl={currentVideoUrl} violations={currentJobData?.violations || []} selectedTimecode={selectedTimecode}
            onSeekViolation={setSelectedTimecode} onInspectEvidence={setInspectingViolation} />
          <UploadZone onFileSelected={handleFileSelected} isUploading={jobStatus === 'uploading' || jobStatus === 'processing'} />
        </div>
        {currentJobData ? (
          <ViolationsList verdict={currentJobData.verdict} violations={currentJobData.violations} onSelectTimecode={setSelectedTimecode} onInspectEvidence={setInspectingViolation} />
        ) : (
          <div className="glass-panel" style={{ padding: '3rem 2rem', textAlign: 'center', color: 'var(--text-muted)' }}>
            {jobStatus === 'failed' ? 'Анализ не завершён. Вердикт отсутствует.' : 'Загрузите видео. Здесь появятся результат анализа и доказательные кадры.'}
          </div>
        )}
      </div>
      <HistoryList history={history} activeJobId={activeJobId} onSelectJob={handleSelectHistoryItem} onClearHistory={handleClearHistory} />
      <EvidenceViewer key={inspectingViolation?.timecode_sec ?? 'empty'} violation={inspectingViolation} onClose={() => setInspectingViolation(null)} />
      <SettingsModal isOpen={isSettingsOpen} onClose={() => setIsSettingsOpen(false)} />
      <ReportModal key={currentJobData?.job_id || 'empty'} jobData={currentJobData} fileName={currentFileName} isOpen={isReportOpen} onClose={() => setIsReportOpen(false)} />
    </div>
  )
}
export default App
