<script setup>
// The Word filters tab: the filters on the left, the entries of the selected one on the right, one per line.
// A line written as /expr/ is a regular expression, any other line a plain word (WF-1, WF-2).
import { markRaw, onMounted, ref, shallowRef, watch } from 'vue'
import { api, describeError } from '../api'
import { createSession } from '../ace'
import { icon } from '../outline'
import AceEditor from './AceEditor.vue'

const props = defineProps({ active: Boolean })

const rows = ref([])
const selected = ref(null) // the name of the filter in the editor
const session = shallowRef(null)
const saved = ref('') // the text as last loaded or saved
const dirty = ref(false)
const busy = ref(false)
const status = ref('')
const failed = ref(false)
const editor = ref(null)

function say(text, isError = false) {
  status.value = text
  failed.value = isError
}

async function run(fn) {
  busy.value = true
  try {
    return await fn()
  } catch (error) {
    say(describeError(error), true)
    return undefined
  } finally {
    busy.value = false
  }
}

function current() {
  return rows.value.find((r) => r.name === selected.value) || null
}

async function refresh() {
  rows.value = await api.get('/wordfilters')
}

function showDiagnostics(diagnostics) {
  if (!session.value) return
  session.value.setAnnotations(diagnostics.map((d) => ({ row: d.line - 1, column: 0, text: d.message, type: 'error' })))
}

function setText(text) {
  const s = markRaw(createSession(text, 'text'))
  s.on('change', () => { dirty.value = s.getValue() !== saved.value })
  saved.value = text
  dirty.value = false
  session.value = s
}

function confirmDiscard() {
  return !dirty.value || confirm(`The word filter ${selected.value} has unsaved changes. Discard them?`)
}

async function open(name, force = false) {
  if (!force && name === selected.value) return
  if (!force && !confirmDiscard()) return
  await run(async () => {
    const data = await api.get('/wordfilter', { name })
    selected.value = name
    setText(data.text)
    say(data.exists ? '' : `${name} is named by a feed but has no entries yet. Saving creates it.`)
  })
}

async function save() {
  if (!selected.value || !session.value) return
  await run(async () => {
    try {
      const result = await api.put('/wordfilter', { name: selected.value, text: session.value.getValue() })
      setText(result.text)
      say(`Saved ${result.count} entr${result.count === 1 ? 'y' : 'ies'} to ${result.name}.`)
      await refresh()
    } catch (error) {
      if (error.body && error.body.diagnostics) {
        showDiagnostics(error.body.diagnostics)
        if (editor.value) editor.value.gotoLine(error.body.diagnostics[0].line)
      }
      throw error
    }
  })
}

async function create() {
  if (!confirmDiscard()) return
  const name = (prompt('Name of the new word filter:', rows.value.length ? '' : 'default') || '').trim()
  if (!name) return
  await run(async () => {
    await api.post('/wordfilter/create', { name })
    await refresh()
    dirty.value = false
    await open(name, true)
    say(`Created ${name}.`)
    if (editor.value) editor.value.focus()
  })
}

async function rename() {
  const row = current()
  if (!row || !row.exists) return
  const newName = (prompt(`New name of the word filter ${row.name}:`, row.name) || '').trim()
  if (!newName || newName === row.name) return
  await run(async () => {
    const result = await api.post('/wordfilter/rename', { name: row.name, new_name: newName })
    selected.value = result.name
    await refresh()
    const users = result.feeds.map((f) => f.title || f.dir)
    say(users.length
      ? `Renamed to ${result.name}. These feeds still name ${row.name} in feed.yaml: ${users.join(', ')}.`
      : `Renamed to ${result.name}.`, users.length > 0)
  })
}

async function remove() {
  const row = current()
  if (!row || !row.exists) return
  const users = row.feeds.map((f) => f.title || f.dir)
  const warning = users.length ? `\n\nIt is used by: ${users.join(', ')}.` : ''
  if (!confirm(`Delete the word filter ${row.name} and its ${row.count} entries?${warning}`)) return
  await run(async () => {
    await api.delete('/wordfilter', { name: row.name })
    selected.value = null
    session.value = null
    dirty.value = false
    await refresh()
    say(`Deleted ${row.name}.`)
  })
}

function reloadFromServer() {
  if (selected.value) open(selected.value, !dirty.value || confirmDiscard())
}

onMounted(() => run(refresh))
watch(() => props.active, (active) => active && run(refresh))

defineExpose({ hasUnsaved: () => dirty.value })
</script>

<template>
  <div class="tab-pane">
    <div class="workspace">
      <div class="sidebar">
        <div class="toolbar">
          <button :disabled="busy" @click="create">New</button>
          <button :disabled="busy || !current() || !current().exists" @click="rename">Rename</button>
          <button class="danger" :disabled="busy || !current() || !current().exists" @click="remove">Delete</button>
          <span class="grow"></span>
          <button class="small" title="Reload the list" @click="run(refresh)">⟳</button>
        </div>
        <div class="tree">
          <div
            v-for="row in rows" :key="row.name" class="tree-row wordfilter-row"
            :class="{ selected: row.name === selected, disabled: !row.exists }"
            :title="row.feeds.length ? 'Used by ' + row.feeds.map((f) => f.title || f.dir).join(', ') : 'Not used by any feed'"
            @click="open(row.name)"
          >
            <span class="label">{{ row.name }}</span>
            <span v-if="row.name === selected && dirty" class="dirty" title="unsaved changes">●</span>
            <span class="grow"></span>
            <span class="count">{{ row.count }}</span>
            <span v-if="row.feeds.length" class="ext" :title="row.feeds.length + ' feed(s)'">{{ row.feeds.length }} feed{{ row.feeds.length === 1 ? '' : 's' }}</span>
          </div>
          <div v-if="!rows.length" class="placeholder-text muted">No word filters yet. Use New to create one.</div>
        </div>
      </div>

      <div class="main">
        <div class="toolbar">
          <button class="primary" :disabled="!selected || busy" @click="save">Save</button>
          <span class="hint">One entry per line. Words match case-insensitively anywhere in the field; write /expr/ for a regular expression.</span>
          <span class="grow"></span>
          <span v-if="selected" class="hint">{{ selected }}</span>
          <button v-if="selected" class="small" title="Reload the filter from the server" @click="reloadFromServer">⟳</button>
        </div>
        <div class="editor-area">
          <AceEditor v-show="session" ref="editor" :session="session" @save="save" />
          <div v-if="!session" class="placeholder">
            <div>Select a word filter on the left, or create one.</div>
          </div>
        </div>
        <div class="statusbar">
          <img v-if="busy" :src="icon('loading.svg')" alt="working" />
          <span :class="{ 'error-text': failed }">{{ status }}</span>
          <span class="grow"></span>
          <span v-if="dirty">unsaved changes</span>
        </div>
      </div>
    </div>
  </div>
</template>
