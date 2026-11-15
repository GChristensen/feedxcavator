// A Hy mode for Ace, derived from the Clojure mode (UI2-4): Hy's special forms, the prelude macros,
// and `#` reader syntax.
import ace from 'ace-builds/src-noconflict/ace'
import 'ace-builds/src-noconflict/mode-clojure'

const SPECIAL_FORMS = [
  'annotate', 'assert', 'await', 'break', 'chainc', 'cond', 'continue', 'cut', 'defclass', 'defmacro', 'defn',
  'defreader', 'del', 'deftype', 'dfor', 'do', 'do-mac', 'eval-and-compile', 'eval-when-compile', 'export',
  'fn', 'for', 'get', 'gfor', 'global', 'if', 'import', 'let', 'lfor', 'local-macros', 'match', 'nonlocal',
  'py', 'pys', 'pragma', 'quasiquote', 'quote', 'raise', 'require', 'return', 'setv', 'setx', 'sfor', 'try',
  'unpack-iterable', 'unpack-mapping', 'unquote', 'unquote-splice', 'when', 'while', 'with', 'yield',
  'except', 'except*', 'else', 'finally', 'unless', 'and', 'or', 'not', 'in', 'not-in', 'is', 'is-not',
].join('|')

const PRELUDE_MACROS = [
  'defextractor', 'defhandler', 'defproxy', 'deftaskfn', 'defselectors', 'with-browser', 'log-fetch-errors',
  'safe-call', 'safely-repeat', 'safely-repeat3', '->', '->>', 'as->', 'some->', 'cond->', 'doto', '$', '$1',
].join('|')

const PRELUDE_FUNCTIONS = [
  'fetch-url', 'apply-selectors', 'parse-html-page', 'parse-pages', 'parse-page-range', 'produce-feed-output',
  'filter-history', 'filter-history!', 'filter-history-by-guid!', 'filter-content', 'filter-headlines',
  'store-object!', 'fetch-object', 'delete-object!', 'find-feed', 'text', 'attr', 'inner-html', 'outer-html',
  'extract', 'q', 'q1', 'parse-html', 'assoc', 'dissoc', 'update', 'get-in', 'assoc-in', 'update-in', 'merge',
  'select-keys', 'distinct-by', 'keep', 'mapcat', 'partition', 'partition-all', 'group-by', 'frequencies',
  'text-page', 'html-page', 'json-page', 'rss-page', 'redirect-to', 'fix-relative-url', 'redirect-url',
  'forward-url', 'get-feed-url', 'print', 'len', 'str', 'int', 'float', 'list', 'dict', 'set', 'tuple', 'range',
  'map', 'filter', 'sorted', 'reversed', 'enumerate', 'zip', 'isinstance', 'getattr', 'setattr', 'hasattr',
].join('|')

const SYMBOL_START = '[^\\s()\\[\\]{}"\';`~#:]'
const SYMBOL_REST = '[^\\s()\\[\\]{}"\';]*'

ace.define('ace/mode/hy_highlight_rules', ['require', 'exports', 'ace/lib/oop', 'ace/mode/text_highlight_rules'],
  function (require, exports) {
    const oop = require('ace/lib/oop')
    const TextHighlightRules = require('ace/mode/text_highlight_rules').TextHighlightRules

    const HyHighlightRules = function () {
      const keywordMapper = this.createKeywordMapper({
        keyword: SPECIAL_FORMS,
        'support.function': PRELUDE_MACROS + '|' + PRELUDE_FUNCTIONS,
        'constant.language': 'True|False|None|Ellipsis',
      }, 'identifier', false, '|')

      this.$rules = {
        start: [
          { token: 'comment', regex: ';.*$' },
          { token: 'comment', regex: '#_' },
          { token: 'keyword.operator', regex: '#\\*\\*|#\\*|#\\^|#@|[`\'~]@?' },
          { token: 'paren.lparen', regex: '#\\(' },
          { token: 'paren.lparen', regex: '#\\{' },
          { token: 'string', regex: '#\\[\\[', next: 'bracket_string' },
          { token: 'paren.lparen', regex: '[\\[({]' },
          { token: 'paren.rparen', regex: '[\\])}]' },
          { token: 'constant.numeric', regex: '[+-]?(?:0[xX][0-9a-fA-F_]+|0[oO][0-7_]+|0[bB][01_]+)\\b' },
          { token: 'constant.numeric', regex: '[+-]?\\d[\\d_]*(?:\\.\\d[\\d_]*)?(?:[eE][+-]?\\d+)?[jJ]?(?=[\\s)\\]}]|$)' },
          { token: 'string', regex: '[rbf]*"', next: 'string' },
          { token: 'variable.parameter', regex: '\\^:[\\w-]+' },
          { token: 'constant.other.keyword', regex: ':' + SYMBOL_REST },
          { token: keywordMapper, regex: SYMBOL_START + SYMBOL_REST },
        ],
        string: [
          { token: 'constant.language.escape', regex: '\\\\.|\\\\$' },
          { token: 'string', regex: '"', next: 'start' },
          { defaultToken: 'string' },
        ],
        bracket_string: [
          { token: 'string', regex: '\\]\\]', next: 'start' },
          { defaultToken: 'string' },
        ],
      }
      this.normalizeRules()
    }
    oop.inherits(HyHighlightRules, TextHighlightRules)
    exports.HyHighlightRules = HyHighlightRules
  })

ace.define('ace/mode/hy', ['require', 'exports', 'ace/lib/oop', 'ace/mode/clojure', 'ace/mode/hy_highlight_rules'],
  function (require, exports) {
    const oop = require('ace/lib/oop')
    const ClojureMode = require('ace/mode/clojure').Mode
    const HyHighlightRules = require('ace/mode/hy_highlight_rules').HyHighlightRules

    const Mode = function () {
      ClojureMode.call(this)
      this.HighlightRules = HyHighlightRules
    }
    oop.inherits(Mode, ClojureMode)
    Mode.prototype.lineCommentStart = ';'
    Mode.prototype.$id = 'ace/mode/hy'
    exports.Mode = Mode
  })
