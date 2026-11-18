// The backend of the static demo console (npm run build:demo). It answers the console API from fixtures
// recorded by tools/record_demo.py against a real server, and keeps edits in memory until the page reloads.
// Feeds that are not recorded get their Test output from handmade/<feed key>.xml, written by hand.

import { ApiError } from '../api'
import fixtures from './fixtures.json'

const handmade = import.meta.glob('./handmade/*.xml', { query: '?raw', import: 'default', eager: true })

const clone = (value) => JSON.parse(JSON.stringify(value))
const now = () => Date.now()
const nowSeconds = () => Date.now() / 1000
const NOT_IN_DEMO = 'Not available in the demo: it has no server behind it.'

const state = {
  files: new Map(Object.entries(fixtures.files).map(([path, content]) => [path, { content, mtime: nowSeconds() }])),
  dirs: new Set(),
  feeds: new Map(fixtures.feeds.map((f) => [f.dir, clone(f)])),
  tests: clone(fixtures.tests),
  tasksTable: clone(fixtures.tasks_table),
  schedules: clone(fixtures.schedules),
  wordfilters: new Map(fixtures.wordfilters.filter((w) => w.exists).map((w) => [w.name, fixtures.wordfilter_texts[w.name] || ''])),
  runs: clone(fixtures.runs),
  log: clone(fixtures.log),
  settings: { ...clone(fixtures.settings), authentication: false, restartable: false, worker_alive: true },
  nextRun: Math.max(0, ...fixtures.runs.map((r) => r.id)) + 1,
  nextLog: Math.max(0, ...fixtures.log.map((e) => e.number)) + 1,
  generation: 1,
}

for (const [file, output] of Object.entries(handmade)) {
  const key = file.split('/').pop().replace(/\.xml$/, '')
  const feed = [...state.feeds.values()].find((f) => f.key === key)
  if (feed) {
    state.tests[feed.dir + '/feed.yaml'] = { output, content_type: 'application/rss+xml',
      count: (output.match(/<item>/g) || []).length, error: null, seconds: 6.2 }
  }
}

function fail(status, error, extra = {}) {
  throw new ApiError(status, { error, ...extra })
}

function load() {
  return { ok: true, abandoned: false, generation: state.generation, loaded_at: nowSeconds(), errors: [] }
}

function reload() {
  state.generation += 1
  return load()
}

function language(name) {
  const ext = name.includes('.') ? name.split('.').pop() : ''
  return { yaml: 'yaml', yml: 'yaml', hy: 'hy', py: 'python', js: 'javascript', json: 'json', md: 'markdown',
    css: 'css', html: 'html' }[ext] || 'text'
}

const parentOf = (path) => (path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '')

function log(level, source, message) {
  state.log.unshift({ number: state.nextLog++, level, source, timestamp: now(), message })
}

// feeds -------------------------------------------------------------------------------------------

// A feed found from its feed.yaml text, for feeds created or edited in the demo.
function feedInfo(dir, text) {
  const value = (key) => {
    const match = text.match(new RegExp(`^${key}:\\s*(.*)$`, 'm'))
    return match ? match[1].replace(/\s+#.*$/, '').trim().replace(/^['"]|['"]$/g, '') : ''
  }
  const list = (key) => {
    const raw = value(key)
    return raw ? raw.replace(/^\[|\]$/g, '').split(',').map((s) => s.trim()).filter(Boolean) : []
  }
  const known = state.feeds.get(dir) || {}
  const suffix = value('suffix') || dir.split('/').pop()
  return {
    ...known, path: dir.replace(/^feeds\//, ''), dir, id: value('id') || null, key: value('id') || suffix,
    title: value('title') || suffix, suffix, enabled: value('enabled') !== 'false', tasks: list('tasks'),
    tags: list('tags'), status: known.status || 'ok', error: null,
  }
}

function syncFeeds() {
  const feeds = new Map()
  for (const [path, file] of state.files) {
    if (path.startsWith('feeds/') && path.endsWith('/feed.yaml')) {
      const dir = parentOf(path)
      feeds.set(dir, feedInfo(dir, file.content))
    }
  }
  state.feeds = feeds
}

function feedByKey(key) {
  return [...state.feeds.values()].find((f) => f.key === key) || fail(404, 'feed not found')
}

function taskFeeds(task) {
  const name = task.toLowerCase()
  return [...state.feeds.values()].filter((f) => f.tasks.some((t) => t.toLowerCase() === name))
}

// tree --------------------------------------------------------------------------------------------

function allDirs() {
  const dirs = new Set(state.dirs)
  for (const path of state.files.keys()) {
    for (let dir = parentOf(path); dir; dir = parentOf(dir)) dirs.add(dir)
  }
  return dirs
}

function tree(dir = '') {
  const dirs = allDirs()
  const childDirs = [...dirs].filter((d) => parentOf(d) === dir)
  const childFiles = [...state.files.keys()].filter((p) => parentOf(p) === dir)
  const byName = (a, b) => a.toLowerCase().localeCompare(b.toLowerCase())
  const nested = dir !== ''
  const nodes = childDirs
    .sort((a, b) => {
      const libA = nested && a.endsWith('/lib'), libB = nested && b.endsWith('/lib')
      return libA !== libB ? (libA ? -1 : 1) : byName(a, b)
    })
    .map((path) => {
      const feed = state.feeds.get(path)
      const node = { name: path.split('/').pop(), path, type: feed ? 'feed' : 'dir', children: tree(path) }
      if (feed) {
        node.feed = feed
        node.children = node.children.filter((c) => c.name !== 'feed.yaml')
      }
      return node
    })
  for (const path of childFiles.sort(byName)) {
    const name = path.split('/').pop()
    nodes.push({ name, path, type: 'file', lang: language(name) })
  }
  return nodes
}

function fileOf(path) {
  return state.files.get(path) || fail(404, `no such file: ${path}`)
}

function under(path, prefix) {
  return path === prefix || path.startsWith(prefix + '/')
}

// runs --------------------------------------------------------------------------------------------

function taskRow(name) {
  return state.tasksTable.tasks.find((t) => t.name.toLowerCase() === name.toLowerCase())
}

function fakeRun(task, trigger, depth = 0) {
  const started = new Date()
  const row = taskRow(task)
  const feedRuns = taskFeeds(task).map((feed) => {
    const test = state.tests[feed.dir + '/feed.yaml']
    const count = test ? test.count : 0
    const error = test ? test.error : 'not recorded in the demo'
    const seconds = test && test.seconds ? test.seconds : 1.5
    log(error ? 'error' : 'info', feed.title, error ? `${task}: ${error}` : `${task}: ${count} headlines`)
    return { feed_id: feed.key, title: feed.title, status: error ? 'error' : 'ok', headline_count: count,
      http_status: error ? null : 200, error, duration: seconds, zero: !error && !count }
  })
  const children = depth < 4 && row ? [...row.run, ...row.then].map((t) => fakeRun(t, 'subtask', depth + 1)) : []
  const duration = Math.max(0, ...feedRuns.map((f) => f.duration), ...children.map((c) => c.duration || 0)) + 0.3
  log('info', task, `task ${task} finished (${trigger})`)
  // as on the server, a task run is ok when it finished; failed feeds show in its feed runs
  return { id: state.nextRun++, task, trigger, status: 'ok', error: '',
    started_at: started.toISOString(), duration, feed_runs: feedRuns, children }
}

function runTask(task, trigger = 'manual') {
  log('info', task, `task ${task} enqueued (${trigger})`)
  const run = fakeRun(task, trigger)
  state.runs.unshift(run)
  const row = taskRow(task)
  if (row) {
    row.last_status = run.status
    row.last_started_at = run.started_at
  }
}

// word filters ------------------------------------------------------------------------------------

function filterUsers() {
  const users = {}
  for (const feed of state.feeds.values()) {
    const text = (state.files.get(feed.dir + '/feed.yaml') || {}).content || ''
    for (const match of text.matchAll(/wordfilter:\s*\{\s*name:\s*([^,}\s]+)/g)) {
      ;(users[match[1]] ||= []).push({ title: feed.title, dir: feed.dir })
    }
  }
  return users
}

function filterCount(text) {
  return text.split('\n').filter((l) => l.trim() && !l.trim().startsWith('#')).length
}

function filterName(name) {
  name = (name || '').trim()
  return name || fail(400, 'a word filter needs a name')
}

// the routes --------------------------------------------------------------------------------------

const routes = {
  'GET /settings': () => state.settings,
  'PUT /settings': (q, body) => {
    Object.assign(state.settings, body)
    return state.settings
  },
  'GET /tasks': () => state.tasksTable.tasks.map((t) => ({
    name: t.name, declared: t.declared, feeds: taskFeeds(t.name).length, run: t.run, then: t.then, pre: t.pre,
    post: t.post, call: t.call || null, concurrency: t.concurrency, timeout: t.timeout || null, error: t.error,
  })),
  'GET /load-status': load,
  'POST /reload': reload,

  'GET /tree': () => ({ tree: tree(), errors: [], load: load() }),
  'GET /feeds': () => [...state.feeds.values()],
  'GET /file': ({ path }) => {
    const file = fileOf(path)
    return { path, content: file.content, mtime: file.mtime, lang: language(path), errors: [] }
  },
  'PUT /file': (q, { path, content }) => {
    state.files.set(path, { content, mtime: nowSeconds() })
    syncFeeds()
    const report = reload()
    return { path, mtime: state.files.get(path).mtime, load: report, file_errors: [],
      feed: [...state.feeds.values()].find((f) => path.startsWith(f.dir + '/')) || null, renamed: null }
  },
  'POST /file/validate': () => ({ diagnostics: [] }),
  'POST /file/create': (q, { path, kind, content }) => {
    if (state.files.has(path) || allDirs().has(path)) fail(409, `${path} already exists`)
    if (kind === 'dir') state.dirs.add(path)
    else state.files.set(path, { content: content || '', mtime: nowSeconds() })
    return { path, load: reload() }
  },
  'POST /file/move': (q, { src, dst }) => {
    if (state.files.has(dst) || allDirs().has(dst)) fail(409, `${dst} already exists`)
    if (under(dst, src)) fail(400, 'cannot move a directory into itself')
    const move = (path) => dst + path.slice(src.length)
    state.files = new Map([...state.files].map(([p, f]) => [under(p, src) ? move(p) : p, f]))
    state.dirs = new Set([...state.dirs].map((d) => (under(d, src) ? move(d) : d)))
    for (const key of Object.keys(state.tests)) {
      if (under(key, src)) state.tests[move(key)] = state.tests[key]
    }
    syncFeeds()
    return { path: dst, load: reload() }
  },
  'DELETE /file': ({ path }) => {
    for (const p of [...state.files.keys()]) if (under(p, path)) state.files.delete(p)
    for (const d of [...state.dirs]) if (under(d, path)) state.dirs.delete(d)
    syncFeeds()
    return { load: reload() }
  },

  'POST /feed/new': (q, { dir }) => {
    const base = 'feeds' + (dir ? '/' + dir.replace(/^\/+|\/+$/g, '') : '')
    const taken = new Set([...state.feeds.values()].map((f) => f.suffix.toLowerCase()))
    let name = 'untitled-feed'
    for (let n = 2; allDirs().has(`${base}/${name}`) || taken.has(name); n++) name = `untitled-feed-${n}`
    const path = `${base}/${name}/feed.yaml`
    state.files.set(path, { mtime: nowSeconds(), content:
      `title: '*Untitled feed'\nsuffix: ${name}\nsource: https://example.com\n#selectors:\n#  item: div.headline\n#  title: h3\n#  link: a\n` })
    syncFeeds()
    return { path, dir: parentOf(path), load: reload() }
  },
  'POST /feed/test': (q, { path, yaml }) => {
    const recorded = state.tests[path]
    if (!recorded) {
      return { output: 'This feed was created in the demo, so there is no recorded output for it.\n\n'
        + 'On a real server, Test runs the unsaved definition and shows the feed it produces.',
      content_type: 'text/plain', count: 0, error: null }
    }
    const original = fixtures.files[path]
    const note = original !== undefined && yaml !== original
      ? '<!-- Demo: the definition was edited, but this is the output recorded for the original one. -->\n' : ''
    const output = recorded.output.startsWith('<?xml')
      ? recorded.output.replace(/^(<\?xml[^>]*>\n?)/, `$1${note}`) : note + recorded.output
    return { output, content_type: recorded.content_type, count: recorded.count, error: recorded.error }
  },
  'POST /feed/suggest': () => fail(501, NOT_IN_DEMO),
  'GET /feed/url': ({ key }) => {
    const feed = feedByKey(key)
    const test = state.tests[feed.dir + '/feed.yaml']
    if (!test || !test.output) fail(404, 'no recorded output for this feed in the demo')
    return { url: URL.createObjectURL(new Blob([test.output], { type: test.content_type || 'text/xml' })) }
  },
  'POST /feed/run-tasks': (q, { key }) => {
    const feed = feedByKey(key)
    for (const task of feed.tasks) runTask(task)
    return { enqueued: feed.tasks }
  },
  'POST /scratch/run': (q, { path, content }) => {
    if (content === fixtures.files[path] && fixtures.scratch && fixtures.scratch.output !== undefined) {
      return fixtures.scratch
    }
    return { output: 'Demo: only the scratch file as shipped has a recorded result.\n'
      + 'On a real server, Run evaluates the code and shows what it returns.', error: null }
  },

  'GET /reference': () => fixtures.reference,
  'GET /manual': () => fixtures.manual,

  'GET /tasks/table': () => {
    for (const row of state.tasksTable.tasks) {
      row.feeds = taskFeeds(row.name).length
      row.schedules = state.schedules.schedules.filter((s) => s.task.toLowerCase() === row.name.toLowerCase()).length
    }
    return state.tasksTable
  },
  'GET /tasks/feeds': ({ task }) => taskFeeds(task).map((f) => ({ key: f.key, title: f.title, suffix: f.suffix })),
  'POST /tasks/run': (q, { task }) => {
    runTask(task)
    return { enqueued: task }
  },
  'POST /tasks/save': (q, { name, fields }) => {
    name = (name || '').trim()
    if (!name || /[\s:#{}[\],&*!|>'"%@`]/.test(name)) fail(400, `'${name}' is not a usable task name`)
    let row = taskRow(name)
    if (!row) {
      row = { name, declared: true, tags: [], path: [], list: [], run: [], then: [], pre: [], post: [], call: '',
        concurrency: 'skip', timeout: '', post_timeout: '', feeds: 0, schedules: 0, error: null,
        last_status: null, last_started_at: null }
      state.tasksTable.tasks.push(row)
    }
    row.declared = true
    for (const [field, value] of Object.entries(fields || {})) {
      row[field] = Array.isArray(value) ? value.map(String) : String(value ?? '')
    }
    return { load: reload(), name }
  },
  'POST /tasks/delete': (q, { task }) => {
    const row = taskRow(task)
    if (!row || !row.declared) fail(404, `tasks.yaml does not declare ${task}`)
    state.tasksTable.tasks.splice(state.tasksTable.tasks.indexOf(row), 1)
    return { load: reload() }
  },

  'GET /schedules': () => {
    state.schedules.tasks = state.tasksTable.tasks.map((t) => t.name)
    state.schedules.schedules.forEach((s, i) => { s.index = i })
    return state.schedules
  },
  'POST /schedules/save': (q, body) => {
    const { index, ...fields } = body
    const rows = state.schedules.schedules
    if (index === undefined || index === null) {
      if (!fields.task || !fields.cron) fail(400, 'a schedule needs a task and a cron expression')
      rows.push({ index: rows.length, task: fields.task, cron: fields.cron, description: '', timezone: fields.timezone || null,
        effective_timezone: fields.timezone || 'UTC', misfire: fields.misfire || 'skip', enabled: true, jitter: 0,
        next: [], last_fired_at: null, last_status: null })
    } else {
      const row = rows[index] || fail(404, 'no such schedule')
      Object.assign(row, fields)
      if (fields.cron) row.description = ''
    }
    return { load: reload() }
  },
  'POST /schedules/delete': (q, { index }) => {
    if (!state.schedules.schedules[index]) fail(404, 'no such schedule')
    state.schedules.schedules.splice(index, 1)
    return { load: reload() }
  },

  'GET /runs': ({ limit = 50, before }) => {
    const runs = before ? state.runs.filter((r) => r.id < Number(before)) : state.runs
    return runs.slice(0, Number(limit))
  },
  'DELETE /runs': () => {
    const deleted = state.runs.length
    state.runs = []
    return { deleted }
  },

  'GET /subscriptions': () => [],
  'PUT /subscriptions': () => fail(501, NOT_IN_DEMO),
  'DELETE /subscriptions': () => fail(501, NOT_IN_DEMO),
  'POST /subscriptions/replace': () => fail(501, NOT_IN_DEMO),

  'GET /log': ({ n = 20, from }) => {
    const entries = from !== undefined ? state.log.filter((e) => e.number <= Number(from)) : state.log
    return entries.slice(0, Number(n))
  },
  'DELETE /log': () => {
    state.log = []
    return { ok: true }
  },
  'GET /server-log': () => ({ content: 'The demo has no server, so there is no server log.\n' }),

  'GET /wordfilters': () => {
    const users = filterUsers()
    const rows = new Map([...state.wordfilters].map(([name, text]) => [name,
      { name, count: filterCount(text), feeds: users[name] || [], exists: true }]))
    for (const [name, feeds] of Object.entries(users)) {
      if (!rows.has(name)) rows.set(name, { name, count: 0, feeds, exists: false })
    }
    return [...rows.values()].sort((a, b) => a.name.toLowerCase().localeCompare(b.name.toLowerCase()))
  },
  'GET /wordfilter': ({ name }) => ({ name, text: state.wordfilters.get(name) || '', exists: state.wordfilters.has(name) }),
  'PUT /wordfilter': (q, { name, text }) => {
    name = filterName(name)
    state.wordfilters.set(name, text)
    return { name, count: filterCount(text), text }
  },
  'POST /wordfilter/create': (q, { name }) => {
    name = filterName(name)
    if (state.wordfilters.has(name)) fail(409, `the word filter ${name} already exists`)
    state.wordfilters.set(name, '')
    return { name }
  },
  'POST /wordfilter/rename': (q, { name, new_name }) => {
    const target = filterName(new_name)
    if (!state.wordfilters.has(name)) fail(404, `no word filter ${name}`)
    if (target !== name && state.wordfilters.has(target)) fail(409, `the word filter ${target} already exists`)
    const text = state.wordfilters.get(name)
    state.wordfilters.delete(name)
    state.wordfilters.set(target, text)
    return { name: target, feeds: filterUsers()[name] || [] }
  },
  'DELETE /wordfilter': ({ name }) => {
    if (!state.wordfilters.delete(name)) fail(404, `no word filter ${name}`)
    return { ok: true }
  },

  'POST /token': () => fail(501, NOT_IN_DEMO),
  'POST /restore': () => fail(501, NOT_IN_DEMO),
  'POST /purge-images': () => fail(501, NOT_IN_DEMO),
  'POST /restart': () => fail(501, NOT_IN_DEMO),
}

export async function handle(method, path, { query, body } = {}) {
  await new Promise((resolve) => setTimeout(resolve, 60))
  // /subscriptions/12 -> /subscriptions
  const route = routes[`${method} ${path}`] || routes[`${method} ${path.replace(/\/\d+$/, '')}`]
  if (!route) fail(404, `${method} ${path} is not part of the demo`)
  const result = route(query || {}, body || {})
  return result === undefined ? null : clone(result)
}
