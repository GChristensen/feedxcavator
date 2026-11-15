<script setup>
// "Go to feed or file" (Ctrl K): a filtered list of the feeds and files in the workspace tree.
import { computed, nextTick, ref, watch } from 'vue'
import { api } from '../api'

const props = defineProps({ open: { type: Boolean, default: false } })
const emit = defineEmits(['close', 'pick'])

const query = ref('')
const items = ref([])
const index = ref(0)
const input = ref(null)
const list = ref(null)

function flatten(nodes, out = []) {
  for (const node of nodes) {
    if (node.type === 'feed') {
      out.push({
        path: node.path + '/feed.yaml', label: (node.feed && node.feed.title) || node.name,
        detail: node.path, suffix: node.feed && node.feed.suffix, feed: true, kind: 'feed',
      })
    } else if (node.type === 'file') {
      const ext = node.name.match(/\.([^.]+)$/)
      out.push({ path: node.path, label: node.name, detail: node.path, feed: false, kind: ext ? ext[1].slice(0, 4) : '' })
    }
    if (node.children) flatten(node.children, out)
  }
  return out
}

function score(item, q) {
  if (!q) return 1
  const label = item.label.toLowerCase()
  const hay = (item.label + ' ' + item.detail + ' ' + (item.suffix || '')).toLowerCase()
  if (label.startsWith(q)) return 4
  if (label.includes(q)) return 3
  if (hay.includes(q)) return 2
  let i = 0
  for (const ch of hay) if (ch === q[i]) i++
  return i === q.length ? 1 : 0
}

const results = computed(() => {
  const q = query.value.trim().toLowerCase()
  return items.value
    .map((item) => ({ item, s: score(item, q) }))
    .filter((r) => r.s > 0)
    .sort((a, b) => b.s - a.s || Number(b.item.feed) - Number(a.item.feed) || a.item.label.localeCompare(b.item.label))
    .slice(0, 50)
    .map((r) => r.item)
})

watch(results, () => (index.value = 0))

watch(() => props.open, async (open) => {
  if (!open) return
  query.value = ''
  index.value = 0
  await nextTick()
  input.value && input.value.focus()
  try {
    items.value = flatten((await api.get('/tree')).tree)
  } catch (e) {
    console.error(e)
  }
})

function move(step) {
  const n = results.value.length
  if (!n) return
  index.value = (index.value + step + n) % n
  nextTick(() => {
    const row = list.value && list.value.children[index.value]
    if (row) row.scrollIntoView({ block: 'nearest' })
  })
}

function choose(item) {
  if (item) emit('pick', item.path)
}

function onKey(event) {
  if (event.key === 'ArrowDown') { event.preventDefault(); move(1) }
  else if (event.key === 'ArrowUp') { event.preventDefault(); move(-1) }
  else if (event.key === 'Enter') { event.preventDefault(); choose(results.value[index.value]) }
  else if (event.key === 'Escape') { event.preventDefault(); emit('close') }
}
</script>

<template>
  <div v-if="open" class="quick-open-backdrop" @mousedown.self="emit('close')">
    <div class="quick-open" role="dialog" aria-label="Go to feed or file">
      <input ref="input" v-model="query" type="text" placeholder="Go to feed or file…" spellcheck="false" @keydown="onKey" />
      <div ref="list" class="results">
        <div
          v-for="(item, i) in results" :key="item.path" class="result" :class="{ active: i === index }"
          @mousemove="index = i" @click="choose(item)"
        >
          <span class="kind">{{ item.kind }}</span>
          <span class="label">{{ item.label }}</span>
          <span class="detail">{{ item.detail }}</span>
        </div>
        <div v-if="!results.length" class="empty">Nothing matches.</div>
      </div>
    </div>
  </div>
</template>
