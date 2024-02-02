(ns feedxcavator.entity
  (:use clojure.tools.macro))

(def entities (atom {}))

(def magic-entities #{:*task :*task-feeds :history})

(defn magic-entity? [entity]
  (magic-entities entity))

(defmacro defentity [key & fields]
  (let [kebab-name# (name key)]
    `(do
       (defsymbolmacro ~(symbol (str kebab-name# "-fields")) (~@fields))
       (swap! entities assoc ~key (assoc {} ~@(mapcat #(vec [(keyword %) (meta %)]) fields))))))

(defentity :object ^:key uuid ^:clj object)

(defentity :settings ^:key kind enable-profiling subscription-url user-email)

(defentity :feed ^:key uuid title suffix source charset output group task parallel proxy timeout
           ^:clj selectors ^:clj pages ^:clj filter realtime extractor partition
           ^:clj params ^:clj _extra)

(defentity :feed-definition ^:key uuid ^:clj yaml)

(defentity :image ^:key uuid url content-type timestamp)
(defentity :blob ^:key uuid data content-type)
(defentity :text ^:key uuid data content-type)

(defentity :code ^:key type ^:clj code timestamp)

(defentity :auth-token ^:key kind token)

(defentity :subscription ^:key uuid name topic callback secret timestamp)

;(defentity :history ^:key uuid ^:clj items)

(defentity :sample ^:key uuid ^:clj data)

(defentity :word-filter ^:key id ^:clj words)

(defentity :log ^:key kind top-entry is-open)
(defentity :log-entry ^:key uuid number level source timestamp)
(defentity :log-message ^:key uuid ^:clj message)

