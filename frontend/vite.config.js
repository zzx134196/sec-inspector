import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import legacy from '@vitejs/plugin-legacy'

export default defineConfig({
  plugins: [
    react(),
    legacy({
      targets: ['Chrome >= 63', 'Firefox >= 60', 'Safari >= 11.1'],
      additionalLegacyPolyfills: ['regenerator-runtime/runtime'],
      modernPolyfills: [
        'es.promise',
        'es.promise.finally',
        'es.array.flat',
        'es.array.flat-map',
        'es.object.from-entries',
        'es.string.match-all',
        'es.global-this',
      ],
      renderLegacyChunks: true,
    }),
  ],
  esbuild: {
    target: 'es2015',
  },
  build: {
    target: 'es2015',
    cssTarget: 'chrome63',
    minify: 'terser',
  },
  server: {
    port: 8003,
    host: '0.0.0.0',
    proxy: {
      '/api': {
        target: process.env.VITE_API_BASE || 'http://localhost:8022',
        changeOrigin: true,
      },
    },
  },
})
