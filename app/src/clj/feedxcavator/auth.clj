(ns feedxcavator.auth
  (:require [compojure.core :refer :all]
            [compojure.response :refer [render]]
            [clojure.java.io :as io]
            [buddy.auth :refer [authenticated? throw-unauthorized]]
            [feedxcavator.core :as core]
            [ring.util.response :refer [response redirect content-type]]
            [buddy.hashers :as password]
            )
  (:gen-class))

(def ^:const login-page "public/login.html")

(defn login
  [request]
  (let [content (slurp (io/resource login-page))]
    (render content request)))

(defn logout
  [request]
  (-> (redirect "/login")
      (assoc :session {})))

(defn get-stored-password-hash [username]
  (let [config (core/get-config)]
    (when (= username (:user config))
      (:password-hash config))))

(defn login-authenticate
  [request]
  (let [username (get-in request [:form-params "username"])
        password (get-in request [:form-params "password"])
        password-hash (get-stored-password-hash username)
        session (:session request)]
    (if (password/check password password-hash)
      (let [next-url (get-in request [:query-params "next"] "/")
            updated-session (assoc session :identity (keyword username))]
        (-> (redirect next-url)
            (assoc :session updated-session)))
      (let [content (slurp (io/resource login-page))]
        (render content request)))))

(defn unauthorized-handler
  [request metadata]
  (cond
    (authenticated? request)
    (-> (render (slurp (io/resource "error.html")) request)
        (assoc :status 403))
    :else
    (let [current-url (:uri request)]
      (redirect (format "/login?next=%s" current-url)))))
