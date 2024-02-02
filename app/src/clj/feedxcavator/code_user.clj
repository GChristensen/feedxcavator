(ns feedxcavator.code-user
  (:require [clojure.string :as str]
            [clojure.data.json :as json]
            [clojure.tools.macro :as macro]
            [clj-time.core :as time]
            [clj-time.format :as time-fmt]
            [net.cgrand.enlive-html :as enlive]
            [feedxcavator.code-api :as api]
            [feedxcavator.websub :as websub]
            [feedxcavator.log :as log]
            [feedxcavator.db :as db]
            [feedxcavator.extraction :as extraction]
            [feedxcavator.webdriver :as webdriver]
            [etaoin.api :as browser]
            [etaoin.keys :as browser-keys]
            )
  (:use [feedxcavator.fetch :only [defproxy log-fetch-errors]]
        [feedxcavator.code :only [deftask deftask* schedule schedule-periodically
                                  defextractor defhandler]]
        [feedxcavator.webdriver :only [with-browser]]))

(def ?* enlive/select)
(defn ?1 [node-or-nodes selector] (first (enlive/select node-or-nodes selector)))
(defn ?1a [node-or-nodes selector] (:attrs (first (enlive/select node-or-nodes selector))))
(defn ?1c [node-or-nodes selector] (:content (first (enlive/select node-or-nodes selector))))
(defn <t [node-or-nodes] (str/trim (enlive/text node-or-nodes)))
(def <* api/html-render)