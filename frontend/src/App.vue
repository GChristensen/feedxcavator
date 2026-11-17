<script setup>
import { onMounted, onUnmounted, provide, reactive, ref } from 'vue'
import { api } from './api'
import { icon } from './outline'
import WorkspaceTab from './components/WorkspaceTab.vue'
import TasksTab from './components/TasksTab.vue'
import SchedulesTab from './components/SchedulesTab.vue'
import RunsTab from './components/RunsTab.vue'
import SubscriptionsTab from './components/SubscriptionsTab.vue'
import WordFiltersTab from './components/WordFiltersTab.vue'
import LogTab from './components/LogTab.vue'
import SettingsTab from './components/SettingsTab.vue'
import ApiTab from './components/ApiTab.vue'
import QuickOpen from './components/QuickOpen.vue'

// UI2-2: Workspace, Schedules, Runs, Log, Settings, API, plus Tasks (TSK2-1) and Word filters (STO2-9)
const TABS = [
  { id: 'workspace', title: 'Workspace' },
  { id: 'tasks', title: 'Tasks' },
  { id: 'schedules', title: 'Schedules' },
  { id: 'runs', title: 'Runs' },
  { id: 'subscriptions', title: 'Subscriptions' },
  { id: 'wordfilters', title: 'Word filters' },
  { id: 'log', title: 'Log' },
  { id: 'settings', title: 'Settings' },
  { id: 'api', title: 'API' },
]

function tabFromHash() {
  const id = window.location.hash.replace(/^#/, '').split('/')[0]
  return TABS.some((t) => t.id === id) ? id : 'workspace' // UI-2
}

const current = ref(tabFromHash())
const visited = reactive(new Set([current.value])) // UI-1: a tab is built when first shown
const workspace = ref(null)
const wordfilters = ref(null)
const quickOpen = ref(false)

// UI-3: the settings and the task list are fetched on load and kept for later use
const store = reactive({
  settings: {},
  tasks: [],
  load: null,
  async refreshSettings() {
    store.settings = await api.get('/settings')
  },
  async refreshTasks() {
    store.tasks = await api.get('/tasks')
  },
  setLoad(load) {
    if (load) store.load = load
  },
  openFile(path, line) {
    select('workspace')
    setTimeout(() => workspace.value && workspace.value.open(path, line), 0)
  },
})
provide('store', store)

function select(id) {
  if (current.value === 'workspace' && id !== 'workspace') {
    // leaving is fine: open files keep their unsaved state
  }
  current.value = id
  visited.add(id)
  if (window.location.hash.replace(/^#/, '') !== id) history.replaceState(null, '', '#' + id)
}

function onHash() {
  select(tabFromHash())
}

function showErrors() {
  if (store.load && !store.load.ok) {
    select('workspace')
    setTimeout(() => workspace.value && workspace.value.showLoadErrors(), 0)
  }
}

function pickQuick(path) {
  quickOpen.value = false
  store.openFile(path)
}

function onKeydown(event) {
  // Ctrl K (Cmd K) opens "Go to feed or file"; the capture phase runs before the Ace editor sees the key
  if ((event.ctrlKey || event.metaKey) && !event.altKey && !event.shiftKey && event.key.toLowerCase() === 'k') {
    event.preventDefault()
    event.stopPropagation()
    quickOpen.value = !quickOpen.value
  }
}

function onBeforeUnload(event) {
  if ((workspace.value && workspace.value.hasUnsaved()) || (wordfilters.value && wordfilters.value.hasUnsaved())) {
    event.preventDefault()
    event.returnValue = ''
  }
}

onMounted(async () => {
  window.addEventListener('hashchange', onHash)
  window.addEventListener('beforeunload', onBeforeUnload)
  window.addEventListener('keydown', onKeydown, true)
  try {
    await Promise.all([store.refreshSettings(), store.refreshTasks()])
    store.load = await api.get('/load-status')
  } catch (e) {
    console.error(e)
  }
})
onUnmounted(() => {
  window.removeEventListener('hashchange', onHash)
  window.removeEventListener('beforeunload', onBeforeUnload)
  window.removeEventListener('keydown', onKeydown, true)
})
</script>

<template>
  <div class="app">
    <header class="appbar">
      <div class="brand">
        <img :src="icon('../logo.png')" alt="" />
        <h1>Feedxcavator</h1>
      </div>
      <nav class="tabs" aria-label="Sections">
        <a
          v-for="tab in TABS" :key="tab.id" class="tab" :class="{ selected: current === tab.id }" :href="'#' + tab.id"
          :aria-current="current === tab.id ? 'page' : null" @click.prevent="select(tab.id)"
        >{{ tab.title }}</a>
      </nav>
      <span class="spacer"></span>
      <button type="button" class="quick-open-button" title="Go to feed or file (Ctrl K)" @click="quickOpen = true">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">
          <circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" />
        </svg>
        <span class="text">Go to feed or file…</span>
        <kbd>Ctrl K</kbd>
      </button>
      <button
        v-if="store.load && !store.load.ok" type="button" class="load-state bad" title="Show the load errors" @click="showErrors"
      >
        <span class="dot"></span>{{ store.load.errors.length }} load error{{ store.load.errors.length === 1 ? '' : 's' }}
      </button>
      <span v-else-if="store.load" class="load-state good" title="The workspace loaded without errors">
        <span class="dot"></span>Loaded
      </span>
      <a v-if="store.settings.authentication" class="icon-button" href="/logout" title="Log out" aria-label="Log out">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" /><path d="m16 17 5-5-5-5" /><path d="M21 12H9" />
        </svg>
      </a>
    </header>
    <div class="tab-content">
      <WorkspaceTab v-if="visited.has('workspace')" v-show="current === 'workspace'" ref="workspace" />
      <TasksTab v-if="visited.has('tasks')" v-show="current === 'tasks'" :active="current === 'tasks'" />
      <SchedulesTab v-if="visited.has('schedules')" v-show="current === 'schedules'" :active="current === 'schedules'" />
      <RunsTab v-if="visited.has('runs')" v-show="current === 'runs'" :active="current === 'runs'" />
      <SubscriptionsTab v-if="visited.has('subscriptions')" v-show="current === 'subscriptions'"
                        :active="current === 'subscriptions'" />
      <WordFiltersTab v-if="visited.has('wordfilters')" v-show="current === 'wordfilters'" ref="wordfilters"
                      :active="current === 'wordfilters'" />
      <LogTab v-if="visited.has('log')" v-show="current === 'log'" :active="current === 'log'" />
      <SettingsTab v-if="visited.has('settings')" v-show="current === 'settings'" />
      <ApiTab v-if="visited.has('api')" v-show="current === 'api'" />
    </div>
    <QuickOpen :open="quickOpen" @close="quickOpen = false" @pick="pickQuick" />
  </div>
</template>
