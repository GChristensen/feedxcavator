;; The simplest browser extractor in Hy: open the channel in Camoufox, wait for the video cards,
;; and read them with the selectors of feed.yaml.

(defextractor youtube-channel [feed]
  (with-browser [b]
    (-> (.open b (:source feed) :wait-for "ytd-rich-item-renderer" :scroll 1 :timeout 60)
        (.apply-selectors feed))))
