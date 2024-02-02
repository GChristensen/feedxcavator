(ns feedxcavator.app
  (:require [clojure.string :as str]
            [feedxcavator.core :as core]
            [feedxcavator.auth :as auth]
            [feedxcavator.fetch :as fetch]
            [feedxcavator.reply :as reply]
            [feedxcavator.code :as code]
            [feedxcavator.code-user :as code-user]          ; required
            [feedxcavator.backend :as backend]
            [feedxcavator.websub :as websub]
            [ring.middleware.json :as ring-json]
            [ring.middleware.multipart-params.byte-array :as ring-byte-array]
            [ring.middleware.resource :refer [wrap-resource]]
            [ring.middleware.defaults :refer [wrap-defaults]]
            [ring.middleware.multipart-params :refer [wrap-multipart-params]]
            [buddy.auth :refer [authenticated? throw-unauthorized]]
            [buddy.auth.backends.session :refer [session-backend]]
            [buddy.auth.middleware :refer [wrap-authentication wrap-authorization]])
  (:use compojure.core))

(def user-code-compiled (atom false))

(defn home [request]
  (let [config (core/get-config)]
    (if (and (not= false (:authentication config))
             (authenticated? request))
      (reply/redirect-to "/console")
      (reply/redirect-to "/login"))
    (reply/redirect-to "/console")))

(defroutes feedxcavator-app-routes
  (GET "/" request home)
  (GET "/login" [] auth/login)
  (POST "/login" [] auth/login-authenticate)
  (GET "/logout" [] auth/logout)
  (GET "/console" [] (backend/main-page))

  (GET "/front/feed-url" [uuid] (backend/get-feed-url uuid))
  (GET "/front/list-feeds" [] (backend/list-feeds))
  (GET "/front/list-task-feeds" [task] (backend/list-task-feeds task))
  (GET "/front/run-feed-task" [uuid] (backend/run-feed-task uuid))
  (GET "/front/create-new-feed" [] (backend/create-new-feed))
  (GET "/front/feed-definition" [uuid] (backend/get-feed-definition uuid))
  (POST "/front/feed-definition" request (backend/save-feed-definition request))
  (POST "/front/test-feed" request (backend/test-feed request))
  (GET "/front/delete-feed" [uuid] (backend/delete-feed uuid))
  (GET "/front/get-tasks" [] (backend/get-tasks))
  (GET "/front/get-code" [type] (backend/get-code type))
  (POST "/front/save-code" request (backend/save-code request))
  (GET "/front/get-log-entries" [n from] (backend/get-log-entries n from))
  (GET "/front/clear-log" [] (backend/clear-log))
  (GET "/front/server-log" [] (backend/get-server-log))
  (GET "/front/get-auth-token" [] (backend/get-auth-token))
  (GET "/front/gen-auth-token" [] (backend/gen-auth-token))
  (GET "/front/get-settings" [] (backend/get-settings))
  (POST "/front/save-settings" request (backend/save-settings request))
  (GET "/front/backup-database" [] (backend/backup-database))
  (POST "/front/restore-database" request (backend/restore-database request))

  ;(ANY "/backend/check-schedules" [] (code/check-schedules nil))
  ;(ANY "/backend/service-task-front" [] (backend/service-task-front))
  ;(ANY "/backend/service-task-background" [] (backend/service-task-background nil))
  (GET "/backend/run/:task" [task] (code/enqueue-task task))
  ;(POST "/backend/execute-task" request (code/execute-queued-task request))

  (POST "/api/wordfilter/add-regex" request (backend/add-filter-regex request))
  (POST "/api/wordfilter/remove-regex" request (backend/remove-filter-regex request))
  (POST "/api/wordfilter/list-words" request (backend/list-word-filter-words request))
  (POST "/api/wordfilter/list" request (backend/list-word-filter request))

  (GET "/feed/:suffix" [suffix] (backend/deliver-feed suffix))
  (ANY "/image/:name" [name] (backend/serve-image name))
  (ANY "/handler/:handler" request (code/execute-handler request))
  (ANY "/redirect/:random/:url" [random url] (backend/redirect url))
  (ANY "/redirect-b64/:random/:url" [random url] (backend/redirect-b64 url))
  (GET "/forward/:random/" [random url referer] (backend/forward-url url referer))
  (GET "/forward-b64/:url" [url] (backend/forward-url-b64 url))
  (ANY "/websub" request (websub/hub-action request))

  ;(ANY "/_ah/mail/*" request (backend/receive-mail request))

  (ANY "*" [] (reply/page-not-found)))

(defn allow-path? [path]
  (or (= "/" path)
      (= "/login" path)
      (str/starts-with? path "/api/")
      (str/starts-with? path "/image/")
      (str/starts-with? path "/feed/")
      (str/starts-with? path "/handler/")
      (str/starts-with? path "/redirect/")
      (str/starts-with? path "/redirect-b64/")
      (str/starts-with? path "/forward/")
      (str/starts-with? path "/forward-b64/")
      (str/starts-with? path "/websub")))

(defn context-binder [handler]
  (fn [req]
    (when (and
            (not= false (:authentication (core/get-config)))
            (not (authenticated? req))
            (not (allow-path? (:uri req))))
      (throw-unauthorized))

    (let [server-name (:server-name req)]
      (binding [core/*remote-addr* (:remote-addr req)
                core/*app-host* (str core/*app-host-scheme* "://"
                                     server-name
                                     (let [port (:server-port req)]
                                       (when (and port (not= port 80) (not= port 443))
                                         (str ":" port))))
                core/*current-logging-source* nil
                fetch/*last-http-response* (atom nil)
                fetch/*last-http-error-code* (atom nil)
                fetch/*last-http-network-error* (atom nil)
                fetch/*last-http-conversion-error* (atom nil)]

        (when (not @user-code-compiled)
          (try
            (code/compile-user-code)
            (reset! user-code-compiled true)
            (catch Exception e (.printStackTrace e))))

        (handler req)))))

(def site-defaults
  "A default configuration for a browser-accessible website, based on current
  best practice."
  {:params    {:urlencoded true
               :multipart  true
               :nested     true
               :keywordize true}
   :cookies   true
   :session   {:flash true
               :cookie-attrs {:http-only true, :same-site :strict}}
   :security  {;:anti-forgery   true
               ;:xss-protection {:enable? true, :mode :block}
               :frame-options  :sameorigin}
   :static    {:resources "public"}
   :responses {:not-modified-responses true
               :absolute-redirects     false
               :content-types          true
               :default-charset        "utf-8"}})

(def auth-backend (session-backend {:unauthorized-handler auth/unauthorized-handler}))

(defn wrap-auth [handler]
  (let [config (core/read-config)]
    (if (not= false (:authentication config))
      (-> handler
          (wrap-authentication auth-backend)
          (wrap-authorization auth-backend))
      handler)))

(def feedxcavator-app-handler
  (-> feedxcavator-app-routes
      context-binder
      wrap-auth
      (wrap-defaults site-defaults)
      (wrap-multipart-params {:store (ring-byte-array/byte-array-store)})
      (wrap-resource "public")
      (ring-json/wrap-json-body {:keywords? true :bigdecimals? true})
    ))

(defn feedxcavator-app-init []
  (when-let [config (core/read-config)]
    (swap! core/config merge config)
    (reset! core/exclusive-jdbc-connection (:exclusive-jdbc-connection config))
    ))