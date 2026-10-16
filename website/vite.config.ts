import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { readFileSync } from 'node:fs';

export default defineConfig({
  server: { proxy: { '/api': { target: process.env.HOLOLAB_GATEWAY_URL || 'http://127.0.0.1:8828', changeOrigin: true } } },
  plugins: [react(), {
    name: 'localized-static-preview',
    configurePreviewServer(server) {
      const { docs, sections } = JSON.parse(readFileSync(new URL('./src/content/docs/index.json', import.meta.url), 'utf8'));
      const routes = new Set<string>(['/', '/zh-CN/', '/library', '/zh-CN/library', ...docs.map((d: { href: string }) => d.href), ...sections.flatMap((s: { href: string }) => [s.href, `/zh-CN${s.href}`])]);
      // Match production's route-file-first serving, including extensionless URLs.
      server.middlewares.use((req, _res, next) => {
        const url = new URL(req.url ?? '/', 'http://localhost');
        const route = url.pathname.replace(/\/$/, '') || '/';
        if (routes.has(route) || routes.has(`${route}/`)) req.url = `${route === '/' ? '' : route}/index.html${url.search}`;
        next();
      });
    },
  }],
});
