# Feedxcavator

This application provides a framework to convert various data sources to
[RSS](https://en.wikipedia.org/wiki/RSS) and serve the resulting feeds.
Feedxcavator allows program feed extraction in a simple Clojure-based
[DSL](https://en.wikipedia.org/wiki/Domain-specific_language) directly through
the web UI (see the [demo](https://gchristensen.github.io/feedxcavator/demo/console.html)). 
This allows to perform arbitrary
transformations on the extracted content, such as sorting or filtering.
There is also a built-in [WebSub](https://en.wikipedia.org/wiki/WebSub) hub to push
real-time feed updates to RSS readers.

## Semi-automatic feed extraction

Feedxcavator can transform web pages to feeds of various formats using CSS selectors. Let's create
a feed definition that takes several pages of a site and converts them to RSS
which will be available by the following URL: `http://<feedxcavator host>:10000/feed/my-first-feed`. 

Place the following [YAML](https://en.wikipedia.org/wiki/YAML) text into the feed definition editor:

```yaml
title: My first feed
suffix: my-first-feed
source: https://example.com
selectors:
  item: article
  title: h2
  link: h2 a
  image: img::src-lazy||src
pages:
  include-source: false
  path: '/page/%n'
  increment: 1
  start: 1
  end: 5
```

This text will instruct Feedxcavator to visit URLs from https://example.com/page/1 to 
https://example.com/page/5 and extract HTML content from tags defined by CSS rules 
in the `selectors` section. Feedxcavator will do this every time the URL 
`http://<feedxcavator host>:10000/feed/my-first-feed` is visited.

By default, the headline link is extracted from the `href` attribute
of the given tag, and image link is extracted from the `src` attribute. 
But the definition above tells Feedxcavator
to extract image URL from the `src-lazy` attribute and then from `src` attribute 
if the previous one is absent.

## Feed configuration parameters
Below is the full list of the configuration parameters that Feedxcavator understands: 

```yaml
title: Feed title
suffix: feed-suffix # a string that defines feed URL at: https://<your project id>.appspot.com/feed/feed-suffix
source: https://example.com # address of the source page
charset: utf-8 # source charset, optional; use if automatic charset resolution does not work for some reason
output: rss # also 'json-feed', 'json' and 'edn' - useful for further aggregation
parallel: true # feeds marked with parallel: false will be processed sequentially
group: group/subgroup # group path of the feed in UI
task: task-name # the name of a background feed extraction task
proxy: 'proxy name' # the name of a proxy used to fetch feed contents
timeout: 0 # HTTP request timeout 
selectors: # CSS selectors of feed elements
  item: div.headline  #container element of the headline item
  title: h3 # item title
  link: a # item link
  summary: div.preview # item description
  image: div.cover img # item cover image
  author: div.user-name # the name of item author
pages: # pagination parameters
  include-source: true # include the URL supplied in the "source" parameter into the set of processed pages
  path: '/?page=%n' # page path template, appended to the source URL
  increment: 1 # increment of the path parameter
  start: 2 # the initial value of the path parameter
  end: 3 # the maximum value of the path parameter
  delay: 0 # delay between page requests
filter: # filtering options
  history: true # feed will omit items seen at the previous time if true  
  content: title+summary # also 'title', the feed will be filtered by the content of these fields if specified
  wordfilter: default # the name of a word-filter to apply
realtime: true # use WebSub to instantly publish a background feed
partition: 100 # send a background feed to the aggregator by parts with the specified number of items in each 
extractor: extractor-function-name # name of the Clojure extractor function to invoke
params: [any additional data, [123, 456]] # arbitrary feed parameters available at the Clojure code
```

## Supported CSS subset

The following CSS features are currently supported:

```* Elements:                       div
* IDs:                            div#some-id
* Classes:                        div.some-class
* Descendants:                    h1 a
* Direct hierarchy:               div#some-id > h1.some-class > a
* Attribute presents:             a[attr]
* Attribute value:                a[attr="value"]
* Attribute starts:               a[attr^="substr"]
* Attribute contains:             a[attr*="substr"]
* Attribute ends:                 a[attr$="substr"]
* Pseudo-classes:                 h1:first-child
* Parameterized pseudo-classes:   a:nth-of-type(3) 
```

In Feedxcavator CSS the root node (item) could be referred to as
`:root`. An attribute name could be appended to the selector through the double colon: `::`. 
It is possible to specify several attributes using the double pipe: `||`. 
For example: `:root > div > img::src-lazy||src`. The value will be extracted from the first 
non-empty attribute. 

Feedxcavator uses [enlive](https://github.com/cgrand/enlive#readme) library
for HTML processing and internally converts all CSS selectors into [enlive
selectors](http://enlive.cgrand.net/syntax.html). It is possible to use elinve
selectors directly: Feedxcavator will not try to convert a selector to the enlive
representation if it resembles a readable Clojure datum 
(e.g. **[[:tr (attr= :colspan "2")] :a]**).

## Custom feed extractors

Feedxcavator will use the provided CSS selectors to automatically extract data if the
"extractor" field of the YAML config is not specified. Otherwise, it assumes
that this field contains the name of an extractor function that should be invoked
to provide the feed content. Extractor functions should be coded in Clojure programming
language at the Feedxcavator "Extractors" tab.

Extractors are defined with the `defextractor` macro.
Extractor functions accept the feed definition as a Clojure map and should return a collection of
Clojure maps with the following set of fields:

```clojure
{
  :title "Headline Title" 
  :link "Headline URL" 
  :author "Author name (optional)"
  :summary "Article summary (optional)" 
  :image "Image URL (optional)"
  :image-type "image/mime-type (optional)"
}
```

When serializing into XML or JSON Feed formats all other fields are ignored, 
but if the feed is serialized into plain JSON or EDN, the result may contain any set of fields.
Let's define an extractor that sorts its headlines by title:

```clojure
(defextractor my-first-extractor [feed]
    (let [headlines (api/parse-html-page feed (:source feed))]
         (sort-by :title headlines)))
```

## Fetching web-resources

The `api/fetch-url` function allows obtaining contents of any web resource. It tries to not throw
exceptions and returns `nil` if a network or HTTP error has occurred. To find out what is happening 
use the `api/get-last-http-error` function to get the last HTTP response code 
and `api/get-last-http-response` to get the whole last [ring](https://github.com/ring-clojure) response.
When debugging, it may be more convenient to wrap it into the `log-fetch-errors` macro.
The `:as` keyword argument of `api/fetch-url` allows to convert the response to the corresponding format,
defined by the argument value:
  * `:html` - parsed enlive HTML representation
  * `:xml` - parsed enlive XML representation
  * `:json` - parsed JSON data
  * `:string` - response text
  * `:bytes` - byte array
 
A raw ring response is returned when the argument is omitted. Please see the Feedxcavator 
"API" tab for more info.

## Selecting elements

The usual workflow is to fetch a web resource as an Enlive document (with the `:as :html` arguments
passed to `api/fetch-url`)
and use the [Enlive](https://github.com/cgrand/enlive) library to retrieve the required parts of it.
Because the Clojure code that does it may look noisy and verbose, Feedxcavator provides some 
handy shortcuts: 

```clojure
(?* node-or-nodes selector) ;; select multiple nodes from an enlive HTML node
                            ;; the equivalent of (enlive/select node-or-nodes selector)
(?1 node-or-nodes selector) ;; select the first node that matches the selector
                            ;; the equivalent of (first (enlive/select node-or-nodes selector))
(?1a node-or-nodes selector) ;; return the map of attributes of the first node that matches the selector
                             ;; the equivalent of (:attrs (first (enlive/select node-or-nodes selector))))
(?1c node-or-nodes selector) ;; return contained nodes from the first node that matches the selector
                             ;; the equivalent of (:content (first (enlive/select node-or-nodes selector))))
(<t node-or-nodes) ;; return text content (without tags) of the given enlive nodes
                   ;; the equivalent of (str/trim (enlive/text node-or-nodes) 
(<* node-or-nodes) ;; return the outer HTML of the given enlive nodes
                   ;; the equivalent of (apply str (enlive/emit* nodeset))

```


## Extracting feeds in the background

Feeds that do not have the `task` attribute in their YAML definition are
extracted each time when the Feedxcavator feed URL is visited. Feeds that have
this attribute are extracted in the background, and their produced feed content is
stored in the database. It is then served when the user or feed reader visits the feed URL.
This approach is suitable for the feeds that require complex and time-consuming
processing. To trigger extraction of the background feed it is necessary to
schedule the feed task with the `schedule` macro at the Feedxcavator "Tasks"
tab. For example, if we have the following feed definition:

```yaml
title: My background feed
suffix: my-background-feed
source: https://example.com
task: my-task
...
```

We need to provide the following statement at the "Tasks" tab to schedule its extraction
at 12:00 of the local time:

```clojure
(schedule my-task 12 0)
```

A task could be scheduled several times.

The Macro `deftask*` allows to define composite tasks that consist of several
individual tasks. The subtasks may be executed in parallel. The tasks specified in the 
:on-completion parameter are executed when all subtasks are completed.

```clojure
(deftask* morning-feeds [news articles papers]
          :on-completion [aggregate-feeds remind-me])
```


It is possible to run a task at any time through the task context menu at the task list:

![run task](https://raw.githubusercontent.com/GChristensen/feedxcavator/main/media/tasks-context-menu.png)


## Handlers

Feedxcavator handlers allow to create a custom web API to manipulate your feeds.
The `defhandler` macro defines a function that will be called when the 
`https://<feedxcavator host>:10000/handler/handler-function-name` URL is visited.
A handler function can accept values of URL query parameters as arguments or a raw ring request if 
the `request` symbol is specified instead of the macro argument list as in the example below.

If handler name is prefixed with the `^:auth` metadata tag, the request will require authorization
by "x-feedxcavator-auth" header, the value of which you may find at the settings tab.

```clojure
(defhandler my-first-handler [param]
  (log/write param)
  (reply/text-page "OK"))

(defhandler ^:auth my-secure-handler request
  (if (= (:request-method request) :get)  ;; for the GET request return kitty feed for processing
    (let [feed (db/find-feed :suffix "kitty-site")
          output (db/fetch-feed-output (:uuid feed))]
      (reply/web-page (:content-type output) (:output output)))
    (let [feed (db/find-feed :suffix "kitty-site")] 
      (db/store-feed-output! (:uuid feed) ;; store the externally processed output otherwise
                             {:content-type "application/json" 
                              :output (slurp (:body request))})
      (reply/text-page "OK"))))
```

## Logging

Feedxcavator supports rudimentary logging through the `log/write` function. It accepts 
a string, object or Throwable and prints its content at the "Log" tab. Optionally, one of the
following log levels may be specified as the first argument: 

* `:info`
* `:warning`
* `:error`

## Word filters

It is possible to apply word filters to the `title` and `summary` fields of the generated headlines
to omit certain content from the resulting feeds.
The name of the used word filter may be specified in the "wordfilter" field of the YAML config.
The word filter with the name "default" is used when filtering is enabled without the specified
filter. 

Word filters can contain ordinary strings and regular expressions (it is often useful to
place a word boundary \b to avoid excessive matching). Currently, there is no GUI to manage word-filter contents -
words or regular expressions could be added programmatically from the "Scratch" tab or through
the Feedxcavator REST API. The author uses [these](https://gist.github.com/GChristensen/c4be3bb8508ad13d982c2f57ac302eb8)
commands for the [iShell browser extension](https://gchristensen.github.io/ishell).

## Clojure code examples

```clojure
;; task setup 

(deftask* morning-feeds [news forums])

;; all time values are in the local time
(schedule news 12 00)
(schedule news 17 00)
(schedule forums 15 00)
(schedule morning-feeds 07 00)


;; utility functions

;; For illustrative purposes, the parse-page function defined below transforms the page 
;; fetched by the supplied URL to a list of headlines with the following fields:
;; {
;;  :title "headline title" 
;;  :link "headline url" 
;;  :summary "article summary" 
;;  :image "image url"
;;  :author "author name"
;;  :html <enlive html representation of the headline container element (item)>
;; }
;; It behaves exactly as the built-in api/parse-html-page function. The fields listed
;; above come from api/apply-selectors.   
(defn parse-page [feed url]
  (when-let [doc-tree (api/fetch-url url :as :html)]
    (api/apply-selectors doc-tree feed)))


;; extractors

;; Fetch some threads from a set of forums of the Bulletin Board. The "params" property 
;; of the feed config should contain an array of numeric ids of the desired forums.
(defextractor bb-extractor [feed]
  (apply concat
         (for [forum (:params feed)]
           (let [forum-url (str (:source feed) forum) ; append forum id to the base url
                 ;; stage 1: extract thread URLs from forum pages (the corresponding  
                 ;; selectors should be specified in the feed settings) using the utility 
                 ;; function defined above 
                 threads (->> (parse-page forum-url)
                              ;; and filter out previously seen urls
                              (api/filter-history! forum-url))]                                             
             (when (seq threads)
               ;; stage 2: fetch thread pages and extract HTML contents  
               ;; of the first post as string
               (for [thread threads]
                 (let [thread-node (api/fetch-url (:link thread) :as :html)]
                   (log/write (str "visited: " (:link thread)))
                   (assoc thread
                     ;; unescape special HTML character entities
                     :title (api/html-unescape (:title thread))
                     ;; get content of the src field of the first <img> tag 
                     :image (:src (?1a thread-node [:.post_text :img]))
                     ;; get rendered HTML of the first tag with .post_text class 
                     :summary (<* (?1 thread-node [:.post_text]))))))))))

;; Extract data from JSON API. The "params" field of the YAML config should contain the 
;; value of the user_id URL parameter.
(defextractor json-api-extractor [feed]
    (let [api-token "..."
          api-version "1"
          url (str "https://json.api/method/data.get?user_id=" (:params feed) 
                   "&access_token=" api-token "&v=" api-version)
          content (api/fetch-url url :as :json)
          posts (:items (:response content))
          headlines (for [p posts]
                      {
                       :title (:title p)
                       :link (:url p)
                       :summary (:text p)
                       :image (or (:photo_640 (:attachments p))
                                  (:photo_320 (:attachments p)))
                      })]
      (sort-by :link #(compare %2 %1) headlines)))

      
;; Transform another RSS feed (just turn titles upper-case).
(defextractor rss-extractor [feed]
  (let [doc-tree (api/fetch-url (:source feed) :as :xml)]
    (for [i (?* doc-tree [:item])]
      (let [tag-content #(<t (?1 i [%]))]
         {
          :title (str/upper-case (tag-content :title))
          :link (tag-content :link)
          :summary (tag-content :description)
         }))))
```

## Installation

Feedxcavator is a web server that runs on port 10000 by default.
It requires [Java development kit](https://jdk.java.net/) v17+ available through the system PATH variable.
To launch Feedxcavator just run `feedxcavator.cmd` or `feedxcavator.sh` from the distribution
and open http://localhost:10000 in the browser.

Please use a HTTPS enabled gateway when deploying in open internet.

## Building

The application is built with [lein ring](https://github.com/weavejester/lein-ring). 
Issue `lein ring server` in the project directory to run a debug server.
The command `lein ring uberjar` will create an executable JAR file with all 
necessary dependencies.

## Using with NewsBlur

Feedxcavator could be seamlessly used with [NewsBlurMod](https://github.com/GChristensen/NewsBlurMod).
It could not provide smooth real-time feed updates to the original NewsBlur as it throttles 
incoming WebSub push requests. Because NewsBlur(Mod) runs inside Docker, Feedxcavator either
should also be put into a container or use a domain name other than localhost to connect 
to a NewsBlur instance deployed on the same machine. 
Please refer Docker documentation for more details. 

## License

(c) g/christensen

Distributed under the GNU GPL v3.