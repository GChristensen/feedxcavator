(ns feedxcavator.ajax
  (:require [ajax.core :refer [GET POST]]
            [clojure.string :as str]
            ;[feedxcavator.mock-backend :as demo]
            )
  (:use [cljs.reader :only [read-string]]))

(defn get-text
  ([url handler]
   (GET url {:handler handler :error-handler handler}))
  ([url params handler]
   (GET url {:params params :handler handler :error-handler handler})))

(defn get-edn
  ([url params handler]
   (GET url {:params params
             :handler (fn [r]
                        (handler (read-string r)))}))
  ([url handler]
   (get-edn url nil handler)))

(defn post-multipart [url params handler]
  (let [form-data (js/FormData.)]
    (doseq [p params]
      (.append form-data (name (first p)) (second p)))
    (POST url {:handler handler
               :error-handler handler
               :params  params
               :body    form-data})))

(defn rearrange-content [content]
  (let [lines (str/split content #"\r")
        lines (for [line lines]
                (if (str/includes? line "  ")
                  (str/join "  " (-> line (str/split #"  ") reverse))
                  line)
                )
        lines (str/join "\n" lines)
        lines (str/join "\n\n\n" (reverse (str/split lines #"\n\n\n")) )]
    lines))

(defn extract-server-error [response-text]
  (let [error (-> response-text
                  (str/replace #"</div>" "&#13;&#13;</div>")
                  (str/replace #"</tr>" "&#13;</tr>")
                  (str/replace #"</td>" "&#x20;&#x20;</td>")
                  (str/replace #"</h1>" "&#13;</h1>")
                  (str/replace #"</h2>" "&#13;</h2>"))
        doc (.createHTMLDocument (.-implementation js/document) "")]
    (set! (.-innerHTML (.-documentElement doc)) error)
    (let [content (str/trim (.-textContent (.-body doc)))]
         (rearrange-content content)
      )))

#_(do
  (def get-text demo/get-text)
  (def get-edn demo/get-edn)
  (def post-multipart demo/post-multipart))