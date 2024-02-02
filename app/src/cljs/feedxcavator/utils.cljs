(ns feedxcavator.utils
  (:require [feedxcavator.ajax :as ajax]))

(defn get-tasks []
  (ajax/get-edn "/front/get-tasks"
                (fn [tasks]
                  (set! (.-feedxcavatorTasks js/window) tasks))))
