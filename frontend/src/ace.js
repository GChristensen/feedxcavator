// Ace setup (D-5, UI2-3): the current 1.x release with YAML, Python, Hy, XML and JSON modes.
import ace from 'ace-builds/src-noconflict/ace'
import 'ace-builds/src-noconflict/theme-monokai'
import 'ace-builds/src-noconflict/mode-yaml'
import 'ace-builds/src-noconflict/mode-python'
import 'ace-builds/src-noconflict/mode-clojure'
import 'ace-builds/src-noconflict/mode-xml'
import 'ace-builds/src-noconflict/mode-json'
import 'ace-builds/src-noconflict/mode-markdown'
import 'ace-builds/src-noconflict/mode-text'
import 'ace-builds/src-noconflict/ext-searchbox'
import './mode-hy'

ace.config.set('useWorker', false)

export const MODES = {
  yaml: 'ace/mode/yaml',
  python: 'ace/mode/python',
  hy: 'ace/mode/hy',
  clojure: 'ace/mode/clojure',
  xml: 'ace/mode/xml',
  json: 'ace/mode/json',
  markdown: 'ace/mode/markdown',
  text: 'ace/mode/text',
}

export function createSession(content, lang) {
  const session = ace.createEditSession(content || '', MODES[lang] || MODES.text)
  session.setUseWorker(false)
  session.setTabSize(lang === 'python' ? 4 : 2) // UI-4: soft tabs
  session.setUseSoftTabs(true)
  session.setUndoManager(new ace.UndoManager())
  return session
}

export function createEditor(element, { readOnly = false } = {}) {
  const editor = ace.edit(element, {
    theme: 'ace/theme/monokai',
    fontSize: 14,
    showPrintMargin: false,
    readOnly,
    highlightActiveLine: !readOnly,
    scrollPastEnd: readOnly ? 0 : 0.3,
  })
  editor.renderer.setScrollMargin(4, 4)
  if (readOnly) {
    editor.renderer.$cursorLayer.element.style.display = 'none'
    editor.setHighlightGutterLine(false)
  }
  return editor
}

export default ace
