import type { JobAccepted, JobResponse } from '../types/api'

export class ApiError extends Error {
  status: number
  code?: string
  constructor(message: string, status: number, code?: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

async function request(path: string, init: RequestInit = {}, timeoutMs = 15_000): Promise<unknown> {
  let response: Response
  try {
    response = await fetch(path, {
      ...init,
      cache: 'no-store',
      signal: init.signal
        ? AbortSignal.any([init.signal, AbortSignal.timeout(timeoutMs)])
        : AbortSignal.timeout(timeoutMs),
    })
  } catch (error) {
    if (init.signal?.aborted) throw error
    throw new Error('Не удалось получить ответ сервера. Проверьте соединение и повторите запрос.')
  }
  const payload: unknown = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = isRecord(payload) ? payload.detail : null
    const code = isRecord(detail) && typeof detail.code === 'string' ? detail.code : undefined
    let message = `Ошибка сервера (${response.status})`
    if (typeof detail === 'string') message = detail
    else if (isRecord(detail) && typeof detail.message === 'string') message = detail.message
    else if (Array.isArray(detail)) {
      message = detail.map((item: unknown) => isRecord(item) ? String(item.msg || '') : '').filter(Boolean).join('; ') || message
    }
    if (response.status === 401) message = 'Нет доступа к API. Проверьте настройки подключения сервера.'
    if (code === 'QUEUE_FULL') message = 'Очередь заполнена. Попробуйте загрузить видео позже.'
    throw new ApiError(message, response.status, code)
  }
  if (!isRecord(payload)) throw new Error('Сервер вернул некорректный JSON-ответ')
  return payload
}

export class ApiService {
  static async checkHealth(): Promise<{ ok: boolean; message: string }> {
    try {
      const payload = await request('/health', {}, 3_000)
      if (!isRecord(payload) || payload.status !== 'ok') throw new Error('Неожиданный ответ проверки связи')
      return { ok: true, message: 'Сервер доступен' }
    } catch (error) {
      return { ok: false, message: error instanceof Error ? error.message : 'Сервер недоступен' }
    }
  }

  static async uploadVideo(file: File, signal?: AbortSignal): Promise<JobAccepted> {
    const formData = new FormData()
    formData.append('file', file)
    const payload = await request('/api/v1/analyze', { method: 'POST', body: formData, signal }, 330_000)
    if (!isRecord(payload) || typeof payload.job_id !== 'string') throw new Error('Сервер не вернул идентификатор задачи')
    return { job_id: payload.job_id }
  }

  static async pollJob(jobId: string, signal?: AbortSignal): Promise<JobResponse> {
    const payload = await request(`/api/v1/jobs/${encodeURIComponent(jobId)}`, { signal })
    if (!isRecord(payload) || payload.job_id !== jobId) throw new Error('Некорректный ответ статуса задачи')
    if (payload.status === 'processing') return payload as unknown as JobResponse
    if (payload.status === 'failed' && isRecord(payload.error)
      && typeof payload.error.code === 'string' && typeof payload.error.message === 'string') {
      return payload as unknown as JobResponse
    }
    if (payload.status === 'done' && Array.isArray(payload.violations)
      && ((payload.verdict === 'violation' && payload.violations.length > 0)
        || (payload.verdict === 'no_violation' && payload.violations.length === 0))) {
      return payload as unknown as JobResponse
    }
    throw new Error('Результат задачи не соответствует контракту API')
  }
}
