import { DurableObject } from "cloudflare:workers";
import { classify, MAX_INGEST_BYTES } from "./validate";

export interface Env {
  RELAY: DurableObjectNamespace<Relay>;
  ASSETS: Fetcher;
  RELAY_TOKEN: string;
  MAX_VIEWERS?: string;
}

const SILENCE_MS = 30_000;
const TICK_MS = 10_000;
const CORS = { "access-control-allow-origin": "*", "access-control-allow-methods": "GET, OPTIONS" };

async function sha256(s: string): Promise<ArrayBuffer> {
  return crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
}

/** Constant-time bearer check: compares SHA-256 digests so length does not leak. */
async function authorised(req: Request, env: Env): Promise<boolean> {
  const m = /^Bearer (.+)$/.exec(req.headers.get("Authorization") ?? "");
  if (!m || !env.RELAY_TOKEN) return false;
  const [a, b] = await Promise.all([sha256(m[1]), sha256(env.RELAY_TOKEN)]);
  return crypto.subtle.timingSafeEqual(a, b);
}

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    const { pathname } = new URL(req.url);
    if (pathname === "/ingest" || pathname === "/live" || pathname === "/state") {
      if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });
      if (pathname !== "/state") {
        if (req.headers.get("Upgrade")?.toLowerCase() !== "websocket") {
          return new Response("Expected a WebSocket", { status: 426 });
        }
        if (pathname === "/ingest" && !(await authorised(req, env))) {
          return new Response("Unauthorized", { status: 401 });
        }
      } else if (req.method !== "GET" && req.method !== "HEAD") {
        return new Response("Method not allowed", { status: 405, headers: CORS });
      }
      return env.RELAY.get(env.RELAY.idFromName("main")).fetch(req);
    }
    // Static files are served by the assets layer before the Worker runs.
    return new Response("Not found", { status: 404 });
  },
} satisfies ExportedHandler<Env>;

export class Relay extends DurableObject<Env> {
  private lastState: string | null = null; // last valid state frame, verbatim
  private lastRx = 0; // last time a valid ingest message arrived

  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    ctx.blockConcurrencyWhile(async () => {
      const snap = ctx.storage.kv.get<{ state: string; rx: number }>("snap");
      if (snap) {
        this.lastState = snap.state;
        this.lastRx = snap.rx;
      }
    });
    // Keep-alive from viewers is answered without waking the object.
    ctx.setWebSocketAutoResponse(new WebSocketRequestResponsePair("ping", "pong"));
  }

  private silent(now = Date.now()): boolean {
    return now - this.lastRx > SILENCE_MS;
  }

  private broadcast(msg: string): void {
    for (const ws of this.ctx.getWebSockets("viewer")) {
      try {
        ws.send(msg);
      } catch {
        /* socket is gone; the runtime will close it */
      }
    }
  }

  private async ensureAlarm(): Promise<void> {
    if ((await this.ctx.storage.getAlarm()) === null) await this.ctx.storage.setAlarm(Date.now() + TICK_MS);
  }

  async fetch(req: Request): Promise<Response> {
    const { pathname } = new URL(req.url);
    if (pathname === "/state") return this.stateResponse();

    const pair = new WebSocketPair();
    const client = pair[0];
    const server = pair[1];

    if (pathname === "/ingest") {
      for (const old of this.ctx.getWebSockets("ingest")) {
        try {
          old.serializeAttachment({ replaced: true });
          old.close(1000, "replaced by a newer ingest connection");
        } catch {
          /* already closed */
        }
      }
      this.ctx.acceptWebSocket(server, ["ingest"]);
      this.lastRx = Date.now();
    } else if (pathname === "/live") {
      const max = Math.max(1, Number(this.env.MAX_VIEWERS) || 400);
      if (this.ctx.getWebSockets("viewer").length >= max) {
        return new Response("Too many viewers right now", { status: 503, headers: { "retry-after": "30" } });
      }
      this.ctx.acceptWebSocket(server, ["viewer"]);
      if (this.lastState) server.send(this.lastState);
      server.send(JSON.stringify({ t: "viewers", n: this.ctx.getWebSockets("viewer").length }));
      if (this.silent()) server.send(this.offlineFrame());
    } else {
      return new Response("Not found", { status: 404 });
    }
    await this.ensureAlarm();
    return new Response(null, { status: 101, webSocket: client });
  }

  private offlineFrame(): string {
    return JSON.stringify({ t: "offline", since: this.lastRx || Date.now() });
  }

  private stateResponse(): Response {
    const headers = { "content-type": "application/json", "cache-control": "no-store", ...CORS };
    if (!this.lastState) return new Response('{"t":"offline"}', { headers });
    if (!this.silent()) return new Response(this.lastState, { headers });
    // Silent harness: say so, but keep the last known counts for the page.
    const body = `{"t":"offline","since":${this.lastRx},"last":${this.lastState}}`;
    return new Response(body, { headers });
  }

  async webSocketMessage(ws: WebSocket, message: string | ArrayBuffer): Promise<void> {
    if (!this.ctx.getTags(ws).includes("ingest")) return; // viewers: ignored
    if ((ws.deserializeAttachment() as { replaced?: boolean } | null)?.replaced) return; // superseded ingest
    if (typeof message !== "string" || message.length > MAX_INGEST_BYTES) return;
    const kind = classify(message);
    if (!kind) return;
    const now = Date.now();
    this.lastRx = now;
    if (kind === "state") {
      this.lastState = message;
      this.ctx.storage.kv.put("snap", { state: message, rx: now });
    }
    this.broadcast(message);
  }

  async webSocketClose(ws: WebSocket, code: number): Promise<void> {
    try {
      ws.close(code >= 1000 && code < 5000 && code !== 1005 && code !== 1006 ? code : 1000, "");
    } catch {
      /* already closed */
    }
  }

  async webSocketError(ws: WebSocket): Promise<void> {
    try {
      ws.close(1011, "error");
    } catch {
      /* already closed */
    }
  }

  async alarm(): Promise<void> {
    if (this.ctx.getWebSockets().length === 0) return; // nobody here: stop ticking, sleep for free
    const now = Date.now();
    this.broadcast(JSON.stringify({ t: "viewers", n: this.ctx.getWebSockets("viewer").length }));
    if (this.silent(now)) this.broadcast(this.offlineFrame());
    await this.ctx.storage.setAlarm(now + TICK_MS);
  }
}
