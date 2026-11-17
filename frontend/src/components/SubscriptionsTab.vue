<script setup>
// The Subscriptions tab: WebSub subscriptions with editable callback URLs and a bulk replace in them.
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { api, describeError } from '../api'
import { formatTime } from '../time'

const props = defineProps({ active: Boolean })

const subs = ref([])
const error = ref('')
const message = ref('')
const drafts = reactive({}) // id -> edited callback
const filter = ref('')
const replace = reactive({ find: '', replace: '', regex: false, ignore_case: false })
const preview = ref(null) // the dry-run result

const shown = computed(() => {
  const q = filter.value.trim().toLowerCase()
  if (!q) return subs.value
  return subs.value.filter((s) => [s.title, s.suffix, s.feed_id, s.callback, s.topic]
    .some((v) => v && v.toLowerCase().includes(q)))
})

async function refresh() {
  try {
    subs.value = await api.get('/subscriptions')
    for (const key of Object.keys(drafts)) delete drafts[key]
    error.value = ''
  } catch (e) {
    error.value = describeError(e)
  }
}

function draft(sub) {
  return sub.id in drafts ? drafts[sub.id] : sub.callback
}

function edited(sub) {
  return sub.id in drafts && drafts[sub.id].trim() !== sub.callback
}

async function save(sub) {
  if (!edited(sub)) {
    delete drafts[sub.id]
    return
  }
  try {
    const updated = await api.put(`/subscriptions/${sub.id}`, { callback: drafts[sub.id] })
    Object.assign(sub, updated)
    delete drafts[sub.id]
    error.value = ''
    message.value = 'Callback saved.'
  } catch (e) {
    error.value = describeError(e)
  }
}

function revert(sub) {
  delete drafts[sub.id]
}

async function remove(sub) {
  if (!confirm(`Delete the subscription of ${sub.callback}?`)) return
  try {
    await api.delete(`/subscriptions/${sub.id}`)
    subs.value = subs.value.filter((s) => s.id !== sub.id)
    delete drafts[sub.id]
  } catch (e) {
    error.value = describeError(e)
  }
}

async function runReplace(dryRun) {
  try {
    const result = await api.post('/subscriptions/replace', { ...replace, dry_run: dryRun })
    error.value = ''
    if (dryRun) {
      preview.value = result
      message.value = ''
    } else {
      preview.value = null
      message.value = `Replaced in ${result.changed.length} callback${result.changed.length === 1 ? '' : 's'}` +
        (result.skipped.length ? `, ${result.skipped.length} skipped.` : '.')
      await refresh()
    }
  } catch (e) {
    error.value = describeError(e)
  }
}

watch(() => ({ ...replace }), () => { preview.value = null })

function feedName(sub) {
  return sub.title || sub.suffix || sub.feed_id
}

function time(iso) {
  return iso ? formatTime(iso) : ''
}

onMounted(refresh)
watch(() => props.active, (active) => { if (active) refresh() })
</script>

<template>
  <div class="tab-pane">
    <div class="toolbar">
      <button @click="refresh">Refresh</button>
      <input v-model="filter" type="search" placeholder="Filter" style="width: 180px" />
      <span class="sep"></span>
      <input v-model="replace.find" type="text" placeholder="Find in callbacks" style="width: 220px"
             @keydown.enter="runReplace(true)" />
      <input v-model="replace.replace" type="text" placeholder="Replace with" style="width: 220px"
             @keydown.enter="runReplace(true)" />
      <label title="Regular expression; \1 or \g<name> in the replacement refer to groups">
        <input v-model="replace.regex" type="checkbox" /> regex</label>
      <label><input v-model="replace.ignore_case" type="checkbox" /> ignore case</label>
      <button :disabled="!replace.find" @click="runReplace(true)">Preview</button>
      <button :disabled="!preview || !preview.changed.length" @click="runReplace(false)">Replace all</button>
      <span class="hint">{{ message }}</span>
      <span class="hint error-text">{{ error }}</span>
    </div>
    <div class="scroll">
      <div v-if="preview" class="replace-preview">
        <div class="muted">
          {{ preview.changed.length }} callback{{ preview.changed.length === 1 ? '' : 's' }} will change<span
            v-if="preview.skipped.length">, {{ preview.skipped.length }} skipped</span>.
          <button class="small" @click="preview = null">Close</button>
        </div>
        <table v-if="preview.changed.length || preview.skipped.length" class="grid">
          <tbody>
            <tr v-for="c in preview.changed" :key="'c' + c.id">
              <td class="mono">{{ c.feed_id }}</td><td class="mono">{{ c.old }}</td><td>→</td>
              <td class="mono">{{ c.new }}</td><td></td>
            </tr>
            <tr v-for="c in preview.skipped" :key="'s' + c.id" class="zero">
              <td class="mono">{{ c.feed_id }}</td><td class="mono">{{ c.old }}</td><td>→</td>
              <td class="mono">{{ c.new }}</td><td class="muted">skipped: {{ c.reason }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <table class="grid">
        <thead>
          <tr><th>Feed</th><th>Callback</th><th>Status</th><th>Expires</th><th>Created</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-for="sub in shown" :key="sub.id">
            <td :title="sub.topic">
              {{ feedName(sub) }}
              <span v-if="!sub.title && !sub.suffix" class="badge skipped">no such feed</span>
            </td>
            <td style="width: 55%">
              <input class="callback-input mono" type="text" :class="{ edited: edited(sub) }" :value="draft(sub)"
                     @input="drafts[sub.id] = $event.target.value" @keydown.enter="save(sub)"
                     @keydown.esc="revert(sub)" />
            </td>
            <td><span class="badge" :class="sub.verified ? 'ok' : 'running'">{{ sub.verified ? 'verified' : 'pending' }}</span></td>
            <td class="nowrap">{{ time(sub.expires_at) }}</td>
            <td class="nowrap">{{ time(sub.created_at) }}</td>
            <td class="nowrap">
              <button v-if="edited(sub)" class="small" @click="save(sub)">Save</button>
              <button v-if="edited(sub)" class="small" @click="revert(sub)">Revert</button>
              <button class="small danger" @click="remove(sub)">Delete</button>
            </td>
          </tr>
          <tr v-if="!shown.length"><td colspan="6" class="muted">
            {{ subs.length ? 'No subscriptions match the filter.' : 'No WebSub subscriptions.' }}</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
