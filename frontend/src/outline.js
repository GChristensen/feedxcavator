// Outlines built by scanning the source text (UI-13, UI2-7b).

const PY = [
  [/^@extractor\(\s*["']([^"']+)["']/, 'extractor'],
  [/^@handler\(\s*["']([^"']+)["']/, 'handler'],
  [/^@task_function\(\s*["']([^"']+)["']/, 'task'],
  [/^(?:async\s+)?def\s+([A-Za-z_]\w*)/, 'function'],
  [/^class\s+([A-Za-z_]\w*)/, 'function'],
]

const HY = [
  [/^\(defextractor\s+([^\s\[\]()]+)/, 'extractor'],
  [/^\(defhandler\s+(?:\^:auth\s+|:auth\s+)?([^\s\[\]()]+)/, 'handler'],
  [/^\(deftaskfn\s+([^\s\[\]()]+)/, 'task'],
  [/^\(defselectors\s+([^\s\[\]()]+)/, 'function'],
  [/^\(defproxy\s+([^\s\[\]()]+)/, 'function'],
  [/^\(def(?:n|macro|class)\s+(?:\/a\s+)?([^\s\[\]()]+)/, 'function'],
]

export function outlineOf(path, text) {
  const lines = text.split('\n')
  const items = []
  if (path === 'tasks.yaml') {
    let inTasks = false
    lines.forEach((line, index) => {
      if (/^tasks:/.test(line)) inTasks = true
      else if (/^\S/.test(line)) inTasks = false
      const match = inTasks && line.match(/^ {2}(["']?)([^\s:#"']+)\1\s*:/)
      if (match) items.push({ kind: 'task', name: match[2], line: index + 1, task: match[2] })
    })
    return items
  }
  if (path === 'schedules.yaml') {
    let task = null
    lines.forEach((line, index) => {
      const t = line.match(/^\s*-?\s*task:\s*["']?([^"'#]+?)["']?\s*(#.*)?$/)
      if (t) task = { name: t[1].trim(), line: index + 1 }
      const c = line.match(/^\s*-?\s*cron:\s*["']?([^"'#]+?)["']?\s*(#.*)?$/)
      if (c && task) {
        items.push({ kind: 'schedule', name: `${task.name} (${c[1].trim()})`, line: task.line })
        task = null
      }
    })
    return items
  }
  const rules = path.endsWith('.py') ? PY : path.endsWith('.hy') ? HY : null
  if (!rules) return items
  let decorated = null
  lines.forEach((line, index) => {
    for (const [pattern, kind] of rules) {
      const match = line.match(pattern)
      if (!match) continue
      if (rules === PY && kind !== 'function') {
        decorated = { kind, name: match[1], line: index + 1 }
      } else if (rules === PY && decorated) {
        items.push(decorated)
        decorated = null
      } else {
        items.push({ kind, name: match[1], line: index + 1 })
      }
      break
    }
  })
  return items
}

export const KIND_ICONS = {
  extractor: 'extractor.svg',
  handler: 'handler.svg',
  task: 'task.svg',
  function: 'function.svg',
  schedule: 'schedule.svg',
}

export function icon(name) {
  return `${import.meta.env.BASE_URL}icons/${name}`
}
