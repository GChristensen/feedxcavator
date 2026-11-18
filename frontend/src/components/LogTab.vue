<script setup>
// The Log tab: the application log, and the server log on request.
import { nextTick, onMounted, reactive, ref, watch } from 'vue'
import { api, describeError } from '../api'
import { formatTime } from '../time'
import { icon } from '../outline'

const props = defineProps({ active: Boolean })

const entries = ref([])
const expanded = reactive(new Set())
const wrapped = reactive(new Set())
const serverLog = ref(null)
const error = ref('')
const list = ref(null)
let loading = false
let exhausted = false

async function refresh() {
  serverLog.value = null
  exhausted = false
  try {
    entries.value = await api.get('/log', { n: 20 }) // the newest 20
    error.value = ''
  } catch (e) {
    error.value = describeError(e)
  }
}

async function more() {
  if (loading || exhausted || serverLog.value !== null || !entries.value.length) return
  loading = true
  try {
    const last = entries.value[entries.value.length - 1]
    const older = await api.get('/log', { n: 10, from: last.number - 1 }) // 10 more at the bottom
    if (!older.length) exhausted = true
    entries.value = entries.value.concat(older)
  } finally {
    loading = false
  }
}

function onScroll(event) {
  const el = event.target
  if (el.scrollTop + el.clientHeight >= el.scrollHeight - 40) more()
}

async function clear() {
  if (!confirm('Delete all log entries?')) return
  await api.delete('/log')
  await refresh()
}

async function showServerLog() {
  try {
    serverLog.value = (await api.get('/server-log')).content || '(log/app.log is empty)'
    await nextTick()
    if (list.value) list.value.scrollTop = list.value.scrollHeight // scrolled to the end
  } catch (e) {
    error.value = describeError(e)
  }
}

function toggle(set, number) {
  set.has(number) ? set.delete(number) : set.add(number)
}

function time(ms) {
  return formatTime(ms)
}

onMounted(refresh)
watch(() => props.active, (active) => active && serverLog.value === null && refresh())
</script>

<template>
  <div class="tab-pane">
    <div class="toolbar">
      <button @click="refresh">Refresh</button>
      <button class="danger" @click="clear">Clear</button>
      <button @click="showServerLog">Server Log</button>
      <span class="hint error-text">{{ error }}</span>
    </div>
    <div ref="list" class="scroll" @scroll="onScroll">
      <pre v-if="serverLog !== null" class="server-log">{{ serverLog }}</pre>
      <template v-else>
        <div v-for="entry in entries" :key="entry.number" class="log-entry">
          <div class="head" @click="toggle(expanded, entry.number)">
            <img :src="icon(entry.level + '.svg')" :alt="entry.level" :title="entry.level" />
            <span class="nowrap">{{ time(entry.timestamp) }}</span>
            <span v-if="entry.source" class="source">{{ entry.source }}</span>
            <span class="links">
              <span title="Expand or collapse">[...]</span>&nbsp;
              <span title="Toggle line wrapping" @click.stop="toggle(wrapped, entry.number)">[wrap]</span>
            </span>
          </div>
          <pre :class="{ collapsed: !expanded.has(entry.number), wrap: wrapped.has(entry.number) }">{{ entry.message }}</pre>
        </div>
        <div v-if="!entries.length" class="tab-pane padded muted">The log is empty.</div>
      </template>
    </div>
  </div>
</template>
