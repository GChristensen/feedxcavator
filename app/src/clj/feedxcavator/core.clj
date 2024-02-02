(ns feedxcavator.core
  (:import (java.io ByteArrayInputStream)
           [java.net URLEncoder]
           [java.time Instant Duration Period LocalTime LocalDate ZoneId]
           org.apache.commons.codec.binary.Base64
           java.security.MessageDigest
           java.math.BigInteger
           javax.crypto.spec.SecretKeySpec
           javax.crypto.Mac
           java.util.Formatter
           java.security.SecureRandom)
  (:require [clojure.java.io :as io]
            [clojure.data.json :as json]
            [clojure.data.xml :as xml]
            [clojure.string :as str]
            [yaml.core :as yaml]
            [net.cgrand.enlive-html :as enlive]
            [feedxcavator.reply :as reply])
  (:use [clojure.java.io :only [input-stream]]
        [chime.core :only [periodic-seq]]
        clojure.tools.macro
        clojure.walk))

(def ^:const app-version "3.0.0")

(def ^:const deployment-type :private) ;; :private, :demo

(def ^:const user-code-ns "feedxcavator.code-user")

;; available in the context of request handler calls
(def ^:dynamic *servlet-context* "A servlet context instance." nil)
(def ^:dynamic *remote-addr* "Request remote address." nil)
(def ^:dynamic *app-host-scheme* "Application server host protocol scheme." "http")
(def ^:dynamic *app-host* "Application server host name (with protocol scheme)." nil)

(def ^:dynamic *current-feed* nil)
(def ^:dynamic *current-logging-source* nil)

(defn read-yaml-file [file-path]
  (yaml/from-file file-path))

(defn read-config []
  (read-yaml-file "config.yml"))

(def config (atom (read-config)))

(def exclusive-jdbc-connection (atom false))

(defn get-config []
  @config)

(defn timestamp []
  (System/currentTimeMillis))

(defn daily-at [h m]
  (let [daily-seq (periodic-seq (-> (LocalTime/of h m 0)
                                    (.atDate (LocalDate/now))
                                    (.atZone (ZoneId/systemDefault))
                                    .toInstant)
                                (Period/ofDays 1))
        now (LocalTime/now)]
    (if (or (> (.getHour now) h) (and (= (.getHour now) h) (>= (.getMinute now) m)))
      (rest daily-seq)
      daily-seq)))

(defn distinct-by [f coll]
  (loop [items coll seen #{} result []]
    (let [item (first items)]
      (if item
        (let [field (f item)]
          (if (seen field)
            (recur (next items) seen result)
            (recur (next items) (conj seen field) (conj result item))))
        result))))

(def regex-char-esc-smap
  (let [esc-chars "()&^%$#!?*."]
    (zipmap esc-chars
            (map #(str "\\" %) esc-chars))))

(defn regex-escape [string]
  (->> string
       (replace regex-char-esc-smap)
       (reduce str)))

(defn generate-uuid
  "Get globally unique identifier."
  []
  (.replaceAll (str (java.util.UUID/randomUUID)) "-" ""))

(defn to-hex-string [bytes]
  (let [formatter (Formatter.)]
    (doseq [b bytes]
      (let [arg (make-array Byte 1)]
        (aset arg 0 b)
        (.format formatter "%02x" arg)))
    (.toString formatter)))

(defn md5 [s]
  (let [algorithm (MessageDigest/getInstance "MD5")
        size (* 2 (.getDigestLength algorithm))
        raw (.digest algorithm (.getBytes s))
        sig (.toString (BigInteger. 1 raw) 16)
        padding (apply str (repeat (- size (count sig)) "0"))]
    (str padding sig)))

(defn sha1-sign [data key]
  (let [key (SecretKeySpec. (.getBytes key "utf-8") "HmacSHA1")
        mac (Mac/getInstance "HmacSHA1")]
    (.init mac key)
    (to-hex-string (.doFinal mac (.getBytes data "utf-8")))))

(defn sha256 [input]
  (let [message-digest (java.security.MessageDigest/getInstance "SHA-256")
        bytes (.digest message-digest (.getBytes input "UTF-8"))]
    (->> bytes
         (map #(format "%02x" %))
         (apply str))))

(defn generate-random [byte-length]
  (let [bytes (make-array Byte/TYPE byte-length)]
    (.nextBytes (SecureRandom.) bytes)
    (to-hex-string bytes)))

(defn url-encode-utf8 [str]
  (URLEncoder/encode str "UTF-8"))

(defn url-safe-base64enc [str]
  (Base64/encodeBase64URLSafeString (.getBytes str "UTF-8")))

(defn url-safe-base64dec [str]
  (let [dec (Base64. true)]
    (String. (.decode dec str) "UTF-8")))

(defn base64enc [str]
  (let [dec (Base64.)]
    (.encode (.getBytes str "UTF-8"))))

(defn base64dec [str]
  (let [dec (Base64.)]
    (String. (.decode dec str) "UTF-8")))

(defn base64dec->bytes [str]
  (let [dec (Base64.)]
    (.decode dec str)))

;; html ;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;

(defn html-render [nodeset]
  (apply str (enlive/emit* nodeset)))

(defn html-unescape [s]
  (str/replace (str/replace (str/replace (str/replace s "&lt;" "<") "&gt;" ">") "&amp;" "&") "&quot;" "&"))

(defn html-sanitize [content]
  (str/escape (str content) {\< "&lt;" \> "&gt;" \" "&quot;" \' "&#39;" \& "&amp;"}))

(defn html-untag [s]
  (str/replace s #"</?[a-z,A-Z]+>" ""))

(defn html-format [html]
  html)

(defn xml-format [xml]
  (when xml
    (let [correct-xml (str/replace xml #"xml:base=\"[^\"]*\"" "")
          formatted-xml (xml/indent-str (xml/parse-str correct-xml))]
      (str/replace formatted-xml "<?xml version=\"1.0\" encoding=\"UTF-8\"?><"
                   "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<"))))

;; misk ;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;

(defmacro safe-call [statement]
  `(try
    ~statement
    (catch Exception e#
      (.printStackTrace e#)
      nil)))

(defmacro safely-repeat [statement]
  `(try
     ~statement
     (catch Exception e#
       (try
         ~statement
         (catch Exception e2#)))))

(defmacro safely-repeat3 [statement]
  `(try
     ~statement
     (catch Exception e#
       (try
         ~statement
         (catch Exception e2#
           (try
             ~statement
             (catch Exception e3#)))))))

(defn kebab-to-pascal [name]
  (let [words (str/split name #"-")]
    (str/join "" (map #(str/capitalize %) words))))

(defn kebab-to-snake [name]
  (str/replace name "-" "_"))

;; application ;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;

(defn production? []
  (:production @config))

(defn get-app-host []
  (or *app-host* (:hostname @config)))

(defn get-app-host-websub []
  (:websub-hostname @config))

(defn redirect-url [url]
  (str (get-app-host) "/redirect/" (generate-random 10) "/" (url-encode-utf8 url)))

(defn redirect-url-b64 [url]
  (str (get-app-host) "/redirect-b64/" (generate-random 10) "/" (url-safe-base64enc url)))

(defn forward-url [url referer]
  (str (get-app-host) "/forward/" (generate-random 10) "/?url=" (url-encode-utf8 url) "&referer=" (url-encode-utf8 referer)))

(defn forward-url-b64 [url referer]
  (str (get-app-host) "/forward-b64/" (url-safe-base64enc (str url ";;" referer))))

(defn get-websub-url [] (str (get-app-host-websub) "/websub"))

(defn get-feed-url [feed]
  (if (:suffix feed)
    (str (get-app-host-websub) "/feed/" (:suffix feed))
    (str (get-app-host-websub) "/feed/uuid:" (:uuid feed))))

(defn fix-relative-url [url base]
  (when (not (str/blank? url))
    (let [target-url base
          target-domain (re-find #"(http.?://)?([^/]+)/?" target-url)
          target-level (get (re-find #"(.*)/[^/]*$" target-url) 1)]
      (cond
        (.startsWith url "//") (str "http:" url)
        (.startsWith url "/") (str (second target-domain) (last target-domain) url)
        (.startsWith url ".") (str target-level (.substring url 1))
        (= (.indexOf url "://") -1) (str target-level
                                         (when (not (.endsWith target-level "/")) "/")
                                         url)
        :default url))))

(defn find-header [response header]
  (second (first (filter #(= (str/lower-case header)
                             (str/lower-case (first %)))
                         (:headers response)))))

(defn response-charset [response]
  (let [content-type (find-header "Content-Type" response)]
    (when content-type
      (let [charset= (.indexOf (str/lower-case content-type) "charset=")]
        (when (>= charset= 0)
          (.substring content-type (+ charset= 8)))))))

(defn read-response [response default-charset]
  ;(java.io.StringReader. (:body response))
  (let [charset (response-charset response)
        charset (if charset
                  charset
                  (if default-charset
                    default-charset
                    "utf-8"))]
    (java.io.InputStreamReader. (:body response)
      charset)))

(defn str->enlive [s]
  (enlive/html-resource (java.io.StringReader. s)))

(defn resp->enlive
  ([response] (resp->enlive response (:charset *current-feed*)))
  ([response default-charset]
   (enlive/html-resource (read-response response default-charset))))

(defn resp->enlive-xml
  ([response] (resp->enlive-xml response (:charset *current-feed*)))
  ([response default-charset]
   (enlive/xml-resource (read-response response default-charset))))

(defn resp->str
  ([response] (resp->str response (:charset *current-feed*)))
  ([response default-charset]
   ;(:body response)
   (let [charset (response-charset response)
         charset (if charset
                   charset
                   (if default-charset
                     default-charset
                     "utf-8"))]
     (slurp (:body response) :encoding charset))))

(defn resp->bytes [response]
  (let [baos (java.io.ByteArrayOutputStream.)]
    (io/copy (:body response) baos)
    (.toByteArray baos)))

(defn string->stream
  ([s] (string->stream s "UTF-8"))
  ([s encoding]
   (-> s
       (.getBytes encoding)
       (ByteArrayInputStream.))))

(defmacro authorized [request & body]
 `(let [auth-token# (:token (feedxcavator.db/fetch :auth-token "main"))]
    (if (and auth-token# (= auth-token# (find-header ~request "x-feedxcavator-auth")))
      (do ~@body)
      (reply/permission-denied))))

(defn sanitize-path [path]
  (-> path
      (str/replace #"[^\p{L}\p{N}\-_./\\]" "_")))

(defn create-directory [path]
  (let [path (sanitize-path path)]
    (if-not (.exists (io/file path))
      (.mkdirs (io/file path)))))

(defn get-parent-directory [file-path]
  (.getParent (clojure.java.io/file file-path)))

(defn write-bytes-to-file [file-path data]
  (with-open [out (java.io.FileOutputStream. (sanitize-path file-path))]
    (.write out data)))

(defn read-file-as-bytes [file-path]
  (if (.exists (io/file (sanitize-path file-path)))
    (-> file-path io/file .toPath java.nio.file.Files/readAllBytes)
    nil))

(defn copy-file-with-backup [file-path]
  (let [file (io/file file-path)]
    (when (.exists file)
      (loop [n 1]
        (let [backup-file (io/file (str (.getParent file) "/" (.getName file) ".bak" n))]
          (if (.exists backup-file)
            (recur (inc n))
            (io/copy file backup-file)))))))

