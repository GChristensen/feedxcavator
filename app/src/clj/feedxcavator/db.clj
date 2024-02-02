(ns feedxcavator.db
  (:require [clojure.java.io :as io]
            [clojure.string :as str]
            [feedxcavator.core :as core]
            [next.jdbc :as jdbc])
  (:use clojure.tools.macro
        [clojure.pprint :only [pprint]]
        feedxcavator.entity)
  (:import))

(def ^:const NS 'feedxcavator.db)
(def ^:const files-dir "./data/files/")

;; jdbc ;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;

(def db-spec (atom nil))

(def db-conn (let [config (core/read-config)
                   database-url (:database-connection-url config)]
               (when (and (:auto-backup-database config) (str/starts-with? database-url "jdbc:h2:"))
                 (let [database-file-match (re-find #"jdbc:h2:([^;]+)" database-url)
                       database-file (str (second database-file-match) ".mv.db")]
                   (core/copy-file-with-backup database-file)))
               (reset! db-spec (jdbc/get-datasource {:jdbcUrl database-url}))
               (if (:exclusive-jdbc-connection config)
                 (jdbc/get-connection @db-spec)
                 nil
                 )))

(defn get-connection []
  (or db-conn (jdbc/get-connection @db-spec)))

(defmacro with-db [& body]
  `(if (deref core/exclusive-jdbc-connection)
     (let [~'connection (get-connection)]
       ~@body)
     (with-open [~'connection (get-connection)]
       ~@body)))

(defn query-database
  ([sql-statement]
    (with-db
      (jdbc/execute! connection sql-statement {:builder-fn next.jdbc.result-set/as-unqualified-lower-maps})))
  ([sql-statement result-handler]
   (with-db
     (let [result (jdbc/execute! connection sql-statement {:builder-fn next.jdbc.result-set/as-unqualified-lower-maps})]
       (if (and result result-handler)
         (result-handler result)
         result)))))

(defn sql!
  ([sql-statement]
    (query-database sql-statement))
  ([sql-statement result-handler]
    (query-database sql-statement result-handler)))

(defn kind-from-kw [kw]
  (ns-resolve (find-ns NS) (symbol (core/kebab-to-snake (name kw)))))

(defn table-from-kind [kw]
  (str "`" (name kw) "`"))

(defn column-name [s]
  (str "`" s "`"))

(defn key-field [kind]
  (let [entity (@entities kind)]
    (ffirst (filter #(:key (second %)) entity))))

(extend-protocol next.jdbc.result-set/ReadableColumn
  java.sql.Clob
  (read-column-by-label [^java.sql.Clob v _]
    (with-open [rdr (.getCharacterStream v)] (slurp rdr)))
  (read-column-by-index [^java.sql.Clob v _2 _3]
    (with-open [rdr (.getCharacterStream v)] (slurp rdr))))

;; replace string representation of clojure types with objects
(defn read-rs-field [entity kv]
  (let [meta (entity (first kv))]
    (if (and (:clj meta) (second kv))
      [(first kv) (read-string (second kv))]
      kv)))

(defn rs-str->clj [kind r]
  (let [entity (@entities kind)]
    (into {} (map #(read-rs-field entity %) r))))

;; replace clojure objects with string representations
(defn print-rs-field [entity kv]
  (let [meta (entity (first kv))]
    (if (and (:clj meta) (second kv))
      [(first kv) (pr-str (second kv))]
      kv)))

(defn rs-clj->str [kind r]
  (let [entity (@entities kind)]
    (into {} (map #(print-rs-field entity %) r))))

(declare fetch-magic-entity)

(defn ->entity [kind rs]
  (map #(rs-str->clj kind %) rs))

(defn fetch
  ([kind]
   (if (magic-entity? kind)
     (fetch-magic-entity kind nil)
     (let [rs (sql! [(str "select * from " (table-from-kind kind))])]
       (->entity kind rs))))
  ([kind key & {:keys [result-handler]}]
   (if (magic-entity? kind)
     (fetch-magic-entity kind key)
     (let [key-field (-> kind key-field name)
          rs (sql! [(str "select * from " (table-from-kind kind) " where " key-field " = ?") key] result-handler)]
      (first (->entity kind rs))))))

(defn fetch-tasks []
  (sql! ["select distinct task from feed where not task is null"]))

(defn fetch-task-feeds [task-name]
  (let [feeds (sql! ["select * from feed where lower(task) = lower(?)" task-name])]
    (->entity :feed feeds)))

(defn filter-term->condition [filter]
  (let [names (map name filter)
        operator (first names)]
    (str "`" (second names) "` " (if (= operator "in") " = any(?)" (str operator " ?")))))

(defn filter->condition [filter]
  (if (list? (first filter))
    (let [terms (map filter-term->condition filter)]
      (str/join " and " terms))
    (filter-term->condition filter)))

(defn filter-term->query-args [filter]
  (let [operator (-> filter first name)]
    (if (= operator "in")
      (list 'into-array (last filter))
      (last filter))))

(defn filter->query-args [filter]
  (if (list? (first filter))
    (map filter-term->query-args filter)
    [(filter-term->query-args filter)]))

(defmacro query [kind filter]
  `(let [condition# (filter->condition '(~@filter))
         rs# (sql! [(str "select * from " (table-from-kind ~kind) " where " condition#)
                    ~@(filter->query-args filter)])]
     (->entity ~kind rs#)))

(defn ->db [kind obj]
  (rs-clj->str kind obj))

(defn store! [kind obj]
  (let [db-obj (->db kind obj)
        entity (@entities kind)
        columns-ext (map #(-> % first) entity)
        column-names (map #(str "`" (-> % name) "`") columns-ext)
        values-ext (map #(db-obj %) columns-ext)
        qvalues (map-indexed (fn [i _] (str "?" (+ i 1))) values-ext)
        assignments (map #(str %1 "=" %2) column-names qvalues)
        query (str "insert into " (table-from-kind kind) " ("
                   (str/join "," column-names) ") values(" (str/join "," qvalues) ")"
                   " on duplicate key update " (str/join "," assignments))]
    (sql! (concat [query] values-ext))))

(defn delete! [kind key]
  (let [key-field (-> kind key-field name)]
    (if (seq? key)
      (sql! [(str "delete from " (table-from-kind kind) " where " key-field " = any(?)") (into-array key)])
      (sql! [(str "delete from " (table-from-kind kind) " where " key-field " = ?") key]))))

(defn fetch-object [uuid]
  (:object (fetch :object uuid)))

(defn store-object! [uuid obj]
  (store! :object {:uuid uuid :object obj}))

(defn delete-object! [uuid]
  (delete! :object uuid))

(defn find-feed [& {:keys [title suffix]}]
  (let [feed (first
               (if suffix
                 (query :feed (= :suffix suffix))
                 (query :feed (= :title title))))]
    feed))

(defn persist-feed! [feed]
  (store! :feed feed))

(defn extra [feed field]
  (when-let [extra-fields (:_extra feed)]
    (extra-fields field)))

(defn data-file-path [name]
  (str files-dir name))

(defn write-data-file [path bytes]
  (let [file-dir (core/get-parent-directory path)]
    (core/create-directory file-dir)
    (core/write-bytes-to-file path bytes)))

(defn fetch-text [uuid]
  (when-let [blob (fetch :text uuid)]
    {:uuid uuid
     :content-type (:content-type blob)
     :text (-> uuid
               data-file-path
               core/read-file-as-bytes
               (String. "utf-8"))}))

(defn store-text! [obj]
  (store! :text {:uuid (:uuid obj)
                 :content-type (:content-type obj)
                 ;:data (:text obj)
                 })
  (let [file-path (data-file-path (:uuid obj))]
    (when-let [text (:text obj)]
      (write-data-file file-path (.getBytes text "utf-8")))))

(defn delete-text! [uuid]
  (delete! :text uuid)
  (io/delete-file (data-file-path uuid)))

#_(defn blob-result-handler [[blob]]
  (when-let [data (:data blob)]
    (let [length (.length data)]
      [(assoc blob :data (.getBytes data 0 length))])))

(defn fetch-blob [uuid]
  (when-let [blob (fetch :blob uuid)]
    {:uuid uuid
     :content-type (:content-type blob)
     :bytes (-> uuid
                data-file-path
                core/read-file-as-bytes)}))

(defn store-blob! [obj]
  (store! :blob {:uuid (:uuid obj)
                 :content-type (:content-type obj)
                 ;:data (:bytes obj)
                 })
  (let [file-path (data-file-path (:uuid obj))]
    (when-let [bytes (:bytes obj)]
    (write-data-file file-path bytes))))

(defn delete-blob! [uuid]
  (delete! :blob uuid)
  (io/delete-file (data-file-path uuid)))


(defn image-exists? [url]
  (first (query :image (= :url url))))

(defn get-cloud-image-url [uuid]
  nil)

(defn get-image-url [uuid]
  (str (core/get-app-host) "/image/" uuid))

(defn fetch-image [uuid]
  (let [;image (fetch :image uuid)
        blob (fetch-blob (str "image/" uuid))]
    blob))

(defn store-image! [uuid url content-type data]
  (store! :image {:uuid uuid :url url :content-type content-type :timestamp (core/timestamp)})
  (store-blob! {:uuid (str "image/" uuid) :content-type content-type :bytes data :public true}))

(defn delete-image! [uuid]
  (delete-blob! (str "image/" uuid))
  (delete! :image uuid))


(defn fetch-feed-output [uuid]
  (let [text (fetch-text (str "feed/" uuid))]
    {:output (:text text)
     :content-type (:content-type text)}))

(defn store-feed-output! [uuid obj]
  (store-text! {:uuid (str "feed/" uuid) :content-type (:content-type obj) :text (:output obj)}))

(defn delete-feed-output! [uuid]
  (delete-text! (str "feed/" uuid)))

(defn fetch-history [uuid]
  (let [uuid (core/sha256 uuid)]
    (when-let [text (fetch-text (str "history/" uuid))]
      (when-let [text (:text text)]
        (read-string text)))))

(defn store-history! [uuid obj]
  (when obj
    (let [uuid (core/sha256 uuid)
          text (pr-str obj)
          uuid (str "history/" uuid)]
      (store-text! {:uuid uuid :content-type "application/edn" :text text}))))


(defn fetch-magic-entity [kind key]
  (case kind
    :*task (fetch-tasks)
    :*task-feeds (fetch-task-feeds key)
    :history (fetch-history key)
    ))