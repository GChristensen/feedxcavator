<script setup>
// The Manual tab: feedxcavator/manual.md, rendered by the server, with a table of contents.
import { nextTick, onMounted, ref } from 'vue'
import { api, describeError } from '../api'

const html = ref('')
const toc = ref([])
const error = ref('')
const content = ref(null)
const current = ref('')

function target(id) {
  return id && content.value ? content.value.querySelector('#' + CSS.escape(id)) : null
}

function go(id) {
  const element = target(id)
  if (!element) return false
  element.scrollIntoView({ block: 'start' })
  current.value = id
  history.replaceState(null, '', '#manual/' + id)
  return true
}

// links inside the manual point to its headings; the console uses the hash for tabs, so they are handled here
function onClick(event) {
  const link = event.target.closest('a')
  if (!link) return
  const href = link.getAttribute('href') || ''
  if (href.startsWith('#') && go(decodeURIComponent(href.slice(1)))) {
    event.preventDefault()
  } else if (/^https?:/.test(href)) {
    link.target = '_blank'
    link.rel = 'noopener'
  }
}

onMounted(async () => {
  try {
    const result = await api.get('/manual')
    toc.value = result.toc.length === 1 ? result.toc[0].children : result.toc
    html.value = result.html
    await nextTick()
    const [tab, id] = window.location.hash.replace(/^#/, '').split('/')
    if (tab === 'manual' && id) go(decodeURIComponent(id))
  } catch (e) {
    error.value = describeError(e)
  }
})
</script>

<template>
  <div class="tab-pane manual">
    <nav class="manual-toc" aria-label="Contents">
      <template v-for="entry in toc" :key="entry.id">
        <a :href="'#manual/' + entry.id" :class="{ selected: current === entry.id }"
           @click.prevent="go(entry.id)">{{ entry.name }}</a>
        <a v-for="sub in entry.children" :key="sub.id" class="sub" :href="'#manual/' + sub.id"
           :class="{ selected: current === sub.id }" @click.prevent="go(sub.id)">{{ sub.name }}</a>
      </template>
    </nav>
    <div class="manual-body">
      <span class="error-text">{{ error }}</span>
      <!-- the server renders its own manual.md, so the HTML is trusted -->
      <article ref="content" class="manual-text" @click="onClick" v-html="html"></article>
    </div>
  </div>
</template>
