<script setup>
// The Settings tab (UI-18, with the changes of STO2-13, STO2-14 and STO2-15).
import { inject, onMounted, reactive, ref } from 'vue'
import { api, describeError } from '../api'

const store = inject('store')

const form = reactive({ subscription_url: '' })
const token = ref('')
const withWorkspace = ref(false)
const unpackWorkspace = ref(false)
const restoreFile = ref(null)
const notes = reactive({ custom: '', token: '', database: '', server: '' })

async function load() {
  await store.refreshSettings()
  form.subscription_url = store.settings.subscription_url || ''}

async function save(fields, where) {
  try {
    await api.put('/settings', fields)
    await store.refreshSettings()
    notes[where] = 'Saved.'
    setTimeout(() => (notes[where] = ''), 2000)
  } catch (error) {
    notes[where] = describeError(error)
  }
}

async function generate() {
  if (store.settings.token_created_at
      && !confirm('Generate a new API token? The current one stops working.')) return
  try {
    token.value = (await api.post('/token')).token
    await store.refreshSettings()
    notes.token = 'Copy the token now: it is shown only once.'
  } catch (error) {
    notes.token = describeError(error)
  }
}

async function restore() {
  const file = restoreFile.value && restoreFile.value.files[0]
  if (!file) {
    notes.database = 'Choose a backup file first.'
    return
  }
  const data = new FormData()
  data.append('file', file)
  try {
    const result = await api.upload('/restore', data, { unpack_workspace: unpackWorkspace.value })
    const counts = Object.entries(result.records).map(([name, n]) => `${n} ${name}`).join(', ')
    let text = `Restored: ${counts}.`
    if (result.has_workspace) {
      if (result.workspace_unpacked) text += ' The workspace was unpacked.'
      else if (result.workspace_error) text += ` The workspace was not unpacked: ${result.workspace_error}.`
      else text += ' The archive has a workspace; tick the box to unpack it into an empty workspace directory.'
    }
    notes.database = text
    store.setLoad(result.load)
    await load()
  } catch (error) {
    notes.database = describeError(error)
  }
}

async function purge() {
  if (!confirm('Delete all stored images?')) return
  try {
    const result = await api.post('/purge-images')
    notes.database = `Deleted ${result.deleted} image${result.deleted === 1 ? '' : 's'}.`
    await store.refreshSettings()
  } catch (error) {
    notes.database = describeError(error)
  }
}

async function restart() {
  if (!confirm('Restart the server?')) return
  try {
    await api.post('/restart')
  } catch (error) {
    notes.server = describeError(error)
    return
  }
  notes.server = 'Restarting…'
  // poll /login once a second and reload the page when the server answers
  const timer = setInterval(async () => {
    try {
      const response = await fetch('/login', { cache: 'no-store' })
      if (response.ok) {
        clearInterval(timer)
        window.location.reload()
      }
    } catch { /* still down */ }
  }, 1000)
}

function time(iso) {
  return iso ? new Date(iso).toLocaleString() : ''
}

onMounted(load)
</script>

<template>
  <div class="tab-pane padded settings">
    <fieldset>
      <legend>Customization</legend>
      <div class="row">
        <label for="subscription-url">Subscription URL</label>
        <input id="subscription-url" v-model="form.subscription_url" type="text" placeholder="https://newsblur/?url="
               @blur="save({ subscription_url: form.subscription_url }, 'custom')" />
        <span class="note">{{ notes.custom }}</span>
      </div>
      <div class="note">"Subscribe" opens this address with the encoded feed URL appended.</div>
    </fieldset>

    <fieldset>
      <legend>Authentication</legend>
      <div class="row">
        <label>API token</label>
        <span v-if="token" class="token">{{ token }}</span>
        <span v-else-if="store.settings.token_created_at" class="muted">
          generated {{ time(store.settings.token_created_at) }} (stored hashed, it cannot be shown again)
        </span>
        <span v-else class="muted">not generated</span>
        <button @click="generate">Generate</button>
      </div>
      <div class="note">{{ notes.token || 'Sent in the x-feedxcavator-auth header to the word filter API, /api/images/save and handlers that require it.' }}</div>
    </fieldset>

    <fieldset>
      <legend>Database</legend>
      <div class="row">
        <label>Backup</label>
        <a class="button" :href="'/front/api/backup?workspace=' + withWorkspace">Download backup</a>
        <label style="min-width: 0"><input v-model="withWorkspace" type="checkbox" /> include the workspace</label>
      </div>
      <div class="row">
        <label>Restore</label>
        <input ref="restoreFile" type="file" accept=".zip" />
        <button @click="restore">Load</button>
        <label style="min-width: 0" :title="store.settings.workspace_empty ? '' : 'The workspace directory is not empty'">
          <input v-model="unpackWorkspace" type="checkbox" :disabled="!store.settings.workspace_empty" />
          unpack the workspace of the archive
        </label>
      </div>
      <div class="row">
        <label>Stored images</label>
        <span>{{ store.settings.images ?? 0 }}</span>
        <button class="danger" @click="purge">Purge</button>
      </div>
      <div class="note">{{ notes.database || 'Feed definitions and code live in the workspace directory, not in the database.' }}</div>
    </fieldset>

    <fieldset>
      <legend>Server</legend>
      <div class="row">
        <button :disabled="!store.settings.restartable" @click="restart">Restart</button>
        <span v-if="!store.settings.restartable" class="note">
          Restart is available only when Feedxcavator is started by feedxcavator.cmd, feedxcavator.sh or the systemd service.
        </span>
        <span class="note">{{ notes.server }}</span>
      </div>
      <div class="row note">
        Worker: <span :class="store.settings.worker_alive ? '' : 'error-text'">
          {{ store.settings.worker_alive ? 'running' : 'not reachable (tasks, schedules, browser and Telegram are unavailable)' }}
        </span>
      </div>
      <div class="row note">Workspace: {{ store.settings.workspace }}</div>
    </fieldset>

    <div class="muted">Feedxcavator {{ store.settings.version }}</div>
  </div>
</template>
