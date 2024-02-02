(ns feedxcavator.code
  (:require [clojure.string :as str]
            [clojure.core.async :as async]
            [feedxcavator.core :as core]
            [feedxcavator.db :as db]
            [feedxcavator.log :as log]
            [feedxcavator.websub :as websub]
            [feedxcavator.extraction :as extraction]
            [feedxcavator.code-api :as api]
            [feedxcavator.reply :as reply])
  (:use [chime.core :only [chime-at]])
  (:import [java.util.concurrent ExecutorService Executors]
           ))

(def io-lock ["lock"])

(def sequential-thread-pool (Executors/newFixedThreadPool 1))
(def parallel-thread-pool (Executors/newFixedThreadPool 4))

(def ^:dynamic *background-tasks* (atom {}))
(def ^:dynamic *schedules* (atom []))
;(def ^:dynamic *periodic-schedules* (atom []))
(def ^:dynamic *handlers* (atom {}))

(defn reset-environment []
  (reset! *background-tasks* {})
  (doall (for [schedule @*schedules*]
           (.close schedule)))
  (reset! *schedules* [])
;  (reset! *periodic-schedules* [])
  (reset! *handlers* {}))

(def code-preamble
"
  (defextractor default-background-extractor [feed]
    (feedxcavator.extraction/default-feed-parser feed))
")

(defn compile-user-code
  ([]
   (binding [*ns* (find-ns (symbol core/user-code-ns))]
     (let [code (map (fn [type]
                       (:code (first (filter (fn [code-record] (= (:type code-record) type))
                                             (db/fetch :code)))))
                     ["library" "tasks" "extractors" "handlers"])]
       (reset-environment)
       (load-string code-preamble)
       (doseq [c code]
         (when c
          (load-string c)))
       "Successfully saved.")))
  ([tab]
   (binding [*ns* (find-ns (symbol core/user-code-ns))]
     (when-let [code (:code (db/fetch :code tab))]
       (if (= tab "scratch")
         (with-out-str (load-string code))
         (do
           (load-string code)
           (try (compile-user-code)
                (catch Throwable e))))))))

(defn task-feeds [task-name]
  (doall (db/fetch :*task-feeds task-name)))

(declare get-task)

(defn get-task-feeds [task-name]
  (when-let [task (get-task task-name)]
    (if (:args task)
      (doall (apply concat (map #(task-feeds %) (:args task))))
      (task-feeds task-name))))

(defn millis-to-time [n]
  (let [m (Math/floor (/ n 60000))
        s (/ (- n (* m 60000)) 1000)]
    (str (when (> m 0) (str (int m) "m ")) s "s")))

(defn print-profiling-results [task-name feeds results start-time]
  (let [now (core/timestamp)]
    (log/write :info
      (str/join "\n"
                (concat
                  [(str "Task: " task-name)
                   (str "Total execution time: " (millis-to-time (- now start-time)))]
                  (for [feed feeds]
                    (let [feed-results (first (filter #(= (:uuid %) (:uuid feed)) results))
                          time (:execution-time feed-results)]
                      (str " - " (:title feed) ": "
                        (if time
                          (millis-to-time time)
                          "N/D")
                        ", " (or (:headline-count feed-results) 0) " headlines"
                           ))))))))

(defn submit-job-to-pool [f sequential]
  (.submit ^ExecutorService (if sequential sequential-thread-pool parallel-thread-pool)
           ^Callable f))

(defn perform-fetch-job [feed]
  (let [start-time (core/timestamp)
        headline-count (atom 0)
        feed-extractor (:extractor feed)
        feed (if feed-extractor
               feed
               (assoc feed :extractor "default-background-extractor"))]
    (log/with-logging-source (:extractor feed)
      (try
        (let [headlines (extraction/extract (with-meta feed {:background true}))]
          (reset! headline-count (count headlines)))
        (catch Throwable e
          (log/write :error e)
          (.printStackTrace e))))

    {:uuid (:uuid feed)
     :headline-count @headline-count
     :execution-time (- (core/timestamp) start-time)}))

(defn fetch-feeds-in-background [task-name feeds]
  (let [settings (db/fetch :settings "main")
        start-time (core/timestamp)
        jobs (doall (for [feed feeds]
                      (submit-job-to-pool
                        #(perform-fetch-job feed) (= false (:parallel feed)))))
        ]
    (future
      (let [results (doall (map #(identity @%) jobs))]
        (when (:enable-profiling settings)
          (log/with-logging-source "profiling"
                                   (print-profiling-results task-name feeds results start-time))))
      )))

(defn handle-background-feed [feed headlines]
  (let [output (extraction/produce-feed-output feed headlines)]
    (db/store-feed-output! (:uuid feed) output)
    (when (:realtime feed)
      (try
        (if (:partition feed)
          (let [feed-url (core/get-feed-url feed)
                parts (partition-all (:partition feed) headlines)]
            (doseq [part (reverse parts)]
              (Thread/sleep 1000)
              (websub/publish-content (:uuid feed) feed-url (extraction/produce-feed-output feed part))))
          (websub/publish-content (:uuid feed) (core/get-feed-url feed) output))
        (catch Throwable e
          (log/with-logging-source "websub"
            (log/write :error e))
          (.printStackTrace e))))
    headlines
    ))

(defmacro defextractor [fun-name [feed] & body]
  (let [extractor-fun (gensym)]
    `(do
       (defn ~extractor-fun [~feed]
         (let [background# (:background (meta ~feed))
               testing# (:testing (meta ~feed))
               has-task# (:task ~feed)]
           (locking io-lock
            (println (str "executing " ~(name fun-name) " of " (:suffix ~feed) (when background# " (background)"))))
           (binding [core/*current-feed* ~feed]
             (if (and (not testing#) has-task#) ; feeds with a task store output
               (if background# ; output is stored when handler is called from a task
                 (let [headlines# (extraction/filter-headlines ~feed (do ~@body))]
                   (when (seq headlines#)
                     (handle-background-feed ~feed headlines#))
                   (locking io-lock
                     (println (str "finished background processing of " (:suffix ~feed))))
                   headlines#)
                 (db/fetch-feed-output (:uuid ~feed))) ; output is retrieved when handler is called from the web
               ;; feeds without a task return output directly
               (let [headlines# (extraction/filter-headlines ~feed (do ~@body))]
                 (when (seq headlines#)
                   (extraction/produce-feed-output ~feed headlines#)))))))
       (defn ~(symbol (str (name fun-name) "-test")) [~feed]
         (~extractor-fun ~feed))
       (def ~fun-name ~extractor-fun))))

(defn task-name [task]
  (if (string? task)
    task
    (name task)))

(defn task-instance [task-name]
  {:name task-name
   :enqueue-fn #(fetch-feeds-in-background task-name (task-feeds task-name))
   })

(declare enqueue-task)

(defn fetch-tasks-in-background [supertask task-names on-completion]
  (let [settings (db/fetch :settings "main")
        start-time (core/timestamp)
        jobs (doall (for [task-name task-names]
                      (fetch-feeds-in-background task-name (task-feeds task-name))))
        ]
    (future
      (let [_ (doall (map #(identity @%) jobs))]
        (when on-completion
          (let [on-completion (if (seq? on-completion) on-completion [on-completion])]
            (doseq [task-name on-completion]
              (enqueue-task (name task-name)))))
        (when (:enable-profiling settings)
          (log/with-logging-source "profiling"
           (log/write :info
                      (str/join "\n"
                                (concat
                                  [(str "Supertask: " supertask)
                                   (str "Total execution time: "
                                        (millis-to-time (- (core/timestamp) start-time)))])))))
      ))))

(defmacro deftask* [symbol-or-string subtasks & {:keys [on-completion]}]
  (let [computed-name (task-name symbol-or-string)
        subtasks (when subtasks (map name subtasks))
        ]
    `(let [subtasks# '~subtasks]
        (swap! *background-tasks* assoc ~computed-name
        {:name ~computed-name
         :args subtasks#
         :enqueue-fn #(feedxcavator.code/fetch-tasks-in-background ~computed-name subtasks# (quote ~on-completion))
         }))))

(defmacro deftask [symbol-or-string subtasks]
  (let [computed-name (task-name symbol-or-string)]
    (if (seq subtasks)
      `(deftask* ~symbol-or-string [~@subtasks])
      `(swap! *background-tasks* assoc ~computed-name (task-instance ~computed-name)))))

(defn get-task [task-name]
  (or (@*background-tasks* task-name)
      (task-instance task-name)))

(defmacro schedule [task hour min]
  `(swap! *schedules* conj (chime-at (feedxcavator.core/daily-at ~hour ~min) (fn [~'_] (enqueue-task ~(name task))))))

(defmacro schedule-periodically [task hours]
   nil)

(defn enqueue-task [task-name]
  (if-let [enqueue (:enqueue-fn (get-task task-name))]
    (try
      (enqueue)
      (reply/web-page "text/plain" (str task-name " enqueued"))
      (catch Throwable e
        (.printStackTrace e)
        (reply/web-page "text/plain" "ERROR")))
    (reply/page-not-found)))

(defmacro defhandler [& params]
  (let [handler-name (first params)
        authorize (:auth (meta handler-name))
        computed-name (name handler-name)
        args (second params)
        body (drop 2 params)]
    (if (= args 'request)
      `(swap! *handlers* assoc ~computed-name {:handler (fn [~'request] ~@body) :request true :auth ~authorize})
      `(swap! *handlers* assoc ~computed-name {:handler (fn [~@args] ~@body) :params '~args :auth ~authorize}))))

(defn execute-handler [request]
  (let [handler-name (:handler (:params request))
        handler (@*handlers* handler-name)
        execute (fn []
                  (if (:request handler)
                    ((:handler handler) request)
                    (let [kw-args (map #(-> % name keyword) (:params handler))
                          params (map #((:params request) %) kw-args)]
                      (apply (:handler handler) params))))]
    (if handler
      (log/with-logging-source handler-name
        (if (:auth handler)
          (core/authorized request (execute))
          (execute)))
      (reply/page-not-found))))