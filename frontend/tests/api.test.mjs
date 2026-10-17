import { test, afterEach } from 'node:test'
import assert from 'node:assert/strict'
import { ApiService, ApiError } from '../src/services/api.ts'

const originalFetch = globalThis.fetch
afterEach(() => { globalThis.fetch = originalFetch })
function reply(payload, status = 200) {
  return new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } })
}

test('upload uses file multipart, relative API path, and no browser API key', async () => {
  globalThis.fetch = async (path, options) => {
    assert.equal(path, '/api/v1/analyze')
    assert.equal(options.method, 'POST')
    assert.equal(options.body.get('file').name, 'sample.MOV')
    assert.equal(new Headers(options.headers).has('X-API-Key'), false)
    assert.equal(new Headers(options.headers).has('Content-Type'), false)
    return reply({ job_id: '123' }, 201)
  }
  assert.deepEqual(await ApiService.uploadVideo(new File(['video'], 'sample.MOV')), { job_id: '123' })
})
test('failed job retains structured code and message', async () => {
  globalThis.fetch = async () => reply({ job_id: '123', status: 'failed', error: { code: 'JOB_TIMEOUT', message: 'Job exceeded its time limit' } })
  assert.deepEqual((await ApiService.pollJob('123')).error, { code: 'JOB_TIMEOUT', message: 'Job exceeded its time limit' })
})
test('successful violation preserves timecodes and base64 evidence', async () => {
  const payload = { job_id: '123', status: 'done', verdict: 'violation', violations: [{
    type: 'event', timecode_sec: 1.25, description: 'description with source', evidence_frames: [{ timestamp_sec: 1.25, image_base64: '/9j/AA==' }],
  }] }
  globalThis.fetch = async () => reply(payload)
  assert.deepEqual(await ApiService.pollJob('123'), payload)
})
test('processing and clean verdict pass through unchanged', async () => {
  for (const payload of [{ job_id: '123', status: 'processing' }, { job_id: '123', status: 'done', verdict: 'no_violation', violations: [] }]) {
    globalThis.fetch = async () => reply(payload)
    assert.deepEqual(await ApiService.pollJob('123'), payload)
  }
})
test('QUEUE_FULL is a readable API error, not a completed job', async () => {
  globalThis.fetch = async () => reply({ detail: { code: 'QUEUE_FULL', message: 'Too many active jobs' } }, 503)
  await assert.rejects(ApiService.uploadVideo(new File(['video'], 'sample.mov')), (error) =>
    error instanceof ApiError && error.status === 503 && error.code === 'QUEUE_FULL' && error.message.includes('Очередь'))
})
test('plain and validation errors are readable', async () => {
  for (const detail of ['File is empty', [{ msg: 'Field required' }]]) {
    globalThis.fetch = async () => reply({ detail }, 422)
    await assert.rejects(ApiService.uploadVideo(new File(['video'], 'sample.mov')), /File is empty|Field required/)
  }
})
test('401 and 404 remain errors', async () => {
  for (const status of [401, 404]) {
    globalThis.fetch = async () => reply({ detail: 'Denied or missing' }, status)
    await assert.rejects(ApiService.pollJob('123'), (error) => error instanceof ApiError && error.status === status)
  }
})
test('inconsistent or wrong-job result is rejected', async () => {
  for (const payload of [{ job_id: 'other', status: 'processing' }, { job_id: '123', status: 'done', verdict: 'violation', violations: [] }, { job_id: '123', status: 'failed', error: 'legacy string' }]) {
    globalThis.fetch = async () => reply(payload)
    await assert.rejects(ApiService.pollJob('123'))
  }
})
test('HTML or unavailable proxy is not accepted as a result', async () => {
  globalThis.fetch = async () => new Response('<html>wrong server</html>', { headers: { 'Content-Type': 'text/html' } })
  await assert.rejects(ApiService.pollJob('123'))
  globalThis.fetch = async () => { throw new TypeError('offline') }
  assert.equal((await ApiService.checkHealth()).ok, false)
})
test('request cancellation propagates without manufacturing a failed job', async () => {
  const controller = new AbortController()
  controller.abort()
  globalThis.fetch = async (_path, options) => { options.signal.throwIfAborted() }
  await assert.rejects(ApiService.pollJob('123', controller.signal), { name: 'AbortError' })
})
