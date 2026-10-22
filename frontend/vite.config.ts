import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    rollupOptions: {
      output: {
        // libraries in their own chunks, so no single file is over 500 kB and they cache separately from the app
        manualChunks(id) {
          if (!id.includes("node_modules")) return undefined;
          if (/[\\/]node_modules[\\/](react|react-dom|react-router|react-router-dom|scheduler)[\\/]/.test(id)) return "react";
          if (/[\\/]node_modules[\\/](react-markdown|remark-|rehype-|micromark|mdast-|hast-|unist-|unified|vfile|property-information|space-separated|comma-separated|decode-named|character-|html-url|trim-lines|devlop|bail|ccount|zwitch|longest-streak|markdown-table|estree-|style-to|inline-style|is-plain-obj|trough|extend)/.test(id)) return "markdown";
          if (/[\\/]node_modules[\\/]lucide-react[\\/]/.test(id)) return "icons";
          return "vendor";
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: true } },
  },
});
