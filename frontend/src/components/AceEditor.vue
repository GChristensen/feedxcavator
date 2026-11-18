<script setup>
// Ace wrapped in a Vue component. The parent owns the edit sessions, one per open file,
// so every file keeps its own undo history.
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { createEditor, createSession } from '../ace'

const props = defineProps({
  session: { type: Object, default: null },
  readOnly: { type: Boolean, default: false },
})
const emit = defineEmits(['save'])

const host = ref(null)
let editor = null
let observer = null
const empty = createSession('', 'text')

onMounted(() => {
  editor = createEditor(host.value, { readOnly: props.readOnly })
  editor.setSession(props.session || empty)
  // Ctrl+S or Cmd+S saves, and the browser's own save dialog is suppressed
  editor.commands.addCommand({
    name: 'save',
    bindKey: { win: 'Ctrl-S', mac: 'Command-S' },
    exec: () => emit('save'),
    readOnly: true,
  })
  observer = new ResizeObserver(() => editor.resize())
  observer.observe(host.value)
})

watch(() => props.session, (session) => {
  if (editor) {
    editor.setSession(session || empty)
    if (!props.readOnly) editor.focus()
  }
})

onBeforeUnmount(() => {
  if (observer) observer.disconnect()
  if (editor) editor.destroy()
})

defineExpose({
  gotoLine(line) {
    if (!editor) return
    editor.gotoLine(line, 0, true)
    editor.focus()
  },
  focus: () => editor && editor.focus(),
  scrollToEnd() {
    if (editor) editor.gotoLine(editor.session.getLength(), 0, false)
  },
  scrollToTop() {
    if (editor) editor.gotoLine(1, 0, false)
  },
})
</script>

<template>
  <div ref="host" class="ace-host"></div>
</template>
