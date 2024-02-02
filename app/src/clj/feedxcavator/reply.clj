(ns feedxcavator.reply)

;; ring responses ;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;

(defn permission-denied []
  {:status  403
   :headers {"Content-Type" "text/html"}
   :body    "<h2>Permission denied</h2>"})

(defn page-not-found []
  {:status  404
   :headers {"Content-Type" "text/html"}
   :body    "<h2>Page not found</h2>"})

(defn internal-server-error []
  {:status  500
   :headers {"Content-Type" "text/html"}
   :body    "<h2>Internal server error</h2>"})

(defn internal-server-error-message [text]
  {:status  500
   :headers {"Content-Type" "text/html"}
   :body    text})

(defn no-content []
  {:status  204
   :headers {"Cache-Control" "no-cache"}})

(defn web-page [content-type body]
  {:status  200
   :headers {"Content-Type"  content-type,
             "Cache-Control" "no-cache"}
   :body    body})

(defn forward-page [headers body]
  {:status  200
   :headers headers
   :body    body})

(defn attachment-page [filename body]
  {:status  200
   :headers {"Content-Disposition" (str "attachment; filename=" filename),
             "Cache-Control"       "no-cache"}
   :body    body})

(defn rss-page [body]
  {:status  200
   :headers {"Content-Type"  "application/rss+xml"
             "Cache-Control" "no-cache"}
   :body    body})

(defn json-page [body]
  {:status  200
   :headers {"Content-Type"  "application/json"
             "Cache-Control" "no-cache"}
   :body    body})

(defn html-page [body]
  {:status  200
   :headers {"Content-Type"  "text/html"
             "Cache-Control" "no-cache"}
   :body    body})

(defn text-page [body]
  {:status  200
   :headers {"Content-Type"  "text/plain",
             "Cache-Control" "no-cache"}
   :body    body})

(defn redirect-to
  [location]
  {:status  302
   :headers {"Location" location}})