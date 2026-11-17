// The demo build writes into demo/, which also holds the sample workspace (demo/workspace) and the recording home
// (demo/home), so vite does not empty it.
//   node demo-post.mjs clean   before the build: removes the previous build's assets
//   node demo-post.mjs         after the build: makes index.html work from file:// and writes demo/console.html,
//                              where the 3.0.0 demo lived and the landing page links to
import { readFile, rm, writeFile } from 'node:fs/promises'

const demo = (path) => new URL('../demo/' + path, import.meta.url)

if (process.argv[2] === 'clean') {
  await rm(demo('assets'), { recursive: true, force: true })
} else {
  // vite emits <script type="module" crossorigin> and <link crossorigin>; from file:// browsers block both.
  // The bundle is a classic iife (vite.config.js), so a deferred plain script runs it the same way.
  let html = await readFile(demo('index.html'), 'utf8')
  html = html
    .replace(/<script type="module" crossorigin src=/g, '<script defer src=')
    .replace(/ crossorigin(?=[\s>])/g, '')
    .replace('href="/static/console/favicon.ico"', 'href="./favicon.ico"')
  await writeFile(demo('index.html'), html)
  await writeFile(demo('console.html'), html)
  console.log('demo/index.html adjusted for file://, demo/console.html written')
}
