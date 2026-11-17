;; Scratch: press Run to evaluate; what it prints shows below. The browser runs in the worker.

(with-browser [b]
  (setv page (.open b "http://127.0.0.1:21901/youtube.html" :wait-for "#video-title"))
  (for [title (.evaluate page "[...document.querySelectorAll('#video-title')].map(e => e.textContent)")]
    (print title)))
