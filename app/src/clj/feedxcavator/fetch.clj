(ns feedxcavator.fetch
  (:require [feedxcavator.core :as core]
            [clj-http.client :as client]
            [clojure.data.json :as json]
            [feedxcavator.log :as log]
            [ring.util.response :as resp]))

(def proxies (atom {}))

(def ^:dynamic *last-http-response* (atom nil))
(def ^:dynamic *last-http-error-code* (atom nil))
(def ^:dynamic *last-http-network-error* (atom nil))
(def ^:dynamic *last-http-conversion-error* (atom nil))
(def ^:dynamic *fetch-url-log-errors* nil)

(defn get-last-http-response []
  @*last-http-response*)

(defn get-last-http-error []
  @*last-http-error-code*)

(defn get-last-network-error []
  @*last-http-network-error*)

(defn get-last-conversion-error []
  @*last-http-conversion-error*)

(defmacro safely-repeat-fetch-url [statement]
  `(try
     ~statement
     (catch Exception e#
       (try
         ~statement
         (catch Exception e2#
           (reset! *last-http-network-error* e2#))))))

(defmacro safely-fetch-url [statement]
  `(try
     ~statement
     (catch Exception e#
       (reset! *last-http-network-error* e#))))

(defn fetch-url
  "
  params are:
  :method
  :headers
  :payload
  :as
  :timeout
  :proxy
  :follow-redirects
  :retry
  :async?
  :insecure?"
  [url & params]
  (reset! *last-http-response* nil)
  (reset! *last-http-error-code* nil)
  (reset! *last-http-network-error* nil)
  (reset! *last-http-conversion-error* nil)

  (let [params-map (apply hash-map params)
        response-type (params-map :as)
        retry (if (false? (params-map :retry)) false true)
        charset (params-map :charset)
        charset (if charset
                  charset
                  (if (:charset core/*current-feed*)
                    (:charset core/*current-feed*)
                    "utf-8"))
        timeout (:timeout params-map)
        proxy (when (not= false (:proxy params-map))
                (or (:proxy params-map)
                    (when core/*current-feed* (:proxy core/*current-feed*))))
        request-options {:url url
                         :throw-exceptions false
                         :cookie-policy :standard
                         :as :stream
                         :redirect-strategy (if (= false (:follow-redirects params-map)) :none :graceful)
                         :request-method (or (:method params-map) :get)
                         :async? (:async? params-map)
                         :headers (:headers params-map)
                         :body (:payload params-map)
                         :insecure (:insecure? params-map)
                         }
        request-options (if timeout
                          (assoc request-options :socket-timeout timeout
                                                 :connection-timeout timeout)
                          request-options)
        request-options (if (and proxy (@proxies proxy))
                          (assoc request-options :proxy-host (:host (@proxies proxy))
                                                 :proxy-port (:port (@proxies proxy))
                                                 :proxy-user (:user (@proxies proxy))
                                                 :proxy-pass (:password (@proxies proxy)))
                          request-options)
        ]

    (let [response (if retry
                     (safely-repeat-fetch-url (client/request request-options))
                     (safely-fetch-url (client/request request-options)))]

      (if (and response (:status response) (< (:status response) 300))
        (try
          (cond (= response-type :html) (core/resp->enlive response charset)
                (= response-type :xml) (core/resp->enlive-xml response charset)
                (= response-type :json) (json/read-str (core/resp->str response charset) :key-fn keyword)
                (= response-type :string) (core/resp->str response charset)
                (= response-type :bytes) (core/resp->bytes response)
                :else (assoc response :final-url (last (:trace-redirects response))
                                      :content-type (resp/find-header response "content-type")))
          (catch Exception e
            (reset! *last-http-conversion-error* true)
            (.printStackTrace e)))
        (do
          (reset! *last-http-response* response)
          (reset! *last-http-error-code* (:status response))
          (when *fetch-url-log-errors*
            (let [msg (str "Fetch error\nURL: " url "\nError: "
                           (or @*last-http-error-code*
                               (when @*last-http-network-error*
                                 (.getMessage @*last-http-network-error*))))]
              (log/write :error msg)))
          nil)))))

(defmacro defproxy [name & params]
  `(swap! proxies assoc ~name (hash-map ~@params)))

(defmacro log-fetch-errors [& body]
  `(binding [*fetch-url-log-errors* true]
     ~@body))

(System/setProperty "org.apache.commons.logging.Log" "org.apache.commons.logging.impl.NoOpLog")

