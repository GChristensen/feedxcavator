(ns feedxcavator.websub
  (:require [feedxcavator.core :as core]
            [feedxcavator.fetch :as fetch]
            [feedxcavator.db :as db]
            [clojure.string :as str]
            [feedxcavator.log :as log]))

(defn websub-enabled? []
  (let [config (core/get-config)]
    (not= (:websub config) false)))

(defn feed-from-url [url]
  (let [suffix (last (str/split url #"/feed/"))]
    (if (str/starts-with? suffix "uuid:")
      (first (db/fetch :feed (last (str/split suffix #":"))))
      (first (db/query :feed (= :suffix suffix))))))

(defn subscribe [params]
  (when (websub-enabled?)
    (let [topic (params "hub.topic")
          callback (params "hub.callback")
          feed (feed-from-url topic)
          subscr (db/fetch :subscription (:uuid feed))]
      (when (or (not subscr)
                (not (and (= (:topic subscr) topic)
                          (= (:callback subscr) callback))))
        (db/store! :subscription {:uuid (:uuid feed)
                                  :name (:title feed)
                                  :topic topic
                                  :callback callback
                                  :secret (params "hub.secret")
                                  :timestamp (core/timestamp)})
        (fetch/fetch-url (str (params "hub.callback")
                             (str "&hub.lease_seconds=" (params "hub.lease_seconds"))
                             (str "&hub.topic=" (params "hub.topic"))
                             (str "&hub.mode=" (params "hub.mode"))
                             (str "&hub.challenge=" (core/generate-uuid)))
                        :proxy false
                        :insecure? true
                        :async? false)
        (println (str "Subscribed to feed: " topic)))
      {:status 202})))

(defn unsubscribe [params]
  (when (websub-enabled?)
    (let [topic (params "hub.topic")
          feed (feed-from-url topic)]
      (db/delete! :subscription (:uuid feed))
      (fetch/fetch-url (str (params "hub.callback")
                           (str "&hub.lease_seconds=" (params "hub.lease_seconds"))
                           (str "&hub.topic=" (params "hub.topic"))
                           (str "&hub.mode=" (params "hub.mode"))
                           (str "&hub.challenge=" (core/generate-uuid)))
                      :proxy false
                      :insecure? true
                      :async? true)
      {:status 202})))

(defn publish-content [uuid topic content]
  (when (websub-enabled?)
    (when-let [subscr (db/fetch :subscription uuid)]
      (let [sig (when (:secret subscr)
                  (core/sha1-sign (:output content) (:secret subscr)))
            headers {"Content-Type" (str (:content-type content) "; charset=utf-8")
                     "Link" (str "<" topic ">; rel=\"self\", <"
                                 (core/get-websub-url) ">; rel=\"hub\"")}
            headers (if sig
                      (assoc headers "X-Hub-Signature" (str "sha1=" sig))
                      headers)
            callback (:callback subscr)
            callback (if (str/includes? callback "feedly.com")
                           (str callback "&hub.mode=publish")
                           callback)
            response (fetch/fetch-url callback
                                     :method :post
                                     :insecure? true
                                     :proxy false
                                     :headers headers
                                     :payload (.getBytes (:output content) "utf-8"))]
        (when (nil? response)
          (if-let [network-error (fetch/get-last-network-error)]
            (log/write :error network-error)
            (let [error (format "HTTP error during WebSub publishing: %d\nresponse content: %s\nHTTP response: %s"
                                (fetch/get-last-http-error)
                                (slurp (:body (fetch/get-last-http-response)))
                                (with-out-str (clojure.pprint/pprint (fetch/get-last-http-response))))]
              (log/write :error error))))
        {:status 204}))))

(defn publish [params]
  (let [topic (params "hub.url")
        feed (feed-from-url topic)
        uuid (:uuid feed)
        content (:output (db/fetch-feed-output uuid))]
    (publish-content uuid topic content)))

(defn hub-action [request]
  (if (= :post (:request-method request))
    (case ((:params request) "hub.mode")
      "subscribe" (subscribe (:params request))
      "unsubscribe" (unsubscribe (:params request))
      "publish" (publish (:params request)))))