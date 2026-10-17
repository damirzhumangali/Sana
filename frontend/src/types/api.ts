export interface EvidenceFrame {
  timestamp_sec: number
  image_base64: string
}

export interface Violation {
  type: string
  timecode_sec: number
  description: string
  evidence_frames: EvidenceFrame[]
  rule_id?: string | null
  source?: string | null
  clause?: string | null
  confidence?: number | null
}

export type JobStatus = 'idle' | 'uploading' | 'processing' | 'done' | 'failed'
export type VerdictType = 'violation' | 'no_violation'

export interface JobAccepted {
  job_id: string
}

export interface JobProcessing {
  job_id: string
  status: 'processing'
}

export interface JobFailed {
  job_id: string
  status: 'failed'
  error: { code: string; message: string }
}

export interface JobDone {
  job_id: string
  status: 'done'
  verdict: VerdictType
  violations: Violation[]
}

export type JobResponse = JobProcessing | JobFailed | JobDone

export interface RuleItem {
  rule_id: string
  type: string
  visual_signs: string
  not_a_violation: string
  source: string
  clause: string
  source_url: string
  interpretation: string
  verified_by: string
  verified_at: string
}

export interface JobHistoryItem {
  id: string
  jobId: string
  createdAt: number
  fileName: string
  fileSizeBytes: number
  videoUrl?: string
  status: JobStatus
  verdict?: VerdictType
  violationsCount: number
  jobData?: JobDone
  error?: string
}

export interface PipelineStage {
  id: string
  label: string
  description: string
  durationEstimateSec: number
}
