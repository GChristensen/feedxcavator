<script setup>
// The Schedules tab (UI2-8): edits go to schedules.yaml, preserving its comments.
import { inject, onMounted, reactive, ref, watch } from 'vue'
import { api, describeError } from '../api'

const props = defineProps({ active: Boolean })
const store = inject('store')

const rows = ref([])
const errors = ref([])
const tasks = ref([])
const message = ref('')
const failed = ref(false)
const edits = reactive({}) // index -> { cron, timezone }
const adding = reactive({ task: '', cron: '0 12 * * *', timezone: '', misfire: 'skip' })

function say(text, isError = false) {
  message.value = text
  failed.value = isError
}

async function refresh() {
  try {
    const data = await api.get('/schedules')
    rows.value = data.schedules
    errors.value = data.errors
    tasks.value = data.tasks
    for (const key of Object.keys(edits)) delete edits[key]
    for (const row of data.schedules) edits[row.index] = { cron: row.cron, timezone: row.timezone || '' }
  } catch (error) {
    say(describeError(error), true)
  }
}

async function change(row, fields) {
  try {
    const result = await api.post('/schedules/save', { index: row.index, ...fields })
    store.setLoad(result.load)
    say('Saved to schedules.yaml.')
    await refresh()
  } catch (error) {
    say(describeError(error), true)
  }
}

function commit(row) {
  const edit = edits[row.index]
  const fields = {}
  if (edit.cron.trim() !== row.cron) fields.cron = edit.cron.trim()
  if ((edit.timezone || '').trim() !== (row.timezone || '')) fields.timezone = (edit.timezone || '').trim()
  if (Object.keys(fields).length) change(row, fields)
}

async function add() {
  if (!adding.task.trim() || !adding.cron.trim()) return say('A schedule needs a task and a cron expression.', true)
  const body = { task: adding.task.trim(), cron: adding.cron.trim() }
  if (adding.timezone.trim()) body.timezone = adding.timezone.trim()
  if (adding.misfire !== 'skip') body.misfire = adding.misfire
  try {
    const result = await api.post('/schedules/save', body)
    store.setLoad(result.load)
    adding.task = ''
    say('Schedule added.')
    await refresh()
  } catch (error) {
    say(describeError(error), true)
  }
  return undefined
}

async function remove(row) {
  if (!confirm(`Delete the schedule of ${row.task} (${row.cron})?`)) return
  try {
    const result = await api.post('/schedules/delete', { index: row.index })
    store.setLoad(result.load)
    say('Schedule deleted.')
    await refresh()
  } catch (error) {
    say(describeError(error), true)
  }
}

async function runNow(row) {
  try {
    await api.post('/tasks/run', { task: row.task })
    say(`${row.task} enqueued.`)
  } catch (error) {
    say(describeError(error), true)
  }
}

function time(iso) {
  return iso ? new Date(iso).toLocaleString() : ''
}

onMounted(refresh)
watch(() => props.active, (active) => active && refresh())
</script>

<template>
  <div class="tab-pane">
    <div class="toolbar">
      <button @click="refresh">Refresh</button>
      <button @click="store.openFile('schedules.yaml')">Edit schedules.yaml</button>
      <span class="hint" :class="{ 'error-text': failed }">{{ message }}</span>
    </div>
    <div v-if="errors.length" class="tab-pane padded error-text" style="flex: none; height: auto">
      <div v-for="(e, i) in errors" :key="i">schedules.yaml{{ e.line ? ':' + e.line : '' }}: {{ e.message }}</div>
    </div>
    <div class="scroll">
      <table class="grid">
        <thead>
          <tr>
            <th>On</th><th>Task</th><th>Cron</th><th>Time zone</th><th>Missed firing</th><th>Next firings</th>
            <th>Last firing</th><th>Last run</th><th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.index">
            <td><input type="checkbox" :checked="row.enabled" title="Enabled"
                       @change="change(row, { enabled: $event.target.checked })" /></td>
            <td class="mono">{{ row.task }}</td>
            <td>
              <input v-model="edits[row.index].cron" type="text" class="mono" size="16"
                     @change="commit(row)" @keyup.enter="$event.target.blur()" />
              <div class="cron-words">{{ row.description }}</div>
            </td>
            <td>
              <input v-model="edits[row.index].timezone" type="text" size="16" :placeholder="row.effective_timezone"
                     @change="commit(row)" @keyup.enter="$event.target.blur()" />
            </td>
            <td>
              <select :value="row.misfire" @change="change(row, { misfire: $event.target.value })">
                <option value="skip">skip</option>
                <option value="run-once">run once</option>
              </select>
            </td>
            <td class="next-fires">
              <div v-for="next in row.next" :key="next">{{ time(next) }}</div>
              <span v-if="!row.enabled">disabled</span>
            </td>
            <td class="nowrap">{{ time(row.last_fired_at) || '—' }}</td>
            <td><span v-if="row.last_status" class="badge" :class="row.last_status">{{ row.last_status }}</span></td>
            <td class="nowrap">
              <button class="small" @click="runNow(row)">Run now</button>
              <button class="small danger" @click="remove(row)">Delete</button>
            </td>
          </tr>
          <tr>
            <td></td>
            <td>
              <input v-model="adding.task" type="text" list="task-names" placeholder="task" size="16" />
              <datalist id="task-names"><option v-for="t in tasks" :key="t" :value="t" /></datalist>
            </td>
            <td><input v-model="adding.cron" type="text" class="mono" size="16" placeholder="0 12 * * *" /></td>
            <td><input v-model="adding.timezone" type="text" size="16" placeholder="local" /></td>
            <td>
              <select v-model="adding.misfire">
                <option value="skip">skip</option>
                <option value="run-once">run once</option>
              </select>
            </td>
            <td colspan="3" class="muted">minute hour day-of-month month day-of-week</td>
            <td><button class="small primary" @click="add">Add</button></td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
