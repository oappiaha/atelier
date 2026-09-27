import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: 'autoUpdate',
      workbox: {
        // Keep the shell available offline; secondary routes cache only when visited.
        globPatterns: ['**/*.{html,css,png,ico,svg,webmanifest}', 'assets/index-*.js', 'assets/api-*.js'],
        runtimeCaching: [{
          urlPattern: ({ url, sameOrigin }) => sameOrigin && /\/assets\/.*\.js$/.test(url.pathname),
          handler: 'CacheFirst',
          options: { cacheName: 'atelier-route-code', expiration: { maxEntries: 60, maxAgeSeconds: 30 * 24 * 3600 } },
        }],
      },
      manifest: {
        name: 'Atelier',
        short_name: 'Atelier',
        description: 'A design archive, and the Wada colorway studio inside it',
        theme_color: '#EFF3F9',
        background_color: '#EFF3F9',
        display: 'standalone',
        start_url: '/',
        icons: [
          { src: '/icon-192.png', sizes: '192x192', type: 'image/png' },
          { src: '/icon-512.png', sizes: '512x512', type: 'image/png' },
        ],
        // A6: share from Instagram/Safari lands in the Inbox as Inspiration
        share_target: {
          action: '/share-target',
          method: 'POST',
          enctype: 'multipart/form-data',
          params: {
            title: 'title',
            text: 'text',
            url: 'url',
            files: [{ name: 'media', accept: ['image/*'] }],
          },
        },
      } as never,
    }),
  ],
  server: {
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true, rewrite: p => p.replace(/^\/api/, '') },
    },
  },
})
