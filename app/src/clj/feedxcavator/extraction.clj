(ns feedxcavator.extraction
  (:require [feedxcavator.core :as core]
            [feedxcavator.db :as db]
            [feedxcavator.fetch :as fetch]
            [clojure.string :as str]
            [clojure.data.json :as json]
            [clj-time.core :as tm]
            [clj-time.format :as fmt]
            [hiccup.core :as hiccup])
  (:use [ring.util.mime-type :only [ext-mime-type]]
        net.cgrand.enlive-html
        clojure.walk))

(def ^:const xml-header "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n")

(def io-lock ["lock"])

(defn css-selector-to-enlive [line]
  (let [line (str/replace line #">" " > ")
        line (str/trim (str/replace line #"[ ]+" " "))
        tokens (str/split line #" ")
        construct-attrs (fn [match]
                          (if (str/blank? (get match 2))
                            (str "? :" (get match 1) ")")
                            (str (case (get match 2)
                                   "=" "="
                                   "*=" "-contains"
                                   "^=" "-starts"
                                   "$=" "-ends"
                                   :default "=")
                                 " :" (get match 1) " "
                                 (if (re-matches #"^\".*\"$"
                                                 (get match 3))
                                   (get match 3)
                                   (str "\"" (get match 3) "\"")) ")")))
        tokens (for [token tokens]
                 (let [token (str ":" token)
                       ;; filter out attribute matchers, only existence check (attr?),
                       ;; full comparison (attr=) and substring check (attr-contains) are supported
                       token (if (re-matches #".*\[.*\].*" token)
                               (str/replace token #"(.*)\[(.*)\](.*)"
                                            (fn [match]
                                              (str "[" (nth match 1) " (net.cgrand.enlive-html/attr"
                                                   (str/replace (nth match 2) #"([^=*~|\^$]+)(?:(.?=)(.*))?"
                                                                construct-attrs)
                                                   "]" (get match 3))))
                               token)
                       ;; filter out pseudoclasses, only parameterless and pseudoclasses with single
                       ;; numeric parameter are supported
                       token (if (re-matches #":.+:.+" token)
                               (str/replace token #":(.+):([^(]+)(?:\((\d+)\))?"
                                            (fn [match]
                                              (if (str/blank? (get match 3))
                                                (str ":" (get match 1) " :> " (get match 2))
                                                (str "[:" (get match 1) " (net.cgrand.enlive-html/"
                                                     (get match 2) " " (get match 3) ")]"))))
                               token)]
                   token))
        enlive-selector (str "[" (apply str (interpose " " tokens)) "]")]
    ;; (println enlive-selector)
    (read-string enlive-selector)))

(defn css-to-enlive [line]
  (case line
    ":root" '[root]
    (if (str/includes? line ",")
      (set (map css-selector-to-enlive (str/split line #",")))
      (css-selector-to-enlive line))))

(defn eval-selector [sel]
  (binding [*ns* (find-ns 'net.cgrand.enlive-html)]
    (eval sel)))

;; selector with multiple alternative attributes to extract value from
;; e.g. div.article a::src||src-lazy
(defn parse-selector [selector]
  (let [line (str/trim selector)
        [sel attrs] (str/split line #"::")
        enlive-sel (or (str/starts-with? sel "[")
                       (str/starts-with? sel "{")
                       (str/starts-with? sel "#{"))
        parsed-sel (if enlive-sel
                    (read-string sel)
                    (css-to-enlive sel))
        attrs (when attrs
                (map keyword (str/split attrs #"\|\|")))]
    {:sel parsed-sel :css (when-not enlive-sel sel) :attrs attrs}
  ))

(defn evaluate-selectors [selectors]
  (into {} (map #(vector (first %) (update-in (second %) [:sel] eval-selector)) selectors)))

(defn apply-selectors
  ([doc]
   (apply-selectors doc core/*current-feed*))
  ([doc feed]
   (let [selectors (evaluate-selectors (:selectors feed))
         headline-sel (:sel (:item selectors))]
     (when headline-sel
       (let [headlines
             (for [headline-html (select doc headline-sel)]
               (letfn [(get-content [element]               ; get the selected element content
                         (let [content (apply str (emit* (:content element)))]
                           (when (not (str/blank? content))
                             content)))
                       (extract-part [part & {:keys [default]}]
                         (let [selector (selectors part)
                               element (first (select headline-html (:sel selector)))
                               select-attrs (if (seq? (:attrs selector))
                                              (:attrs selector)
                                              (when default [default]))
                               element-attrs (:attrs element)]
                           (if (and element-attrs (not (empty? select-attrs)))
                             (some #(element-attrs %) select-attrs)
                             (get-content element))))]
                 (let [headline-data {:title   (extract-part :title)
                                      :link    (core/fix-relative-url (extract-part :link :default :href) (:source feed))
                                      :summary (extract-part :summary)
                                      :image   (core/fix-relative-url (extract-part :image :default :src) (:source feed))
                                      :author  (extract-part :author)
                                      :html    headline-html}]
                   (when (some #(and (string? (second %)) (not (str/blank? (second %)))) headline-data)
                     headline-data))))]
         (filter #(not (nil? %)) headlines))))))

(defn generate-rss [feed headlines]
  (let [feed-entry (fn [key]
                     (core/html-sanitize (key feed)))
        item-entry (fn [key item]
                     (core/html-sanitize (key item)))
        date (fmt/unparse (:rfc822 fmt/formatters) (tm/now))
        now (tm/now)
        item-ctr (atom 1)
        rss (hiccup/html
              [:rss (array-map :version "2.0"
                               :xmlns:atom "http://www.w3.org/2005/Atom"
                               :xmlns:xml "http://www.w3.org/XML/1998/namespace"
                               :xml:base (feed-entry :source))
               [:channel
                [:title (feed-entry :title)]
                [:link (feed-entry :source)]
                (when (:realtime feed)
                  [:atom:link {:rel "hub" :href (core/get-websub-url)}])
                [:atom:link {:rel  "self" :type "application/rss+xml"
                             :href (core/get-feed-url feed)}]
                ;[:description]
                [:pubDate date]
                [:lastBuildDate date]
                (for [headline headlines]
                  [:item
                   [:title (item-entry :title headline)]
                   [:link (item-entry :link headline)]
                   [:pubDate (fmt/unparse (fmt/formatters :rfc822)
                                          (tm/plus now (tm/seconds (swap! item-ctr inc))))]
                   (if (:guid headline)
                     [:guid {:isPermaLink "false"} (item-entry :guid headline)]
                     (when (:realtime feed)
                       [:guid {:isPermaLink "false"} (item-entry :link headline)]))
                   (when (not (str/blank? (item-entry :author headline)))
                     [:author (item-entry :author headline)])
                   [:description (item-entry :summary headline)]
                   (when (:image headline)
                     [:enclosure {:url  (:image headline)
                                  :type (if (:image-type headline)
                                          (:image-type headline)
                                          (ext-mime-type
                                            (:image headline)))}])])]])]
    (str xml-header rss)))

(defn generate-json-feed [feed headlines]
  (let [content {"version" "https://jsonfeed.org/version/1"
                 "title" (:title feed)
                 "home_page_url" (:source feed)
                 "feed_url" (core/get-feed-url feed)}
        content (if (:realtime feed)
                  (assoc content "hubs" {"type" "WebSub" "url" (core/get-websub-url)})
                  content)
        content (assoc content
                       "items" (for [headline headlines]
                                 (into {}
                                   (apply concat
                                          [["id" (:link headline)]]
                                          [["url" (:link headline)]]
                                          (when (:title headline)
                                            [["title" (:title headline)]])
                                          (when (:summary headline)
                                            [["content_html" (:summary headline)]])
                                          (when (:image headline)
                                            [["image" (:image headline)]])
                                          (when (:author headline)
                                            [["author" {"name" (:author headline)}]])))))]
    (json/write-str content)))

(defn generate-json [feed headlines]
  (let [headlines (map #(dissoc % :guid :html) headlines)]
    (json/write-str headlines)))

(defn generate-edn [feed headlines]
  (let [headlines (map #(dissoc % :guid :html) headlines)]
      (pr-str headlines)))

(defn produce-feed-output [feed headlines]
  (let [headlines (doall headlines)]
    (cond (= (:output feed) "json-feed")
          {:output (generate-json-feed feed headlines) :content-type "application/json"}
          (= (:output feed) "json")
          {:output (generate-json feed headlines) :content-type "application/json"}
          (= (:output feed) "edn")
          {:output (generate-edn feed headlines) :content-type "application/edn"}
          :else
          {:output (generate-rss feed headlines) :content-type "application/rss+xml"})))

(defn make-out-of-sync-feed [feed]
  (produce-feed-output feed
                       [{:title   "Error"
                         :summary "Probably feed selectors are out of sync with the source markup."}]))

(defn make-http-error-feed [feed error]
  (produce-feed-output feed
                       [{:title   "Error"
                         :summary (str "HTTP error: " error)}]))

(defn make-network-error-feed [feed]
  (produce-feed-output feed
                       [{:title   "Error"
                         :summary "Network error."}]))

(defn parse-html-page
  ([feed url]
    (when-let [doc-tree (fetch/fetch-url url :as :html :timeout (* (or (:timeout feed) 0) 1000))]
      (apply-selectors doc-tree feed)))
  ([feed]
   (parse-html-page feed (:source feed))))

(defn parse-page-range [feed path start end
                        & {:keys [include-source increment parser delay]
                           :or   {include-source true increment 1 parser parse-html-page}}]
  (let [start (if start start 2)
        increment (if increment increment 1)
        include-source (if (nil? include-source) true include-source)
        path-fn (if (string? path)
                  (fn [n] (format (str/replace path "%n" "%d") n))
                  path)]
    (apply concat (when include-source
                    (parser feed (:source feed)))
           (for [n (range start (inc (* end increment)) increment)]
             (do
               (when delay (Thread/sleep (* delay 1000)))
              (parser feed (str (:source feed) (path-fn n))))))))

(defn parse-pages [feed path n-pages
                   & {:keys [include-source increment parser delay]
                      :or   {include-source true increment 1 parser parse-html-page}}]
  (parse-page-range feed path
                    (if include-source 2 1)
                    n-pages
                    :include-source include-source
                    :increment increment
                    :parser parser
                    :delay delay))

(defn filter-history [feed-or-uuid headlines]
  (let [uuid (or (:uuid feed-or-uuid) feed-or-uuid)
        history (or (db/fetch-history uuid) #{})]
    (filter #(not (history (:link %))) headlines)))

(defn filter-history-by! [feed-or-uuid headlines field]
  (if (seq headlines)
    (let [uuid (or (:uuid feed-or-uuid) feed-or-uuid)
          history (or (db/fetch-history uuid) #{})
          result (filter #(not (history (field %))) headlines)]
      (db/store-history! uuid (set (map #(field %) headlines)))
      result)
    '()))

(defn filter-history! [feed-or-uuid headlines]
  (filter-history-by! feed-or-uuid headlines :link))

(defn filter-history-by-guid! [feed-or-uuid headlines]
  (filter-history-by! feed-or-uuid headlines :guid))

(defn add-filter-word
  ([word-filter word]
   (when word
     (let [word-filter (or (db/fetch :word-filter word-filter) {:id word-filter})
           words (-> (or (:words word-filter) #{})
                     (conj (str/lower-case word)))]
       (db/store! :word-filter (assoc word-filter :words words)))))
  ([word]
   (add-filter-word "default" word)))

(defn remove-filter-word
  ([word-filter word]
   (when word
     (let [word-filter (or (db/fetch :word-filter word-filter) {:id word-filter})
           words (->> (or (:words word-filter) #{})
                      (remove #(= % (str/lower-case word))))]
       (db/store! :word-filter (assoc word-filter :words words)))))
  ([word]
   (remove-filter-word "default" word)))

(defn add-filter-regex
  ([word-filter expr]
   (when expr
     (let [word-filter (or (db/fetch :word-filter word-filter) {:id word-filter})
           words (-> (or (:words word-filter) #{})
                     (conj (re-pattern (str "(?i)" expr))))]
       (db/store! :word-filter (assoc word-filter :words words)))))
  ([expr]
   (add-filter-regex "default" expr)))

(defn remove-filter-regex
  ([word-filter expr]
   (when expr
     (let [word-filter (or (db/fetch :word-filter word-filter) {:id word-filter})
           words (->> (or (:words word-filter) #{})
                      (remove #(= (.toString %) (str "(?i)" expr))))]
       (db/store! :word-filter (assoc word-filter :words words)))))
  ([expr]
   (remove-filter-regex "default" expr)))

(defn list-word-filter [word-filter]
  (when-let [word-filter (db/fetch :word-filter word-filter)]
    (for [item (:words word-filter)]
      (if (string? item)
        item
        (str/replace (.toString item) #"^\(\?i\)" "")))))

(defn matches-filter? [s word-filter]
  (when s
    (let [s (str/lower-case s)]
      (loop [words word-filter]
        (let [word (first words)]
          (when word
            (if (string? word)
              (if (str/includes? s word)
                true
                (recur (next words)))
              (if (re-find word s)
                true
                (recur (next words))))))))))

(defn filter-content [feed headlines]
  (let [word-filter (or (:wordfilter (:filter feed)) "default")
        words (:words (db/fetch :word-filter word-filter))]

    (cond (= (:content (:filter feed)) "title")
          (filter #(not (matches-filter? (:title %) words)) headlines)
          (= (:content (:filter feed)) "title+summary")
          (filter #(not (or (matches-filter? (:title %) words)
                            (matches-filter? (:summary %) words)))
                  headlines)
          :else headlines)))

(defn filter-headlines [feed headlines]
  (if (:filter feed)
    (let [headlines (if (and (:history (:filter feed)) (not (:testing (meta feed))))
                      (filter-history! (:uuid feed) headlines)
                      headlines)
          headlines (if (:content (:filter feed))
                      (filter-content feed headlines)
                      headlines)]
      headlines)
    headlines))

(defn default-feed-parser [feed]
  (if-let [pages (:pages feed)]
    (parse-page-range feed (:path pages) (:start pages) (:end pages)
                      :increment (:increment pages)
                      :include-source (:include-source pages)
                      :delay (:delay pages))
    (parse-html-page feed (:source feed))))

(defn default-extractor [feed]
  (if-let [headlines (default-feed-parser feed)]
      (produce-feed-output feed (filter-headlines feed headlines))
      (cond (fetch/get-last-network-error) (make-network-error-feed feed)
            (fetch/get-last-http-error) (make-http-error-feed feed (fetch/get-last-http-error))
            :else (make-out-of-sync-feed feed))))

(defn prepare-feed [feed]
  (loop [extra (:_extra feed) result feed]
    (if-let [kv (first extra)]
     (recur (next extra) (assoc result (first kv) (second kv)))
      (dissoc result :_extra))))

(defn get-extractor [feed]
  (let [extractor (:extractor feed)]
    (if (not (str/blank? extractor))
      (ns-resolve (find-ns (symbol core/user-code-ns)) (symbol extractor))
      default-extractor)))

(defn extract [feed]
  (let [feed (prepare-feed feed)
        extractor (get-extractor feed)]
    (when extractor
      (extractor feed))))
