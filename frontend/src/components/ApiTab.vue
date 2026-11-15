<script setup>
// The API tab (UI-19, UC2-21): generated from the docstrings of the user library.
import { computed, onMounted, ref } from 'vue'
import { api, describeError } from '../api'

const sections = ref([])
const filter = ref('')
const error = ref('')

const visible = computed(() => {
  const needle = filter.value.trim().toLowerCase()
  if (!needle) return sections.value
  return sections.value
    .map((section) => ({
      ...section,
      entries: section.entries.filter((e) => (e.name + ' ' + e.hy + ' ' + e.doc).toLowerCase().includes(needle)),
    }))
    .filter((section) => section.entries.length)
})

onMounted(async () => {
  try {
    sections.value = (await api.get('/reference')).sections
  } catch (e) {
    error.value = describeError(e)
  }
})
</script>

<template>
  <div class="tab-pane padded reference">
    <div class="search">
      <input v-model="filter" type="text" placeholder="Filter the reference…" />
      <span class="error-text"> {{ error }}</span>
    </div>
    <p class="muted">
      Everything below is available in every workspace file without imports. Python names use snake_case;
      in Hy the same names are written with dashes. Files load in this order: the prelude, <code>lib/</code>,
      the code of each feed, <code>handlers/</code>, then <code>tasks.yaml</code> and <code>schedules.yaml</code>.
    </p>
    <div v-for="section in visible" :key="section.category">
      <h2>{{ section.category }}</h2>
      <div v-for="entry in section.entries" :key="entry.name" class="entry">
        <div class="sig">
          {{ entry.name }}{{ entry.signature }}
          <span v-if="entry.hy !== entry.name && section.category !== 'Hy macros'" class="hy">{{ entry.hy }}</span>
        </div>
        <div class="doc">{{ entry.doc }}</div>
      </div>
    </div>
  </div>
</template>
