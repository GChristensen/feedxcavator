import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// The built console is served by Django from feedxcavator/static/console.
// `vite build --mode demo` (npm run build:demo) builds the static demo with its mock backend into ../demo,
// served by GitHub Pages; .env.demo sets VITE_DEMO.
export default defineConfig(({ mode }) => {
  const demo = mode === 'demo'
  return {
    plugins: [vue()],
    base: demo ? './' : '/static/console/',
    build: {
      outDir: demo ? '../demo' : '../feedxcavator/static/console',
      // demo/ also holds the sample workspace and the recording home: demo-post.mjs clears only old assets
      emptyOutDir: !demo,
      chunkSizeWarningLimit: 1500,
      // the demo also opens from disk (file://), where browsers refuse module scripts: one classic script
      ...(demo ? {
        modulePreload: false,
        rollupOptions: { output: { format: 'iife', inlineDynamicImports: true } },
      } : {}),
    },
    server: {
      proxy: demo ? {} : {
        '^/(front|feed|backend|login|logout|image|handler|api)': 'http://localhost:10000',
      },
    },
  }
})
