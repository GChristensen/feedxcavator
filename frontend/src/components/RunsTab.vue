<script setup>
// The Runs tab (UI2-9): recent task runs, expandable to their feed runs.
import { onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { api, describeError } from '../api'

const props = defineProps({ active: Boolean })

const runs = ref([])
const expanded = reactive(new Set())
const error = ref('')
const auto = ref(true)
let timer = null

async function refresh() {
  try {
    runs.value = await api.get('/runs', { limit: 100 })
    error.value = ''
  } catch (e) {
    error.value = describeError(e)
  }
}

async function more() {
  const last = runs.value[runs.value.length - 1]
  if (!last) return
  const older = await api.get('/runs', { limit: 100, before: last.id })
  runs.value = runs.value.concat(older)
}

function toggle(id) {
  expanded.has(id) ? expanded.delete(id) : expanded.add(id)
}

function duration(seconds) {
  if (seconds === null || seconds === undefined) return '…'
  if (seconds < 60) return seconds.toFixed(1) + 's'
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`
}

function time(iso) {
  return new Date(iso).toLocaleString()
}

function totals(run) {
  let feeds = 0
  let headlines = 0
  let problems = 0
  const walk = (r) => {
    for (const f of r.feed_runs) {
      feeds += 1
      headlines += f.headline_count || 0
      if (f.status !== 'ok' || f.zero) problems += 1
    }
    r.children.forEach(walk)
  }
  walk(run)
  return { feeds, headlines, problems }
}

function schedule() {
  clearInterval(timer)
  if (props.active && auto.value) timer = setInterval(refresh, 5000)
}

onMounted(() => {
  refresh()
  schedule()
})
onUnmounted(() => clearInterval(timer))
watch(() => [props.active, auto.value], () => {
  if (props.active) refresh()
  schedule()
})
</script>

<template>
  <div class="tab-pane">
    <div class="toolbar">
      <button @click="refresh">Refresh</button>
      <label><input v-model="auto" type="checkbox" /> refresh every 5 seconds</label>
      <span class="hint error-text">{{ error }}</span>
    </div>
    <div class="scroll">
      <table class="grid">
        <thead>
          <tr><th></th><th>Task</th><th>Status</th><th>Trigger</th><th>Started</th><th>Duration</th><th>Feeds</th>
            <th>Headlines</th><th></th></tr>
        </thead>
        <tbody>
          <template v-for="run in runs" :key="run.id">
            <tr class="clickable" @click="toggle(run.id)">
              <td class="muted">{{ expanded.has(run.id) ? '▼' : '▶' }}</td>
              <td class="mono">{{ run.task }}</td>
              <td><span class="badge" :class="run.status">{{ run.status }}</span></td>
              <td>{{ run.trigger }}</td>
              <td class="nowrap">{{ time(run.started_at) }}</td>
              <td>{{ duration(run.duration) }}</td>
              <td>{{ totals(run).feeds }}</td>
              <td>{{ totals(run).headlines }}</td>
              <td>
                <span v-if="totals(run).problems" class="badge zero">{{ totals(run).problems }} to check</span>
                <span v-if="run.error" class="error-text"> {{ run.error }}</span>
              </td>
            </tr>
            <tr v-if="expanded.has(run.id)">
              <td colspan="9">
                <RunDetails :run="run" :duration="duration" />
              </td>
            </tr>
          </template>
          <tr v-if="!runs.length"><td colspan="9" class="muted">No task runs yet.</td></tr>
        </tbody>
      </table>
      <div v-if="runs.length >= 100" style="padding: 8px"><button @click="more">Load older runs</button></div>
    </div>
  </div>
</template>

<script>
import { h } from 'vue'

// Feed runs of a task run, and of its subtasks below them.
const RunDetails = {
  name: 'RunDetails',
  props: { run: Object, duration: Function, nested: Boolean },
  render() {
    const run = this.run
    const rows = run.feed_runs.map((f) => h('tr', { class: { zero: f.zero } }, [
      h('td', f.title || f.feed_id),
      h('td', [h('span', { class: ['badge', f.status === 'ok' && f.zero ? 'zero' : f.status] }, f.status)]),
      h('td', f.headline_count === null ? '' : `${f.headline_count} headline${f.headline_count === 1 ? '' : 's'}`),
      h('td', f.http_status ? `HTTP ${f.http_status}` : ''),
      h('td', this.duration(f.duration)),
      h('td', { class: 'error-text' }, f.error || ''),
    ]))
    const children = run.children.map((child) => h('div', [
      h('div', { class: 'muted', style: 'margin-top: 6px' }, [
        `subtask ${child.task} `, h('span', { class: ['badge', child.status] }, child.status),
        child.error ? h('span', { class: 'error-text' }, ' ' + child.error) : null,
      ]),
      h(RunDetails, { run: child, duration: this.duration, nested: true }),
    ]))
    return h('div', { class: 'feed-runs' }, [
      rows.length ? h('table', { class: 'grid' }, [h('tbody', rows)])
        : (!children.length ? h('div', { class: 'muted' }, 'No feed jobs.') : null),
      ...children,
    ])
  },
}

export default { components: { RunDetails } }
</script>
