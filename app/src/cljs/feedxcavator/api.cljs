(ns feedxcavator.api
  (:require [dommy.core :refer-macros [sel sel1]]
    [dommy.core :as dommy]
    [crate.core :as crate]
    [goog.events :as events]
    [goog.dom :as dom]
    )
  (:import [goog.ui Component Toolbar ToolbarButton ToolbarSeparator]))

(def api-docs "The following namespaces and functions are available in the extractor code.

Namespaces:

  clojure.core.async :as async         ;; e.g. (async/chan)
  clojure.string :as str               ;; e.g. (str/includes? s \"my string\")
  clojure.data.json :as json           ;; e.g. (json/read-str s :key-fn keyword)
  clj-time.core :as time               ;; e.g. (time/date-time 1986 10 14)
  clj-time.format :as time-fmt         ;; e.g. (time-fmt/format :iso-date (zoned-date-time))
  net.cgrand.enlive-html :as enlive    ;; e.g. (enlive/select doc-tree [:p])
  etaoin.api :as browser               ;; e.g. (browser/go driver \"https://example.com\")
  etaoin.keys :as browser-keys         ;; e.g. browser-keys/home
  feedxcavator.reply :as reply         ;; e.g. (reply/web-page content-type content)
  feedxcavator.webdriver :as webdriver ;; e.g. (webdriver/config :firefox {...})
  feedxcavator.log :as log             ;; e.g. (log/write :error \"Error message\")
  feedxcavator.db :as db               ;; e.g. (db/store-object! \"object-uuid\" {:field \"value\"})

  Any other available namespace could be referred by its full name, e.g. (clojure.java.io/reader x).

Macros:

  (defextractor fun-name [feed] body)  ;; define a feed extractor function
  (defhandler fun-name [param1 param2 ...] body) ;; define HTTP a handler function
  (deftask* symbol-or-string subtasks on-completion) ;; define a composite task
  (schedule task hour min) ;; schedule a task daily at the given time
  (defproxy name params) ;; define a proxy, the keyword parameters are: :host, :port, :user, :password
  (log-fetch-errors body) ;; write all fetch errors to the Feedxcavator log
  (with-browser body) ;; execute browser defined with webdriver/config

Functions:

  (?* node-or-nodes selector) ;; select multiple nodes from an enlive HTML node
  (?1 node-or-nodes selector) ;; select the first node that matches the selector
  (?1a node-or-nodes selector) ;; return the map of attributes of the first node that matches the selector
  (?1c node-or-nodes selector) ;; return contained nodes from the first node that matches the selector
  (<t node-or-nodes) ;; return text content (without tags) of the given enlive nodes
  (<* node-or-nodes) ;; return the outer HTML of the given enlive nodes
  (api/html-unescape s) ;; unescape special HTML character entities such as &quot;

  (api/apply-selectors doc feed) ;; apply selectors from the feed definition to an enlive node and return headline maps
  (api/produce-feed-output feed headlines) ;; produce string representation of the feed from the given list of headline maps
  (api/parse-html-page feed url) ;; apply selectors from the feed definition to the page defined by url and return headline maps
  (api/parse-pages feed path n-pages :include-source :increment :parser :delay) parse several pages and return headline maps

  (api/filter-history headlines) ;; filter headlines by :link excluding ones that were previously seen by the api/filter-history! function
  (api/filter-history! headlines) ;; filter headlines by :link remembering headlines that were passed in the corresponding argument
  (api/filter-history-by-guid! headlines) ;; filter headlines by :guid

  (api/add-filter-word word-filter word) ;; add a word to the given wordfilter
  (api/remove-filter-word word-filter word) ;; remove a filter word
  (api/add-filter-regex word-filter word) ;; add a regex to the given wordfilter
  (api/remove-filter-regex word-filter word) ;; remove a filter regex

  (api/fetch-url url params) ;; fetch the given url, the parameters are:
    :method ;; keyword designating HTTP method of the request
    :headers ;; a map of HTTP headers
    :payload ;; string or byte payload
    :as
      :html ;; enlive HTML representation
      :xml ;; enlive XML representation
      :json ;; parsed JSON map
      :string ;; plain string
      :bytes ;; byte array
    :timeout ;; request timeout in milliseconds
    :proxy ;; string designating a proxy defined with defproxy
    :follow-redirects ;; boolean indicating that redirects should be followed
    :retry ;; retry a request in the case of a network error
    :async? ;; perform an async request
    :insecure? ;; ignore TSL errors
  (api/get-last-http-response) ;; return last ring response obtained by the fetch-url function
  (api/get-last-http-error) ;; return last http error
  (api/get-last-network-error) ;; return last network error exception

  (api/find-header response header) ;; get the value of the given header from a ring response
  (api/str->enlive str) ;; convert string to an enlive node
  (api/resp->enlive response) ;; convert ring response to an enlive node
  (api/resp->enlive-xml response) ;; convert ring response containing XML to an enlive node
  (api/resp->str response) ;; convert ring response to string

  (api/url-encode-utf8 url) ;; encode an UTF-8 url using java.net.URLEncoder
  (api/url-safe-base64enc str) ;; encode string to url-safe Base64
  (api/url-safe-base64dec srt) ;; decode string from url-safe Base64
  (api/base64enc str) ;; encode string to Base64
  (api/base64dec str) ;; decode string from Base64
  (api/base64dec->bytes str) ;; return byte representation of a Base64 string

  (api/fix-relative-url url base) ;; make a relative url absolute using the given base
  (api/redirect-url url) ;; returns a Feedxcavator URL that redirects to the given URL
  (api/redirect-url-b64 url) ;; returns a Feedxcavator URL that redirects to the given URL which is encoded as Base64
  (api/forward-url url referer) ;; returns a Feedxcavator URL that forwards the content of the given url with the given referer
  (api/forward-url-b64 url referer) ;; the same as above, but the given URL is encoded as Base64

  (api/md5 s) ;; compute MD5 digest of a string
  (api/generate-uuid) ;; generate a random UUID
  (api/generate-random byte-length) ;; generate a random byte array represented as string
  (api/timestamp) ;; return the current UNIX timestamp

  (api/distinct-by f col) ;; return distinct elements from the collection by the given key function f
  (api/pprint object) ;; clojure.pprint/pprint

  (api/extra feed field-keyword) ;; return a non-standard feed property that presents in the YAML feed definition

  (db/store-object! uuid object) ;; store a Clojure object in database
  (db/fetch-object uuid) ;; query a Clojure object from database
  (db/delete-object! uuid) ;; delete a Clojure object from database

  (db/find-feed field-keyword field-value) ;; query a feed definition by :suffix or :title
  (db/persist-feed! feed) ;; persist feed, currently only the value of the :params field is persisted
  (db/fetch-feed-output uuid) ;; retrieve the contents of a background feed with the given uuid
  (db/store-feed-output! uuid object) ;; tore the feed output object in the same format as returned by db/fetch-feed-output
  ")

  (defn construct-api-tab []
    (dommy/append! (sel1 :#tab-content)
                   (crate/html
                     [:div#api-panel.tab-panel {:style "display: none"}
                    [:div.main-editor-wrapper [:div.main-editor {:id (str "api-editor")}]]
                    ]))

  (let [editor (.edit js/ace (str "api-editor"))
        editor-session (.getSession editor)]

    (.setMode editor-session "ace/mode/clojure")
    (.setTheme editor "ace/theme/monokai")
    (.setShowPrintMargin editor false)
    (.setUseSoftTabs editor-session true)
    (.setTabSize editor-session 2)
    (.setReadOnly editor true)
    (.setValue editor api-docs 1))
 )

(defn show-api-tab []
  (when (not (sel1 :#api-panel))
    (construct-api-tab))
  (.hide (js/$ "#tab-content > div"))
  (.show (js/$ "#api-panel")))