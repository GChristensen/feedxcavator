;; The Hy DSL of the prelude (PORT-REQUIREMENTS section 6). These macros are required into every
;; user file. `_fx` is the feedxcavator.userlib module, placed in the user namespace by the prelude.

(import hy.models [Expression Symbol String List Keyword Dict])

(defn _fx-name [form]
  "The registered name of a definition: the symbol as written, or a string."
  (if (isinstance form String) (str form) (str form)))

(defmacro defextractor [name params #* body]
  `(do
     (defn ~name ~params ~@body)
     ((_fx.extractor ~(_fx-name name)) ~name)))

(defmacro deftaskfn [name params #* body]
  `(do
     (defn ~name ~params ~@body)
     ((_fx.task-function ~(_fx-name name)) ~name)))

(defmacro defhandler [#* forms]
  ;; (defhandler name [a b] ...), (defhandler ^:auth name [a b] ...), (defhandler name request ...)
  (setv forms (list forms)
        auth False)
  (when (and forms (isinstance (get forms 0) Symbol) (= (str (get forms 0)) "^:auth"))
    (setv auth True
          forms (cut forms 1 None)))
  (when (and forms (isinstance (get forms 0) Keyword) (= (. (get forms 0) name) "auth"))
    (setv auth True
          forms (cut forms 1 None)))
  (when (< (len forms) 2)
    (raise (SyntaxError "defhandler expects a name and a parameter list")))
  (setv name (get forms 0)
        params (get forms 1)
        body (cut forms 2 None))
  (when (isinstance params Symbol)
    (setv params (List [params])))
  `(do
     (defn ~name ~params ~@body)
     ((_fx.handler ~(_fx-name name) :auth ~auth) ~name)))

(defmacro defproxy [name #* options]
  `(_fx.proxy ~(_fx-name name) ~@options))

(defmacro with-browser [#* forms]
  ;; (with-browser [b :profile "name"] body...) or (with-browser body...) binding `b`
  (setv forms (list forms)
        binding (Symbol "b")
        options [])
  (when (and forms (isinstance (get forms 0) List))
    (setv spec (list (get forms 0))
          forms (cut forms 1 None))
    (when spec
      (setv binding (get spec 0)
            options (cut spec 1 None))))
  `(with [~binding (_fx.browser ~@options)]
     ~@forms))

(defmacro log-fetch-errors [#* body]
  `(with [(_fx.fetch.log-fetch-errors)]
     ~@body))

(defmacro safe-call [form]
  `(_fx.safe-call (fn [] ~form)))

(defmacro safely-repeat [form]
  `(_fx.safely-repeat (fn [] ~form) 2))

(defmacro safely-repeat3 [form]
  `(_fx.safely-repeat (fn [] ~form) 3))

(defn _fx-selector [selector]
  "A literal selector is checked at expansion time (SEL-11); an Enlive vector becomes CSS (SEL-12)."
  (import feedxcavator.selectors :as sel)
  (cond
    (isinstance selector String)
      (do (sel.compile-selector (str selector)) selector)
    (isinstance selector List)
      (do
        (setv css (sel.enlive-to-css (lfor item selector
                                           (if (isinstance item Keyword) (. item name) (str item)))))
        (sel.compile-selector css)
        (String css))
    True selector))

(defmacro $ [node selector]
  `(_fx.sel.q ~node ~(_fx-selector selector)))

(defmacro $1 [node selector]
  `(_fx.sel.q1 ~node ~(_fx-selector selector)))

(defmacro defselectors [name #* parts]
  (when (% (len parts) 2)
    (raise (SyntaxError "defselectors expects pairs of a part name and a selector")))
  (setv pairs [])
  (for [i (range 0 (len parts) 2)]
    (setv key (get parts i)
          value (get parts (+ i 1)))
    (.append pairs (String (if (isinstance key Keyword) (. key name) (str key))))
    (.append pairs (if (isinstance value #(String List)) (_fx-selector value) value)))
  `(setv ~name (_fx.sel.SelectorSet ~(Dict pairs))))

(defmacro cond-> [expr #* clauses]
  ;; (cond-> x test1 (form1 ...) test2 (form2 ...)): threads x through the forms whose tests hold
  (when (% (len clauses) 2)
    (raise (SyntaxError "cond-> expects pairs of a test and a form")))
  (setv g (hy.gensym)
        steps [])
  (for [i (range 0 (len clauses) 2)]
    (setv test (get clauses i)
          form (get clauses (+ i 1))
          threaded (if (isinstance form Expression)
                       (Expression [(get form 0) g #* (cut form 1 None)])
                       (Expression [form g])))
    (.append steps `(setv ~g (if ~test ~threaded ~g))))
  `(do
     (setv ~g ~expr)
     ~@steps
     ~g))
