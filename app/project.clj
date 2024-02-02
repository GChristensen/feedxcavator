(defproject feedxcavator "3.0.0-SNAPSHOT"
  :description "Programmable feed extractor and server"
  :min-lein-version "2.0.0"
  :dependencies [[org.clojure/clojure "1.11.1"]
                 [org.clojure/core.async "1.6.681"]
                 [org.clojure/tools.macro "0.1.5"]
                 [org.clojure/data.json "0.2.6"]
                 [org.clojure/data.xml "0.0.8"]
                 [org.clojure/clojurescript "1.10.520"]
                 [compojure "1.6.1"]
                 [ring/ring-core "1.11.0"]
                 [ring/ring-defaults "0.3.2"]
                 [org.slf4j/slf4j-log4j12 "2.0.7"]
                 [com.github.seancorfield/next.jdbc "1.3.909"]
                 [com.h2database/h2 "2.2.224"]
                 [ring/ring-json "0.5.1"]
                 [enlive "1.1.6"]
                 [hiccup "1.0.5"]
                 [crate "0.2.4"]
                 [org.flatland/ordered "1.5.7"]
                 [prismatic/dommy "1.1.0"]
                 [cljs-ajax "0.8.0"]
                 [io.forward/yaml "1.0.9"]
                 [org.htmlunit/htmlunit "3.10.0"
                  :exclusions [commons-io
                               org.eclipse.jetty.websocket/websocket-client]]
                 [org.sejda.imageio/webp-imageio "0.1.6"]
                 [clj-time "0.15.2"]
                 [clj-http "3.12.3"]
                 [jarohen/chime "0.3.3"]
                 [etaoin "1.0.40"]
                 [buddy/buddy-auth "3.0.323"]
                 [buddy/buddy-hashers "1.4.0"]
                 ]
  :plugins [[lein-ring "0.12.5"]
            [lein-cljsbuild "1.1.7"]]
  :ring {:handler feedxcavator.app/feedxcavator-app-handler
         :init feedxcavator.app/feedxcavator-app-init
         :port 10000
         :stacktraces? true
         :nrepl {:start? true :port 55550}}
  :source-paths ["src/clj"]
  :java-source-paths ["src/java"]
  ;:resource-paths ["resources/public"]
  :uberjar-name "feedxcavator.jar"
  :profiles
  {:dev {:dependencies [[javax.servlet/servlet-api "2.5"]
                        [ring/ring-mock "0.3.2"]
                        ]}
   :provided {:dependencies []}}
   :cljsbuild {
              :builds [{
                        ; The path to the top-level ClojureScript source directory:
                        :source-paths ["src/cljs"]
                        ; The standard ClojureScript compiler options:
                        ; (See the ClojureScript compiler documentation for details.)
                        :compiler     {
                                       :output-to   "resources/public/js/main.js"
                                       :optimizations :advanced ;:whitespace ;:advanced
                                       :pretty-print  false ;true ;false
                                       :infer-externs true
                                       :externs ["src/js/externs.js"
                                                 "resources/public/js/jstree.js"]
                                       }}]})