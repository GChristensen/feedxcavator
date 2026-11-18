// The console API client. Mutating requests carry the CSRF token.

const BASE = '/front/api'

function csrfToken() {
  const match = document.cookie.match(/(?:^|;\s*)feedxcavator_csrftoken=([^;]+)/)
  return match ? decodeURIComponent(match[1]) : ''
}

export class ApiError extends Error {
  constructor(status, body) {
    super((body && body.error) || `HTTP ${status}`)
    this.status = status
    this.body = body || {}
  }
}

async function request(method, path, { query, body, form } = {}) {
  // the static demo (npm run build:demo) answers from recorded fixtures; the normal build drops this branch
  if (import.meta.env.VITE_DEMO) return (await import('./demo/mock.js')).handle(method, path, { query, body, form })
  let url = BASE + path
  if (query) {
    const params = new URLSearchParams()
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== null) params.set(key, value)
    }
    if ([...params].length) url += '?' + params
  }
  const headers = {}
  const options = { method, headers, credentials: 'same-origin' }
  if (method !== 'GET') headers['X-CSRFToken'] = csrfToken()
  if (form) {
    options.body = form
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    options.body = JSON.stringify(body)
  }
  const response = await fetch(url, options)
  if (response.status === 401) {
    window.location.href = '/login?next=' + encodeURIComponent(window.location.pathname + window.location.hash)
    throw new ApiError(401, { error: 'authentication required' })
  }
  const text = await response.text()
  let data = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = { error: text.slice(0, 2000) }
  }
  if (!response.ok) throw new ApiError(response.status, data)
  return data
}

export const api = {
  get: (path, query) => request('GET', path, { query }),
  post: (path, body, query) => request('POST', path, { body: body ?? {}, query }),
  put: (path, body) => request('PUT', path, { body }),
  delete: (path, query) => request('DELETE', path, { query }),
  upload: (path, form, query) => request('POST', path, { form, query }),
}

// A message for the output pane from any failure, with diagnostics and tracebacks when present.
export function describeError(error) {
  if (!(error instanceof ApiError)) return String(error && error.message ? error.message : error)
  const lines = [error.message]
  for (const d of error.body.diagnostics || []) lines.push(`  line ${d.line}: ${d.message}`)
  if (error.body.traceback) lines.push('', error.body.traceback)
  return lines.join('\n')
}

export function formatLoad(load) {
  if (!load) return ''
  if (load.ok) return 'Loaded without errors.'
  const lines = [load.abandoned
    ? 'The load was abandoned, the previous state stays live:'
    : `Loaded with ${load.errors.length} error${load.errors.length === 1 ? '' : 's'}:`]
  for (const e of load.errors) {
    lines.push(`${e.path}${e.line ? ':' + e.line : ''}: ${e.message}`)
    if (e.traceback) lines.push(e.traceback.replace(/^/gm, '    ').trimEnd())
  }
  return lines.join('\n')
}
