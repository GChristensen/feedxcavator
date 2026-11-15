<script setup>
// The Workspace tab (UI2-6 to UI2-7b, UI2-10, UI2-11): one tree for all files next to an editor
// with one tab per open file.
import { computed, inject, markRaw, onMounted, onUnmounted, provide, reactive, ref } from 'vue'
import { api, ApiError, describeError, formatLoad } from '../api'
import { createSession } from '../ace'
import { icon, KIND_ICONS, outlineOf } from '../outline'
import AceEditor from './AceEditor.vue'
import TreeNode from './TreeNode.vue'

const store = inject('store')
const STORAGE_KEY = 'feedxcavator.workspace'

function loadState() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY)) || {}
  } catch {
    return {}
  }
}
const saved = loadState()

const nodes = ref([])
const files = reactive([]) // { path, name, lang, session, mtime, dirty, clean }
const active = ref(null)
const busy = ref(false)
const status = ref('')
const outline = ref([])
const menu = reactive({ visible: false, x: 0, y: 0, entries: [] })
const editor = ref(null)
const outputEditor = ref(null)
const outputSession = ref(markRaw(createSession('', 'text')))
let outlineTimer = null
let validateTimer = null
let statusTimer = null

const tree = reactive({
  expanded: new Set(saved.expanded || ['feeds', 'lib', 'handlers']),
  selected: saved.selected || null,
  errorPaths: new Set(),
  dropTarget: null,
  toggle(path) {
    tree.expanded.has(path) ? tree.expanded.delete(path) : tree.expanded.add(path)
    persist()
  },
  select(path) {
    tree.selected = path
    persist()
  },
  open: (path) => open(path),
  menu: (event, node) => showMenu(event, node),
  move: (source, directory) => move(source, directory),
})
provide('tree', tree)

function persist() {
  // UI-5: the tree state persists in local storage
  localStorage.setItem(STORAGE_KEY, JSON.stringify({
    expanded: [...tree.expanded], selected: tree.selected, open: files.map((f) => f.path), active: active.value,
  }))
}

const current = computed(() => files.find((f) => f.path === active.value) || null)
const isFeed = computed(() => !!current.value && /^feeds\/.+\/feed\.yaml$/.test(current.value.path))
const isScratch = computed(() => !!current.value && /^scratch\.(hy|py)$/.test(current.value.path))
const currentFeed = computed(() => (isFeed.value ? findNode(current.value.path.replace(/\/feed\.yaml$/, '')) : null))

function findNode(path, list = nodes.value) {
  for (const node of list) {
    if (node.path === path) return node
    if (node.children && path.startsWith(node.path + '/')) {
      const found = findNode(path, node.children)
      if (found) return found
    }
  }
  return null
}

function setOutput(text, lang = 'text') {
  outputSession.value = markRaw(createSession(text || '', lang))
}

function flash(message, ms = 2000) {
  status.value = message
  clearTimeout(statusTimer)
  if (ms) statusTimer = setTimeout(() => (status.value = ''), ms)
}

async function run(fn) {
  busy.value = true
  try {
    return await fn()
  } catch (error) {
    if (!(error instanceof ApiError && error.handled)) setOutput(describeError(error))
    return undefined
  } finally {
    busy.value = false
  }
}

async function refreshTree() {
  const data = await api.get('/tree')
  nodes.value = data.tree
  tree.errorPaths = new Set(data.errors)
  store.setLoad(data.load)
  annotate()
}

function afterLoad(load) {
  store.setLoad(load)
  return Promise.all([refreshTree(), store.refreshTasks()])
}

// files ------------------------------------------------------------------------------------------

function annotate(file = current.value, extra = []) {
  if (!file) return
  const annotations = extra.map((d) => ({ row: Math.max(0, (d.line || 1) - 1), column: 0, text: d.message, type: 'error' }))
  for (const e of (store.load && store.load.errors) || []) {
    if (e.path === file.path) annotations.push({ row: Math.max(0, (e.line || 1) - 1), column: 0, text: e.message, type: 'error' })
  }
  file.session.setAnnotations(annotations)
}

function refreshOutline() {
  const file = current.value
  if (!file) {
    outline.value = []
    return
  }
  const items = outlineOf(file.path, file.session.getValue())
  if (file.path === 'tasks.yaml') {
    // UI-14: also the task names taken from the feeds
    const declared = new Set(items.map((i) => i.name.toLowerCase()))
    for (const task of store.tasks) {
      if (!declared.has(task.name.toLowerCase())) items.push({ kind: 'task', name: task.name, task: task.name, line: null })
    }
  }
  outline.value = items
}

function watchSession(file) {
  file.session.on('change', () => {
    file.dirty = file.session.getValue() !== file.clean
    clearTimeout(outlineTimer)
    outlineTimer = setTimeout(refreshOutline, 3000) // UI-13
    if (file.lang === 'yaml') {
      clearTimeout(validateTimer)
      validateTimer = setTimeout(() => validate(file), 900) // UI2-5: while typing
    }
  })
}

async function validate(file) {
  try {
    const result = await api.post('/file/validate', { path: file.path, content: file.session.getValue() })
    annotate(file, result.diagnostics)
  } catch { /* validation is advisory */ }
}

async function open(path, line) {
  let file = files.find((f) => f.path === path)
  if (!file) {
    const data = await run(() => api.get('/file', { path }))
    if (!data) return
    file = reactive({
      path: data.path, name: data.path.split('/').pop(), lang: data.lang, mtime: data.mtime, dirty: false,
      clean: data.content, session: markRaw(createSession(data.content, data.lang)),
    })
    watchSession(file)
    files.push(file)
  }
  activate(file.path)
  if (line) setTimeout(() => editor.value && editor.value.gotoLine(line), 0)
}

function activate(path) {
  active.value = path
  tree.selected = path
  persist()
  refreshOutline()
  annotate()
  const file = current.value
  const errors = ((store.load && store.load.errors) || []).filter((e) => file && e.path === file.path)
  if (errors.length) setOutput(formatLoad({ ok: false, errors })) // UI2-7a
}

function tabLabel(file) {
  if (file.name !== 'feed.yaml') return file.name
  const node = findNode(file.path.replace(/\/feed\.yaml$/, ''))
  return node && node.feed ? node.feed.title || node.name : file.path.split('/').slice(-2, -1)[0]
}

function close(file) {
  if (file.dirty && !confirm(`Discard unsaved changes in ${file.path}?`)) return // UI-7
  const index = files.indexOf(file)
  files.splice(index, 1)
  if (active.value === file.path) {
    const next = files[Math.min(index, files.length - 1)]
    active.value = next ? next.path : null
    if (next) activate(next.path)
    else outline.value = []
  }
  persist()
}

function renameOpen(oldPath, newPath) {
  for (const file of files) {
    if (file.path === oldPath || file.path.startsWith(oldPath + '/')) {
      const wasActive = active.value === file.path
      file.path = newPath + file.path.slice(oldPath.length)
      file.name = file.path.split('/').pop()
      if (wasActive) active.value = file.path
    }
  }
  if (tree.expanded.has(oldPath)) {
    tree.expanded.delete(oldPath)
    tree.expanded.add(newPath)
  }
  persist()
}

function closeUnder(path) {
  for (const file of [...files]) {
    if (file.path === path || file.path.startsWith(path + '/')) {
      file.dirty = false
      close(file)
    }
  }
}

async function save(force = false) {
  const file = current.value
  if (!file) return
  const content = file.session.getValue()
  busy.value = true
  try {
    const result = await api.put('/file', { path: file.path, content, base_mtime: file.mtime, force })
    file.mtime = result.mtime
    file.clean = content
    file.dirty = false
    await afterLoad(result.load)
    // UI2-11: the result of the reload the save triggered
    if (result.load.ok) {
      setOutput('')
      flash('Successfully saved.')
    } else if (result.file_errors.length) {
      setOutput(formatLoad({ ok: false, errors: result.file_errors, abandoned: result.load.abandoned }))
      flash('Saved, but the file failed to load.', 0)
    } else {
      setOutput('Saved.\n' + formatLoad(result.load))
      flash('Saved. Other files have load errors.', 4000)
    }
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) {
      // UI2-10: the file changed on disk while it was open
      busy.value = false
      if (confirm(`${file.path} was changed on disk since it was opened.\nOverwrite it with the editor contents?`)) {
        return save(true)
      }
      flash('Not saved: the file changed on disk.', 0)
      return undefined
    }
    if (error instanceof ApiError && error.body.diagnostics) annotate(file, error.body.diagnostics)
    setOutput(describeError(error))
    flash('Not saved.', 0)
  } finally {
    busy.value = false
  }
  return undefined
}

async function reloadFromDisk() {
  const file = current.value
  if (!file || (file.dirty && !confirm('Discard unsaved changes?'))) return
  const data = await run(() => api.get('/file', { path: file.path }))
  if (!data) return
  file.session.setValue(data.content)
  file.clean = data.content
  file.mtime = data.mtime
  file.dirty = false
}

// feed actions (UI-8, UI2-7) ----------------------------------------------------------------------

async function test() {
  const file = current.value
  if (!file) return
  const result = await run(() => api.post('/feed/test', { path: file.path, yaml: file.session.getValue() }))
  if (!result) return
  const lang = result.content_type.includes('xml') ? 'xml' : result.content_type.includes('json') ? 'json' : 'text'
  setOutput(result.output, lang)
  flash(`${result.count} headline${result.count === 1 ? '' : 's'}${result.error ? ' (' + result.error + ')' : ''}`, 0)
}

async function suggest() {
  const file = current.value
  if (!file) return
  const result = await run(() => api.post('/feed/suggest', { path: file.path, yaml: file.session.getValue() }))
  if (!result) return
  // AUTO-2: the proposed block is inserted into the editor; nothing is saved until the user saves
  const lines = file.session.getValue().split('\n')
  const start = lines.findIndex((l) => /^selectors:/.test(l))
  const block = result.yaml.replace(/\n$/, '').split('\n')
  if (start >= 0) {
    let end = start + 1
    while (end < lines.length && (/^\s/.test(lines[end]) || lines[end] === '')) end += 1
    while (end > start + 1 && lines[end - 1] === '') end -= 1
    lines.splice(start, end - start, ...block)
  } else {
    while (lines.length && lines[lines.length - 1] === '') lines.pop()
    lines.push(...block, '')
  }
  file.session.setValue(lines.join('\n'))
  file.dirty = file.session.getValue() !== file.clean
  setOutput('Suggested selectors were inserted. Test the feed, then save.\n\n' + result.yaml, 'yaml')
}

async function runScratch() {
  const file = current.value
  if (!file) return
  const result = await run(() => api.post('/scratch/run', { path: file.path, content: file.session.getValue() }))
  if (!result) return
  setOutput(result.output)
  annotate(file, result.error ? [result.error] : [])
  flash(result.error ? 'Failed.' : 'Done.', result.error ? 0 : 2000)
}

async function feedUrl(feed) {
  const result = await run(() => api.get('/feed/url', { key: feed.key }))
  return result ? result.url : null
}

async function openFeedUrl(feed) {
  const url = await feedUrl(feed)
  if (url) window.open(url, '_blank', 'noopener')
}

async function subscribe(feed) {
  const url = await feedUrl(feed)
  if (!url) return
  const base = store.settings.subscription_url || 'https://newsblur/?url=' // UI-8
  window.open(base + encodeURIComponent(url), '_blank', 'noopener')
}

async function runFeedTasks(feed) {
  const result = await run(() => api.post('/feed/run-tasks', { key: feed.key }))
  if (result) flash(result.enqueued.length ? `Enqueued: ${result.enqueued.join(', ')}` : 'The feed has no tasks.', 4000)
}

// Changes the suffix in feed.yaml and moves the stored output, history and subscriptions with it.
async function renameSuffix(node) {
  const path = node.path + '/feed.yaml'
  const open_ = files.find((f) => f.path === path)
  if (open_ && open_.dirty) {
    alert('Save or discard the unsaved changes in this feed.yaml first.')
    return
  }
  const pinned = !!node.feed.id
  const suffix = prompt(
    `New suffix for "${node.feed.title || node.name}" (ASCII letters, digits, . - _ ~).\n`
    + (pinned
      ? 'The feed has an id, so its stored state stays where it is.'
      : 'Its stored output, history and subscriptions move to the new name.')
    + '\nThe feed URL changes: readers have to subscribe to the new one.',
    node.feed.suffix || node.name,
  )
  if (!suffix || suffix === node.feed.suffix) return
  const result = await run(() => api.post('/feed/rename-suffix', { path: node.path, suffix: suffix.trim() }))
  if (!result) return
  if (open_ && result.content !== undefined) {
    open_.session.setValue(result.content)
    open_.clean = result.content
    open_.mtime = result.mtime
    open_.dirty = false
  }
  await afterLoad(result.load)
  const moved = result.moved
  setOutput(`Suffix changed to ${result.suffix}.\n` + (moved
    ? `Moved: ${moved.output ? 'stored output, ' : ''}${moved.history} history keys, `
      + `${moved.subscriptions} subscriptions, ${moved.runs} run records.`
    : 'The stored state is kept under the feed id and was not moved.')
    + (result.load.ok ? '' : '\n\n' + formatLoad(result.load)))
  flash('Suffix changed.')
}

// tree actions (UI2-6) -----------------------------------------------------------------------------

function parentOf(path) {
  return path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : ''
}

function directoryFor(node) {
  if (!node) return ''
  return node.type === 'file' ? parentOf(node.path) : node.path
}

async function newFeed(node) {
  let directory = directoryFor(node)
  directory = directory === 'feeds' ? '' : directory.startsWith('feeds/') ? directory.slice(6) : ''
  const result = await run(() => api.post('/feed/new', { dir: directory }))
  if (!result) return
  let path = parentOf(result.dir)
  while (path) {
    tree.expanded.add(path)
    path = parentOf(path)
  }
  await afterLoad(result.load)
  await open(result.path) // UI-6
}

async function create(node, kind) {
  const directory = directoryFor(node)
  const what = kind === 'dir' ? 'folder' : 'file'
  const suggestion = kind === 'dir' ? 'new-folder' : node && node.type === 'feed' ? 'extract.hy' : 'new-file.hy'
  const name = prompt(`Name of the new ${what}${directory ? ' in ' + directory : ''}:`, suggestion)
  if (!name) return
  const path = (directory ? directory + '/' : '') + name.replace(/^\/+/, '')
  const result = await run(() => api.post('/file/create', { path, kind, content: '' }))
  if (!result) return
  if (directory) tree.expanded.add(directory)
  await afterLoad(result.load)
  if (kind === 'file') await open(result.path)
}

async function rename(node) {
  const name = prompt(`Rename ${node.path} to:`, node.name)
  if (!name || name === node.name) return
  const target = (parentOf(node.path) ? parentOf(node.path) + '/' : '') + name
  await moveTo(node.path, target)
}

async function move(source, directory) {
  const target = directory + '/' + source.split('/').pop()
  if (target === source || directory === source || directory.startsWith(source + '/')) return
  await moveTo(source, target)
}

async function moveTo(source, target) {
  const dirty = files.some((f) => f.dirty && (f.path === source || f.path.startsWith(source + '/')))
  if (dirty && !confirm('Files under this path have unsaved changes. Move anyway?')) return
  const result = await run(() => api.post('/file/move', { src: source, dst: target }))
  if (!result) return
  renameOpen(source, result.path)
  for (const file of files) {
    if (file.path === result.path || file.path.startsWith(result.path + '/')) {
      try {
        file.mtime = (await api.get('/file', { path: file.path })).mtime
      } catch { /* keep the old one */ }
    }
  }
  await afterLoad(result.load)
  flash(`Moved to ${result.path}.`)
}

async function remove(node) {
  const what = node.type === 'feed' ? `the feed "${node.feed.title || node.name}" and its files`
    : node.type === 'dir' ? `the folder ${node.path} and everything in it` : node.path
  if (!confirm(`Delete ${what}?`)) return
  const result = await run(() => api.delete('/file', { path: node.path }))
  if (!result) return
  closeUnder(node.path)
  await afterLoad(result.load)
  flash('Deleted.')
}

async function listTaskFeeds(task) {
  const feeds = await run(() => api.get('/tasks/feeds', { task }))
  if (feeds) setOutput(feeds.length ? feeds.map((f) => f.title).join('\n') : `Task ${task} has no feeds.`)
}

async function runTask(task) {
  const result = await run(() => api.post('/tasks/run', { task }))
  if (result) flash(`${task} enqueued`, 4000)
}

// context menus ------------------------------------------------------------------------------------

function popup(event, entries) {
  menu.entries = entries
  menu.x = Math.min(event.clientX, window.innerWidth - 220)
  menu.y = Math.min(event.clientY, window.innerHeight - entries.length * 30 - 10)
  menu.visible = true
}

function showMenu(event, node) {
  tree.select(node.path)
  const entries = []
  const inFeeds = node.path === 'feeds' || node.path.startsWith('feeds/')
  if (node.type === 'feed') {
    // UI2-7
    const ok = node.feed.status === 'ok'
    entries.push(
      { label: 'New code file', action: () => create(node, 'file') },
      { label: 'Test', action: async () => { await open(node.path + '/feed.yaml'); await test() } },
      { label: 'Open feed URL', action: () => openFeedUrl(node.feed), disabled: !ok },
      { label: 'Subscribe', action: () => subscribe(node.feed), disabled: !ok },
      { label: 'Run feed task', action: () => runFeedTasks(node.feed), disabled: !ok || !node.feed.tasks.length },
      { label: 'Suggest selectors', action: async () => { await open(node.path + '/feed.yaml'); await suggest() } },
      { divider: true },
      { label: 'Rename suffix', action: () => renameSuffix(node), disabled: !node.feed.suffix },
      { label: 'Rename folder', action: () => rename(node) },
      { label: 'Delete', action: () => remove(node) },
    )
  } else if (node.type === 'dir') {
    if (inFeeds) entries.push({ label: 'New feed', action: () => newFeed(node) })
    entries.push(
      { label: 'New file', action: () => create(node, 'file') },
      { label: 'New folder', action: () => create(node, 'dir') },
    )
    if (!['feeds', 'lib', 'handlers'].includes(node.path)) {
      entries.push({ divider: true }, { label: 'Rename', action: () => rename(node) },
        { label: 'Delete', action: () => remove(node) })
    }
  } else {
    entries.push({ label: 'Open', action: () => open(node.path) })
    if (!['tasks.yaml', 'schedules.yaml'].includes(node.path)) {
      entries.push({ label: 'Rename', action: () => rename(node) }, { label: 'Delete', action: () => remove(node) })
    }
  }
  popup(event, entries)
}

function rootMenu(event) {
  popup(event, [
    { label: 'New feed', action: () => newFeed(null) },
    { label: 'New file', action: () => create(null, 'file') },
    { label: 'New folder', action: () => create(null, 'dir') },
    { divider: true },
    { label: 'Reload workspace', action: reloadWorkspace },
  ])
}

function outlineMenu(event, item) {
  if (item.kind !== 'task' || !item.task) return
  // UI-14: right-clicking a task offers Run and List feeds
  popup(event, [
    { label: 'Run', action: () => runTask(item.task) },
    { label: 'List feeds', action: () => listTaskFeeds(item.task) },
  ])
}

function pick(entry) {
  menu.visible = false
  if (entry.action) entry.action()
}

function dropOnRoot(event) {
  event.preventDefault()
  const source = event.dataTransfer.getData('text/plain')
  if (source && source.includes('/')) moveTo(source, source.split('/').pop())
}

async function reloadWorkspace() {
  const load = await run(() => api.post('/reload'))
  if (!load) return
  await afterLoad(load)
  setOutput(formatLoad(load))
}

function showLoadErrors() {
  if (store.load) setOutput(formatLoad(store.load))
}

function hideMenu() {
  menu.visible = false
}

onMounted(async () => {
  window.addEventListener('click', hideMenu)
  window.addEventListener('blur', hideMenu)
  await run(refreshTree)
  for (const path of saved.open || []) {
    if (findNode(path) || findNode(parentOf(path))) await open(path)
  }
  if (saved.active && files.some((f) => f.path === saved.active)) activate(saved.active)
})
onUnmounted(() => {
  window.removeEventListener('click', hideMenu)
  window.removeEventListener('blur', hideMenu)
})

defineExpose({
  open,
  showLoadErrors,
  hasUnsaved: () => files.some((f) => f.dirty),
})
</script>

<template>
  <div class="tab-pane">
    <div class="workspace">
      <div class="sidebar">
        <div class="toolbar">
          <button @click="newFeed(findNode(tree.selected))">New Feed</button>
          <button title="New file" @click="create(findNode(tree.selected), 'file')">+ File</button>
          <button title="New folder" @click="create(findNode(tree.selected), 'dir')">+ Folder</button>
          <span class="grow"></span>
          <button class="small" title="Reload the tree" @click="run(refreshTree)">⟳</button>
        </div>
        <div class="tree" @contextmenu.prevent="rootMenu" @dragover.prevent @drop="dropOnRoot">
          <TreeNode v-for="node in nodes" :key="node.path" :node="node" />
        </div>
        <div v-if="outline.length" class="outline">
          <div class="title">Outline</div>
          <div
            v-for="(item, index) in outline" :key="index" class="item"
            @click="item.line && editor && editor.gotoLine(item.line)"
            @contextmenu.prevent.stop="outlineMenu($event, item)"
          >
            <img :src="icon(KIND_ICONS[item.kind])" alt="" />
            <span :class="{ muted: !item.line }">{{ item.name }}</span>
          </div>
        </div>
      </div>

      <div class="main">
        <div class="file-tabs">
          <div
            v-for="file in files" :key="file.path" class="file-tab"
            :class="{ active: file.path === active, error: tree.errorPaths.has(file.path) }"
            :title="file.path" @click="activate(file.path)" @mousedown.middle.prevent="close(file)"
          >
            <span class="name">{{ tabLabel(file) }}</span>
            <span v-if="file.dirty" class="dirty" title="unsaved changes">●</span>
            <button class="close" title="Close" @click.stop="close(file)">×</button>
          </div>
        </div>
        <div class="toolbar">
          <!-- UI2-7a: the toolbar adapts to the active file -->
          <button class="primary" :disabled="!current || busy" @click="save()">Save</button>
          <template v-if="isFeed">
            <button :disabled="busy" @click="test">Test</button>
            <button :disabled="busy" @click="suggest">Suggest selectors</button>
            <span class="sep"></span>
            <button :disabled="!currentFeed || currentFeed.feed.status !== 'ok'" @click="openFeedUrl(currentFeed.feed)">Open</button>
            <button :disabled="!currentFeed || currentFeed.feed.status !== 'ok'" @click="subscribe(currentFeed.feed)">Subscribe</button>
            <button :disabled="!currentFeed || !currentFeed.feed.suffix" title="Change the suffix and move the stored state with it"
                    @click="renameSuffix(currentFeed)">Rename suffix</button>
            <button class="danger" :disabled="!currentFeed" @click="remove(currentFeed)">Delete</button>
          </template>
          <button v-if="isScratch" :disabled="busy" @click="runScratch">Run</button>
          <span class="grow"></span>
          <span v-if="current" class="hint">{{ current.path }}</span>
          <button v-if="current" class="small" title="Reload the file from disk" @click="reloadFromDisk">⟳</button>
        </div>
        <div class="editor-area">
          <AceEditor v-show="current" ref="editor" :session="current ? current.session : null"
                     @save="isScratch ? runScratch() : save()" />
          <div v-if="!current" class="placeholder">
            <div>Select a feed or a file in the tree.<br />Right-click the tree for more actions.</div>
          </div>
        </div>
        <div class="statusbar">
          <img v-if="busy" :src="icon('loading.svg')" alt="working" />
          <span>{{ status }}</span>
          <span class="grow"></span>
          <span v-if="current && current.dirty">unsaved changes</span>
        </div>
        <div class="output-area">
          <AceEditor ref="outputEditor" :session="outputSession" read-only />
        </div>
      </div>
    </div>

    <div v-if="menu.visible" class="context-menu" :style="{ left: menu.x + 'px', top: menu.y + 'px' }" @click.stop>
      <template v-for="(entry, index) in menu.entries" :key="index">
        <div v-if="entry.divider" class="divider"></div>
        <div v-else class="entry" :class="{ disabled: entry.disabled }" @click="pick(entry)">{{ entry.label }}</div>
      </template>
    </div>
  </div>
</template>
