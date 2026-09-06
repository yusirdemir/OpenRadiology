import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { defineConfig, type Plugin } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

/**
 * Browser development against a running sidecar.
 *
 * In the packaged application the Tauri shell reads the sidecar's handshake
 * line and injects the port and token into the web view. In a plain browser
 * there is no shell, so the dev server reads the same handshake file the MCP
 * bridge uses and proxies `/api` with the Authorization header attached. That
 * keeps the token out of the page and out of the browser's history, and means
 * `npm run dev` needs no configuration beyond a running `openrad-server`.
 */
function sidecarProxy(): Plugin {
  const handshakePath =
    process.platform === "darwin"
      ? join(homedir(), "Library", "Application Support", "OpenRadiology", "bridge.json")
      : process.platform === "win32"
        ? join(process.env.APPDATA ?? join(homedir(), "AppData", "Roaming"), "OpenRadiology", "bridge.json")
        : join(process.env.XDG_STATE_HOME ?? join(homedir(), ".local", "state"), "openradiology", "bridge.json");

  const read = (): { port: number; token: string } | null => {
    try {
      const parsed = JSON.parse(readFileSync(handshakePath, "utf8"));
      return parsed?.port && parsed?.token ? { port: parsed.port, token: parsed.token } : null;
    } catch {
      return null;
    }
  };

  return {
    name: "openrad-sidecar-proxy",
    configureServer(server) {
      server.middlewares.use("/api", (request, response) => {
        response.socket?.setNoDelay(true);
        const handshake = read();
        if (!handshake) {
          response.statusCode = 503;
          response.setHeader("Content-Type", "application/json");
          response.end(
            JSON.stringify({ error: `No sidecar handshake at ${handshakePath}. Start openrad-server first.` }),
          );
          return;
        }
        import("node:http").then(({ request: proxy }) => {
          const upstream = proxy(
            {
              host: "127.0.0.1",
              port: handshake.port,
              path: request.url ?? "/",
              method: request.method,
              headers: { ...request.headers, host: `127.0.0.1:${handshake.port}`, authorization: `Bearer ${handshake.token}` },
            },
            (upstreamResponse) => {
              response.statusCode = upstreamResponse.statusCode ?? 502;
              for (const [key, value] of Object.entries(upstreamResponse.headers)) {
                if (value !== undefined) response.setHeader(key, value);
              }
              /*
               * Bounded bodies are collected and written once.
               *
               * Piping writes the headers and then each chunk separately, and
               * on a machine whose loopback punishes the write-write-read
               * pattern that second hop costs as much as the first -- about
               * two hundred milliseconds per response. Everything this proxy
               * carries is small, because the viewer asks for byte ranges, so
               * buffering into a single `end` removes the interaction. Event
               * streams keep piping: not being buffered is their whole point.
               */
              const kind = String(upstreamResponse.headers["content-type"] ?? "");
              if (kind.startsWith("text/event-stream")) {
                upstreamResponse.pipe(response);
                return;
              }
              const parts: Buffer[] = [];
              upstreamResponse.on("data", (chunk: Buffer) => parts.push(chunk));
              upstreamResponse.on("end", () => response.end(Buffer.concat(parts)));
              upstreamResponse.on("error", () => response.end());
            },
          );
          upstream.setNoDelay(true);
          upstream.on("error", (error) => {
            response.statusCode = 502;
            response.end(JSON.stringify({ error: String(error) }));
          });
          request.pipe(upstream);
        });
      });
    },
  };
}

export default defineConfig({
  plugins: [react(), tailwindcss(), sidecarProxy()],
  clearScreen: false,
  server: { port: 5273, strictPort: true, host: "127.0.0.1" },
  build: { target: "es2022", sourcemap: true, chunkSizeWarningLimit: 900 },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
