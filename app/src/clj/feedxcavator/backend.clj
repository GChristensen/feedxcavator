(ns feedxcavator.backend
  (:require [clojure.java.io :as io]
            [clojure.string :as str]
            [feedxcavator.fetch :as fetch]
            [yaml.core :as yaml]
            [clojure.edn :as edn]
            [cheshire.core :as json]
            [feedxcavator.reply :as reply]
            [feedxcavator.log :as log]
            [feedxcavator.entity :as entity]
            [feedxcavator.code-api :as api]
            [feedxcavator.extraction :as extraction]
            [feedxcavator.core :as core]
            [feedxcavator.code :as code]
            [feedxcavator.log :as log]
            [feedxcavator.db :as db])
  (:use hiccup.core
        [clojure.pprint :only [pprint]]
        [chime.core :only [chime-at periodic-seq]])
  (:import [java.time Instant Duration Period LocalTime LocalDate ZoneId]))

(defn main-page []
  (reply/html-page
    (html [:html
           [:head
            [:title "Feedxcavator"]
            [:link {:rel "stylesheet" :type "text/css" :href "css/goog/common.css"}]
            ;[:link {:rel "stylesheet" :type "text/css" :href "css/goog/tooltip.css"}]
            ;[:link {:rel "stylesheet" :type "text/css" :href "css/goog/flatbutton.css"}]
            [:link {:rel "stylesheet" :type "text/css" :href "css/goog/tab.css"}]
            [:link {:rel "stylesheet" :type "text/css" :href "css/goog/tabbar.css"}]
            [:link {:rel "stylesheet" :type "text/css" :href "css/goog/toolbar.css"}]
            [:link {:rel "stylesheet" :type "text/css" :href "js/jstree/themes/default/style.css"}]
            [:link {:rel "stylesheet" :type "text/css" :href "js/jstree/mod.css"}]
            [:link {:rel "stylesheet" :type "text/css" :href "css/main.css"}]
            [:link {:rel "stylesheet" :type "text/css" :href "css/dark.css"}]
            [:script {:type "text/javascript" :src "js/jquery.js"}]
            [:script {:type "text/javascript" :src "js/jstree/jstree.js"}]
            [:script {:type "text/javascript" :src "js/ace/ace.js"}]
            [:script {:type "text/javascript" :src "js/main.js"}]
            ]
           [:body
            [:div#branding
             [:img {:src "images/logo.png"}]
             [:h1 "Feedxcavator"
                  (when (= core/deployment-type :demo)
                    [:sup {:style "font-size: 50%"} " DEMO"])]]
            [:div#tabbar.goog-tab-bar.goog-tab-bar-top
             [:div#feeds-tab.goog-tab.goog-tab-selected "Feeds"]
             [:div#tasks-code-tab.goog-tab "Tasks"]
             [:div#library-code-tab.goog-tab "Library"]
             [:div#extractors-code-tab.goog-tab "Extractors"]
             [:div#handlers-code-tab.goog-tab "Handlers"]
             [:div#scratch-code-tab.goog-tab "Scratch"]
             [:div#log-tab.goog-tab "Log"]
             [:div#settings-tab.goog-tab "Settings"]
             [:div#api-tab.goog-tab "API"]]
            [:div.goog-tab-bar-clear]
            [:div#tab-content.goog-tab-content]
            [:script {:type "text/javascript"} "feedxcavator.frontend.main()"]
            ]])))


(defn list-feeds []
  (let [feeds (db/fetch :feed)
        feeds (map #(assoc {} :uuid (:uuid %)
                              :group (:group %)
                              :title (:title %))
                   feeds)]
    (reply/text-page (pr-str (sort-by :title feeds)))))

(defn list-task-feeds [task]
  (reply/text-page (str/join "\n" (map :title (code/get-task-feeds task)))))

(defn run-feed-task [uuid]
  (if-let [feed (db/fetch :feed uuid)]
    (do
      (code/enqueue-task (:task feed))
      (reply/text-page "OK"))
    (reply/page-not-found)))

(defn get-feed-url [uuid]
  (if-let [feed (db/fetch :feed uuid)]
    (reply/text-page (core/get-feed-url feed))
    (reply/page-not-found)))

(defn parse-feed-definition [yaml]
  (let [feed-fields (set (map #(keyword (name %)) entity/feed-fields))
        feed (yaml/parse-string yaml)
        standard-fields (filter #(feed-fields (first %)) feed)
        extra-fields (into {} (filter #(not (feed-fields (first %))) feed))
        selectors (into {} (map #(vector (first %) (extraction/parse-selector (second %))) (:selectors feed)))]
    (assoc (into {} standard-fields)
      :selectors (when (count selectors) selectors)
      :_extra (when (count extra-fields) extra-fields))))

(defn get-feed-definition [uuid]
  (let [params-splitter #"(?:params:[^\n]*(?:\n|$)(?:[ ]+[^\n]+(?:\n|$))+)|(?:params:[^\n]*(?:\n|$))"
        feed-definition (:yaml (db/fetch :feed-definition uuid))
        params (:params (db/fetch :feed uuid))
        params (when params
                 (yaml/generate-string {:params params}
                                        :dumper-options (when (not (coll? params))
                                                          {:flow-style :block})))
        yaml (if params
               (let [parts (str/split feed-definition params-splitter)
                     result (if (> (count parts) 1)
                              (str/join params parts)
                              (str (first parts) params))]
                 result)
               feed-definition)
        ]
    (reply/text-page yaml)))

(defn save-yaml [uuid yaml]
  (let [feed (-> yaml
                 parse-feed-definition
                 (assoc :uuid uuid))
        feed (if (empty? (:_extra feed))
               (dissoc feed :_extra)
               feed)]
    (db/store! :feed-definition {:uuid uuid :yaml yaml})
    (db/store! :feed feed)))

(defn save-feed-definition [request]
  (try
    (let [uuid (get (:multipart-params request) "uuid")
          yaml (get (:multipart-params request) "yaml")]
      (save-yaml uuid yaml)
      (reply/text-page "Successfully saved."))
    (catch Exception e
      (reply/internal-server-error-message (.getMessage e)))))

(defn create-new-feed []
  (let [uuid (core/generate-uuid)
        yaml
"title: '*Untitled feed'
suffix: untitled-feed
source: https://example.com
#charset: utf-8
#output: rss
#parallel: true
#group: group/subgroup
#task: task-name
#proxy: default
#timeout: 0
#selectors:
#  item: div.headline
#  title: h3
#  link: a
#  summary: div.preview
#  image: div.cover img:first-of-type
#  author: div.user-name
#pages:
#  include-source: true
#  path: '/?page=%n'
#  increment: 1
#  start: 2
#  end: 2
#  delay: 0
#filter:
#  history: true
#  content: title+summary
#  wordfilter: default
#realtime: true
#partition: 100
#extractor: extractor-function-name
#params: [any addiitional data, [123, 456]]
"]
    (save-yaml uuid yaml)
    (reply/text-page uuid)))

(defn delete-feed [uuid]
  (db/delete! :feed uuid)
  (db/delete! :feed-definition uuid)
  (db/delete! :subscription uuid)
  (reply/text-page "OK"))

(defn deliver-feed [suffix]
  (if (and suffix (not (str/blank? suffix)))
    (if-let [feed (if (str/starts-with? suffix "uuid:")
                    (db/fetch :feed (subs suffix 5))
                    (first (db/query :feed (= :suffix suffix))))]
      (try
        (if-let [result (extraction/extract feed)]
          (reply/web-page (:content-type result) (:output result))
          (reply/no-content))
        (catch Exception e
          (if (core/production?)
            (reply/internal-server-error)
            (throw e))))
      (reply/page-not-found))
    (reply/page-not-found)))

(when (= core/deployment-type :demo)
  (eval '(defn deliver-feed [suffix]
    (reply/rss-page
"<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<rss version=\"2.0\">
  <channel>
    <title>Feedxcavator static demo feed</title>
    <link>https://feedxcavator.appspot.com</link>
    <item>
      <title>Feedxcavator Demo</title>
      <link>https://feedxcavator.appspot.com</link>
      <description>Feedxcavator static demo feed</description>
    </item>
  </channel>
</rss>"))))

(defn test-feed [request]
  (let [uuid (get (:multipart-params request) "uuid")
        yaml (get (:multipart-params request) "yaml")
        feed (-> yaml
                 parse-feed-definition
                 (assoc :uuid uuid))
        feed (if (:extractor feed)
               (assoc feed :extractor (str (:extractor feed) "-test"))
               feed)
        result (extraction/extract (with-meta feed {:testing true}))]
    (reply/text-page
      (if (= (:content-type result) "application/rss+xml")
        (core/xml-format (:output result))
        (:output result)))))

(defn get-tasks []
  (let [tasks (->> (db/fetch :*task)
                   (map :task))]
    (reply/text-page (pr-str tasks))))

(defn get-code [type]
  (reply/text-page (:code (db/fetch :code type))))

(defn save-code [request]
  (let [type (get (:multipart-params request) "type")
        code (get (:multipart-params request) "code")]
    (db/store! :code {:type type :code code :timestamp (core/timestamp)})
    (reply/text-page (code/compile-user-code type))))

(defn get-log-entries [n from]
  (let [n (when n (Integer/parseInt n))
        from (when from (Integer/parseInt from))]
    (reply/text-page (pr-str (log/read-entries n from)))))

(defn clear-log []
  (log/clear-log)
  (reply/text-page "OK"))

(defn get-server-log []
  (let [file-path "./log/app.log"
        log-contents (when (.exists (io/file file-path))
                       (slurp file-path))]
  (reply/text-page log-contents)))

(defn gen-auth-token []
  (let [token (core/generate-random 20)]
    (db/store! :auth-token {:kind "main" :token token})
    (reply/text-page token)))

(defn get-auth-token []
  (if-let [token (db/fetch :auth-token "main")]
    (reply/text-page (:token token))
    (gen-auth-token)))

(defn redirect [url]
  (reply/redirect-to url))

(defn redirect-b64 [url]
  (reply/redirect-to (core/url-safe-base64dec url)))

(defn forward-url [url referer]
  (let [response (fetch/fetch-url url :headers {"referer" referer})]
    (reply/forward-page (:headers response) (:body response))))

(defn forward-url-b64 [url]
  (let [[url referer] (str/split (core/url-safe-base64dec url) #";;")
        response (fetch/fetch-url url :headers {"referer" referer})]
    (reply/forward-page (:headers response) (:body response))))

(defn add-filter-regex [request]
  (core/authorized request
                   (let [params (:body request)
                         word-filter (if (:word-filter params) (:word-filter params) "default")
                         regex (:regex params)]
                     (if regex
                       (do
                         (extraction/add-filter-regex word-filter regex)
                         (reply/no-content))
                       (reply/page-not-found)))))

(defn remove-filter-regex [request]
  (core/authorized request
                   (let [params (:body request)
                         word-filter (if (:word-filter params) (:word-filter params) "default")
                         regex (:regex params)]
                     (if regex
                       (do
                         (extraction/remove-filter-regex word-filter regex)
                         (reply/no-content))
                       (reply/page-not-found)))))

(defn list-word-filter-words [request]
  (core/authorized request
                   (let [params (:body request)
                         word-filter (if (:word-filter params) (:word-filter params) "default")
                         words (extraction/list-word-filter word-filter)]
                     (if (seq words)
                       (reply/json-page (json/generate-string words))
                       (reply/page-not-found)))))

(defn list-word-filter [request]
  (core/authorized request
                   (let [filters (map :id (db/fetch :word-filter))]
                     (if (seq filters)
                       (reply/json-page (json/generate-string filters))
                       (reply/json-page (json/generate-string []))))))

(defn serve-image [name]
  (try
    (let [image (db/fetch-image name)]
      (if image
        (reply/web-page (:content-type image) (:bytes image))
        (reply/page-not-found)))
    (catch Exception e (reply/internal-server-error))))

(defn receive-mail [request]
  #_(let [message (mail/parse-message request)
        from (:from message)
        from (if (>= (.indexOf from ">") 0)
               (get (re-find #"<([^>]*)>" from) 1)
               from)
        feed (first (db/query :feed (= :source from)))]
    (when feed
      (try
        (let [feed (assoc feed :e-mail message)
              result (extraction/extract (with-meta feed {:background true}))]
          (when result
            (db/store-feed-output! (:uuid feed) result)))
        (catch Exception e
          (.printStackTrace e)))))
  (reply/web-page "text/plain" "OK"))

;; sending mail
;(mail/make-message :from (:sender-mail settings)
;                   :to (:recipient-mail settings)
;                   :subject "Subject"
;                   :text-body "Text body")]
;
;(mail/send msg)

;(defn service-task-front []
;  (code/reset-completed-schedules)
;  (reply/web-page "text/plain" "OK"))

(defn service-task-background [_]
  (println "executing service task")
  (let [days-ago (- (core/timestamp) 259200000)
        old-images (db/query :image (< :timestamp days-ago))
        old-log-entries (db/query :log-entry (< :timestamp days-ago))]
    (doseq [image old-images]
      (db/delete-image! (:uuid image)))
    (db/delete! :log-message (map :uuid old-log-entries))
    (db/delete! :log-entry (map :uuid old-log-entries)))
  (reply/web-page "text/plain" "OK"))

(chime-at (core/daily-at 4 0) service-task-background)

(defn import-word-filter [word-filter]
  (let [words (map #(if (str/starts-with? % "(?i)") (re-pattern %) %)
                   (:words word-filter))]
    (db/store! :word-filter
               (assoc word-filter :words words))))

(defn restore-database [request]
  (let [edn (get (:multipart-params request) "edn")
        edn (String. (:bytes edn) "utf-8")
        data (edn/read-string edn)]
    ;(doseq [f (:feeds data)]
    ;  (db/store! :feed f))
    (doseq [f (:feed-definitions data)]
      (db/store! :feed-definition f)
      (let [feed (parse-feed-definition (:yaml f))
            feed (assoc feed :uuid (:uuid f))]
        (db/store! :feed feed)
        )
      )
    (doseq [s (:subscriptions data)]
      (db/store! :subscription s))
    (doseq [t (:auth-tokens data)]
      (db/store! :auth-token t))
    (doseq [o (:objects data)]
      (db/store! :object o))
    (doseq [w (:word-filters data)]
      (import-word-filter w))
    (doseq [c (:code data)]
      (db/store! :code c))
    (doseq [s (:settings data)]
      (db/store! :settings s)))
  (reply/text-page "OK"))

(defn export-word-filters []
  (for [word-filter (map #(into {} %) (db/fetch :word-filter))]
    (let [words (map #(str %) (:words word-filter))]
      (assoc word-filter :words words))))

(defn backup-database []
  (reply/attachment-page
    "backup.edn"
      (with-out-str
        (pprint
          {
           :feeds            (map #(into {} %) (db/fetch :feed))
           :feed-definitions (map #(into {} %) (db/fetch :feed-definition))
           :subscriptions    (map #(into {} %) (db/fetch :subscription))
           :auth-tokens      (map #(into {} %) (db/fetch :auth-token))
           :objects          (map #(into {} %) (db/fetch :object))
           :word-filters     (export-word-filters)
           :code             (map #(into {} %) (db/fetch :code))
           :settings         (map #(into {} %) (db/fetch :settings))
           }))))

(defn get-settings []
  (let [settings (-> (into {} (db/fetch :settings "main"))
                     (assoc :version core/app-version))]
    (reply/text-page (pr-str settings))))

(defn save-settings [request]
  (let [settings (get (:multipart-params request) "settings")]
    (db/store! :settings (assoc (read-string settings) :kind "main"))))

;(defn schedule-tasks []
;  (def task-check-seq (-> (periodic-seq (Instant/now) (Duration/ofMinutes 1)) rest))
;  (chime-at (rest task-check-seq) code/check-schedules))
