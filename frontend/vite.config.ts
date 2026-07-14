import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

// Dev server proxies /api/* to the backend on :8000 so we don't have
// to deal with cross-origin cookies. Production will serve frontend
// and backend from the same origin. The backend mounts every router
// under /api, so the proxy must forward the path UNCHANGED — do not
// rewrite/strip the /api prefix (that 404s every route, incl. login).
//
// VitePWA makes the app installable ("Add to Home Screen") on phones:
// it emits a web manifest + a service worker that precaches the built
// shell (JS/CSS/icons) so DESS opens instantly and launches
// full-screen. IMPORTANT: the service worker never caches /api/* — all
// contact data stays server-side behind the privacy filter and off the
// device. navigateFallbackDenylist keeps API requests on the network;
// no runtimeCaching means no API response is ever stored locally.
export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: "autoUpdate",
      injectRegister: "auto",
      manifest: {
        name: "DESS CRM",
        short_name: "DESS",
        description: "Voice-first team CRM + a deterministic warm-introduction engine.",
        id: "/",
        start_url: "/",
        scope: "/",
        display: "standalone",
        orientation: "portrait",
        background_color: "#F5EEE0",
        theme_color: "#C8202F",
        icons: [
          {
            src: "/icons/pwa-192.png",
            sizes: "192x192",
            type: "image/png",
            purpose: "any",
          },
          {
            src: "/icons/pwa-512.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "any",
          },
          {
            src: "/icons/pwa-maskable-512.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "maskable",
          },
        ],
      },
      workbox: {
        globPatterns: ["**/*.{js,css,html,svg,png,ico,woff,woff2}"],
        navigateFallback: "/index.html",
        navigateFallbackDenylist: [/^\/api\//],
        cleanupOutdatedCaches: true,
        clientsClaim: true,
      },
      // Keep the SW out of `npm run dev` so HMR and the API proxy behave
      // normally; the PWA only activates in the production build.
      devOptions: { enabled: false },
    }),
  ],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
