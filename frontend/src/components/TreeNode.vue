<script setup>
// One node of the workspace tree (UI2-6, UI2-7). Feed directories are shown as feeds.
import { computed, inject } from 'vue'
import { icon } from '../outline'

const props = defineProps({
  node: { type: Object, required: true },
  depth: { type: Number, default: 0 },
})
const tree = inject('tree')

const isContainer = computed(() => props.node.type !== 'file')
const expanded = computed(() => tree.expanded.has(props.node.path))
const feed = computed(() => props.node.feed)
const label = computed(() => (feed.value ? feed.value.title || props.node.name : props.node.name))
const hasError = computed(() => {
  if (feed.value && feed.value.status !== 'ok') return true
  if (props.node.type === 'file') return tree.errorPaths.has(props.node.path)
  return [...tree.errorPaths].some((p) => p.startsWith(props.node.path + '/'))
})
const selected = computed(() => tree.selected === props.node.path
  || (feed.value && tree.selected === props.node.path + '/feed.yaml'))

const image = computed(() => {
  if (props.node.type === 'feed') return icon('feed.svg')
  if (props.node.type === 'dir') return icon('group.svg')
  if (props.node.path === 'tasks.yaml') return icon('task.svg')
  if (props.node.path === 'schedules.yaml') return icon('schedule.svg')
  return null
})
const extension = computed(() => {
  const match = props.node.name.match(/\.([^.]+)$/)
  return match ? match[1].slice(0, 4) : ''
})
const title = computed(() => {
  if (!feed.value) return props.node.path
  const lines = [props.node.path, `suffix: ${feed.value.suffix || ''}`]
  if (!feed.value.enabled) lines.push('disabled')
  if (feed.value.error) lines.push(feed.value.error)
  return lines.join('\n')
})

function click() {
  if (props.node.type === 'feed') {
    tree.open(props.node.path + '/feed.yaml')
    if (props.node.children.length && !expanded.value) tree.toggle(props.node.path)
  } else if (props.node.type === 'dir') {
    tree.toggle(props.node.path)
    tree.select(props.node.path)
  } else {
    tree.open(props.node.path)
  }
}

function twisty(event) {
  event.stopPropagation()
  tree.toggle(props.node.path)
}

function dragStart(event) {
  event.dataTransfer.setData('text/plain', props.node.path)
  event.dataTransfer.effectAllowed = 'move'
}

function dragOver(event) {
  if (props.node.type !== 'dir') return
  event.preventDefault()
  event.dataTransfer.dropEffect = 'move'
  tree.dropTarget = props.node.path
}

function drop(event) {
  if (props.node.type !== 'dir') return
  event.preventDefault()
  event.stopPropagation()
  tree.dropTarget = null
  const source = event.dataTransfer.getData('text/plain')
  if (source) tree.move(source, props.node.path)
}
</script>

<template>
  <div>
    <div
      class="tree-row"
      :class="{ selected, disabled: feed && !feed.enabled, drop: tree.dropTarget === node.path }"
      :style="{ paddingLeft: 6 + depth * 14 + 'px' }"
      :title="title"
      draggable="true"
      @click="click"
      @contextmenu.prevent.stop="tree.menu($event, node)"
      @dragstart="dragStart"
      @dragover="dragOver"
      @dragleave="tree.dropTarget = null"
      @drop="drop"
    >
      <span class="twisty" @click="twisty">
        <template v-if="isContainer && node.children.length">{{ expanded ? '▼' : '▶' }}</template>
      </span>
      <img v-if="image" :src="image" alt="" />
      <span v-else class="ext">{{ extension }}</span>
      <span class="label">{{ label }}</span>
      <span v-if="hasError" class="mark" title="failed to load">!</span>
    </div>
    <template v-if="isContainer && expanded">
      <TreeNode v-for="child in node.children" :key="child.path" :node="child" :depth="depth + 1" />
    </template>
  </div>
</template>
