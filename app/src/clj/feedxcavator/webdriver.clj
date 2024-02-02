(ns feedxcavator.webdriver
  (:require [clojure.string :as str]
            [etaoin.api :as browser]
            [feedxcavator.core :as core]))

(def browser-config (atom {}))

(def ^:dynamic *current-driver* nil)

(defn config [browser conf]
  (reset! browser-config {:browser browser :config conf})
  (when-let [profile-dir (:profile conf)]
    (core/create-directory profile-dir)))

(defmacro with-browser [& body]
  `(browser/with-driver (:browser @browser-config) (:config @browser-config) ~'driver
                        (binding [*current-driver* ~'driver]
                          ~@body)))

(defn apply-selectors
  ([]
   (apply-selectors *current-driver* core/*current-feed*))
  ([driver feed]
   (let [selectors (:selectors feed)
         headline-sel (:css (:item selectors))]
     (when headline-sel
       (let [headlines
             (doall (for [headline-elt (core/safe-call (browser/query-all driver {:css headline-sel}))]
               (letfn [(get-content [element]
                         (when element
                           (let [content (core/safe-call (browser/get-element-text-el driver element))]
                             (when (not (str/blank? content))
                               content))))
                       (get-attr-content [element attr]
                         (when element
                           (core/safe-call (browser/get-element-attr-el driver element attr))))
                       (extract-part [part & {:keys [default]}]
                         (let [selector (selectors part)
                               css (:css selector)
                               element (if (= ":root" css)
                                           headline-elt
                                           (when css (core/safe-call (browser/child driver headline-elt {:css css}))))
                               select-attrs (if (seq (:attrs selector))
                                              (:attrs selector)
                                              (when default [default]))]
                           (if (and selector (not (empty? select-attrs)))
                             (some #(get-attr-content element %) select-attrs)
                             (get-content element))))]
                 (let [headline-data {:title   (extract-part :title)
                                      :link    (core/fix-relative-url (extract-part :link :default :href) (:source feed))
                                      :summary (extract-part :summary)
                                      :image   (core/fix-relative-url (extract-part :image :default :src) (:source feed))
                                      :author  (extract-part :author)}]
                   (when (some #(and (string? (second %)) (not (str/blank? (second %)))) headline-data)
                     headline-data)))))]
         (filter #(not (nil? %)) headlines))))))