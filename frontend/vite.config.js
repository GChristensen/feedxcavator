import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// The built console is served by Django from feedxcavator/static/console (STK-3).
export default defineConfig({
  plugins: [vue()],
  base: '/static/console/',
  build: {
    outDir: '../feedxcavator/static/console',
    emptyOutDir: true,
    chunkSizeWarningLimit: 1500,
  },
  server: {
    proxy: {
      '^/(front|feed|backend|login|logout|image|handler|api)': 'http://localhost:10000',
    },
  },
})
