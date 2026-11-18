<script setup>
// The Tasks tab: a table of tasks; edits go to tasks.yaml, preserving its comments.
// Tasks named only in the `tasks` field of feeds are listed too; editing one declares it.
import { computed, inject, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { api, describeError } from '../api'
import { formatTime } from '../time'

const props = defineProps({ active: Boolean })
const store = inject('store')

const LISTS = ['tags', 'path', 'list', 'run', 'then', 'pre', 'post']
const TEXTS = ['call', 'timeout', 'post_timeout']

const rows = ref([])
const errors = ref([])
const functions = ref([])
const message = ref('')
const failed = ref(false)
const edits = reactive({}) // name -> field -> text
const expanded = reactive({}) // name -> feeds of the task, when shown
const filter = ref('')
const blankTask = () => ({
  name: '', concurrency: 'skip',
  ...Object.fromEntries([...LISTS, ...TEXTS].map((f) => [f, ''])),
})
const adding = reactive(blankTask())
const addingOpen = ref(false)
const addingInput = ref(null) // the name input of the new-task row

async function openAdding() {
  addingOpen.value = true
  await nextTick()
  if (addingInput.value) addingInput.value.focus()
}

function closeAdding() {
  addingOpen.value = false
  Object.assign(adding, blankTask())
}

const shown = computed(() => {
  const needle = filter.value.trim().toLowerCase()
  return needle ? rows.value.filter((r) => r.name.toLowerCase().includes(needle)) : rows.value
})

function say(text, isError = false) {
  message.value = text
  failed.value = isError
}

const joinList = (values) => values.join(', ')
const splitList = (text) => text.split(',').map((s) => s.trim()).filter(Boolean)

async function refresh() {
  try {
    const data = await api.get('/tasks/table')
    rows.value = data.tasks
    errors.value = data.errors
    functions.value = data.functions
    for (const key of Object.keys(edits)) delete edits[key]
    for (const row of data.tasks) {
      const edit = {}
      for (const f of LISTS) edit[f] = joinList(row[f])
      for (const f of TEXTS) edit[f] = row[f]
      edits[row.name] = edit
    }
    for (const name of Object.keys(expanded)) {
      if (!data.tasks.some((r) => r.name === name)) delete expanded[name]
    }
  } catch (error) {
    say(describeError(error), true)
  }
}

async function change(row, fields, done) {
  try {
    const result = await api.post('/tasks/save', { name: row.name, fields })
    store.setLoad(result.load)
    say(row.declared ? 'Saved to tasks.yaml.' : `${row.name} is now declared in tasks.yaml.`)
    await Promise.all([refresh(), store.refreshTasks()])
    if (done) done()
  } catch (error) {
    say(describeError(error), true)
  }
}

function commit(row, field) {
  const text = edits[row.name][field]
  if (LISTS.includes(field)) {
    const values = splitList(text)
    if (joinList(values) !== joinList(row[field])) change(row, { [field]: values })
    else edits[row.name][field] = joinList(row[field])
  } else if (text.trim() !== row[field]) {
    change(row, { [field]: text.trim() })
  }
}

async function add() {
  const name = adding.name.trim()
  if (!name) return say('A task needs a name.', true)
  if (rows.value.some((r) => r.name.toLowerCase() === name.toLowerCase() && r.declared)) {
    return say(`${name} is already declared.`, true)
  }
  const fields = {}
  for (const f of LISTS) {
    const values = splitList(adding[f])
    if (values.length) fields[f] = values
  }
  for (const f of TEXTS) if (adding[f].trim()) fields[f] = adding[f].trim()
  if (adding.concurrency !== 'skip') fields.concurrency = adding.concurrency
  await change({ name, declared: true }, fields, () => {
    closeAdding()
    say(`Task ${name} added to tasks.yaml.`)
  })
  return undefined
}

async function remove(row) {
  const uses = []
  if (row.schedules) uses.push(`${row.schedules} schedule${row.schedules === 1 ? '' : 's'}`)
  const callers = rows.value.filter((r) => r.run.includes(row.name) || r.then.includes(row.name)).map((r) => r.name)
  if (callers.length) uses.push(`the tasks ${callers.join(', ')}`)
  const warning = uses.length ? `\n\nIt is still named by ${uses.join(' and ')}.` : ''
  if (!confirm(`Delete the task ${row.name} from tasks.yaml?${warning}`)) return
  try {
    const result = await api.post('/tasks/delete', { task: row.name })
    store.setLoad(result.load)
    say(`${row.name} deleted from tasks.yaml.`)
    await Promise.all([refresh(), store.refreshTasks()])
  } catch (error) {
    say(describeError(error), true)
  }
}

async function runNow(row) {
  try {
    await api.post('/tasks/run', { task: row.name })
    say(`${row.name} enqueued.`)
  } catch (error) {
    say(describeError(error), true)
  }
}

async function toggleFeeds(row) {
  if (expanded[row.name]) {
    delete expanded[row.name]
    return
  }
  try {
    expanded[row.name] = await api.get('/tasks/feeds', { task: row.name })
  } catch (error) {
    say(describeError(error), true)
  }
}

function time(iso) {
  return iso ? formatTime(iso) : ''
}

onMounted(refresh)
watch(() => props.active, (active) => active && refresh())
</script>

<template>
  <div class="tab-pane">
    <div class="toolbar">
      <button class="primary" :disabled="addingOpen" @click="openAdding">Add task</button>
      <button @click="refresh">Refresh</button>
      <button @click="store.openFile('tasks.yaml')">Edit tasks.yaml</button>
      <input v-model="filter" type="search" placeholder="Filter by name" size="18" />
      <span class="hint" :class="{ 'error-text': failed }">
        {{ message || 'Lists are comma-separated. Durations look like 30m, 2h or 45s.' }}
      </span>
    </div>
    <div v-if="errors.length" class="tab-pane padded error-text" style="flex: none; height: auto">
      <div v-for="(e, i) in errors" :key="i">tasks.yaml{{ e.line ? ':' + e.line : '' }}: {{ e.message }}</div>
    </div>
    <datalist id="task-list-names"><option v-for="r in rows" :key="r.name" :value="r.name" /></datalist>
    <datalist id="task-function-names"><option v-for="f in functions" :key="f" :value="f" /></datalist>
    <div class="scroll">
      <table class="grid tasks-grid">
        <thead>
          <tr>
            <th>Task</th>
            <th title="Feeds are selected by tags, path globs, or suffixes, and by their own tasks field">Feed selection</th>
            <th>Feeds</th>
            <th title="Subtasks, started together">Run</th>
            <th title="Tasks enqueued when this one has finished">Then</th>
            <th title="Task functions run before and after the feed jobs">Pre / post</th>
            <th title="A task function run as one job">Call</th>
            <th>Concurrency</th>
            <th title="For example 30m, 2h or 45s">Timeout / post</th>
            <th>Schedules</th>
            <th>Last run</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="addingOpen" class="new-task" @keyup.enter="$event.target.tagName === 'INPUT' && add()"
              @keyup.esc="closeAdding">
            <td class="nowrap">
              <input ref="addingInput" v-model="adding.name" type="text" class="mono" placeholder="new task name"
                     size="14" />
            </td>
            <td class="field-stack">
              <label><span>tags</span><input v-model="adding.tags" type="text" size="14" /></label>
              <label><span>path</span><input v-model="adding.path" type="text" class="mono" size="14"
                     placeholder="news/*" /></label>
              <label><span>list</span><input v-model="adding.list" type="text" class="mono" size="14"
                     placeholder="suffixes" /></label>
            </td>
            <td></td>
            <td><input v-model="adding.run" type="text" class="mono" size="14" list="task-list-names" /></td>
            <td><input v-model="adding.then" type="text" class="mono" size="14" list="task-list-names" /></td>
            <td class="field-stack">
              <label><span>pre</span><input v-model="adding.pre" type="text" class="mono" size="14"
                     list="task-function-names" /></label>
              <label><span>post</span><input v-model="adding.post" type="text" class="mono" size="14"
                     list="task-function-names" /></label>
            </td>
            <td><input v-model="adding.call" type="text" class="mono" size="14" list="task-function-names" /></td>
            <td>
              <select v-model="adding.concurrency">
                <option value="skip">skip</option>
                <option value="queue">queue</option>
                <option value="allow">allow</option>
              </select>
            </td>
            <td class="field-stack">
              <label><span>run</span><input v-model="adding.timeout" type="text" class="mono" size="6"
                     placeholder="none" /></label>
              <label><span>post</span><input v-model="adding.post_timeout" type="text" class="mono" size="6"
                     placeholder="5m" /></label>
            </td>
            <td></td>
            <td></td>
            <td class="nowrap">
              <button class="small primary" @click="add">Save</button>
              <button class="small" @click="closeAdding">Cancel</button>
            </td>
          </tr>
          <tr v-if="rows.length && !shown.length">
            <td colspan="12" class="muted">No tasks match the filter.</td>
          </tr>
          <template v-for="row in shown" :key="row.name">
            <tr>
              <td class="nowrap">
                <span class="mono">{{ row.name }}</span>
                <div v-if="!row.declared" class="muted small-note" title="Named in the tasks field of feeds, not declared in tasks.yaml">from feeds</div>
                <div v-if="row.error" class="error-text small-note">{{ row.error }}</div>
              </td>
              <td class="field-stack">
                <label><span>tags</span><input v-model="edits[row.name].tags" type="text" size="14"
                       @change="commit(row, 'tags')" @keyup.enter="$event.target.blur()" /></label>
                <label><span>path</span><input v-model="edits[row.name].path" type="text" class="mono" size="14"
                       placeholder="news/*" @change="commit(row, 'path')" @keyup.enter="$event.target.blur()" /></label>
                <label><span>list</span><input v-model="edits[row.name].list" type="text" class="mono" size="14"
                       placeholder="suffixes" @change="commit(row, 'list')" @keyup.enter="$event.target.blur()" /></label>
              </td>
              <td>
                <button class="small" :title="expanded[row.name] ? 'Hide the feeds' : 'Show the feeds of the task and its subtasks'"
                        @click="toggleFeeds(row)">{{ row.feeds }} {{ expanded[row.name] ? '▴' : '▾' }}</button>
              </td>
              <td>
                <input v-model="edits[row.name].run" type="text" class="mono" size="14" list="task-list-names"
                       @change="commit(row, 'run')" @keyup.enter="$event.target.blur()" />
              </td>
              <td>
                <input v-model="edits[row.name].then" type="text" class="mono" size="14" list="task-list-names"
                       @change="commit(row, 'then')" @keyup.enter="$event.target.blur()" />
              </td>
              <td class="field-stack">
                <label><span>pre</span><input v-model="edits[row.name].pre" type="text" class="mono" size="14"
                       list="task-function-names" @change="commit(row, 'pre')" @keyup.enter="$event.target.blur()" /></label>
                <label><span>post</span><input v-model="edits[row.name].post" type="text" class="mono" size="14"
                       list="task-function-names" @change="commit(row, 'post')" @keyup.enter="$event.target.blur()" /></label>
              </td>
              <td>
                <input v-model="edits[row.name].call" type="text" class="mono" size="14" list="task-function-names"
                       @change="commit(row, 'call')" @keyup.enter="$event.target.blur()" />
              </td>
              <td>
                <select :value="row.concurrency" @change="change(row, { concurrency: $event.target.value })">
                  <option value="skip">skip</option>
                  <option value="queue">queue</option>
                  <option value="allow">allow</option>
                </select>
              </td>
              <td class="field-stack">
                <label><span>run</span><input v-model="edits[row.name].timeout" type="text" class="mono" size="6"
                       placeholder="none" @change="commit(row, 'timeout')" @keyup.enter="$event.target.blur()" /></label>
                <label><span>post</span><input v-model="edits[row.name].post_timeout" type="text" class="mono" size="6"
                       placeholder="5m" @change="commit(row, 'post_timeout')" @keyup.enter="$event.target.blur()" /></label>
              </td>
              <td>{{ row.schedules || '—' }}</td>
              <td class="nowrap">
                <span v-if="row.last_status" class="badge" :class="row.last_status">{{ row.last_status }}</span>
                <div class="muted small-note">{{ time(row.last_started_at) }}</div>
              </td>
              <td class="nowrap">
                <button class="small" @click="runNow(row)">Run now</button>
                <button v-if="row.declared" class="small danger" @click="remove(row)">Delete</button>
              </td>
            </tr>
            <tr v-if="expanded[row.name]" class="task-feeds">
              <td></td>
              <td colspan="11">
                <span v-if="!expanded[row.name].length" class="muted">No feeds.</span>
                <span v-for="f in expanded[row.name]" :key="f.key" class="task-feed" :title="f.key">{{ f.title || f.suffix }}</span>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
  </div>
</template>
