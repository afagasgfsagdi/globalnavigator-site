const ALLOWED_ORIGINS = new Set([
  "https://afagasgfsagdi.github.io"
]);

const json = (body, status, headers = {}) => new Response(JSON.stringify(body), {
  status,
  headers: { "Content-Type": "application/json; charset=utf-8", ...headers }
});

export default {
  async fetch(request, env) {
    const origin = request.headers.get("Origin") || "";
    if (!ALLOWED_ORIGINS.has(origin)) return json({ error: "Origin not allowed" }, 403);

    const corsHeaders = {
      "Access-Control-Allow-Origin": origin,
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
      "Access-Control-Max-Age": "86400",
      "Vary": "Origin"
    };
    const url = new URL(request.url);

    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: corsHeaders });
    if (url.pathname === "/health" && request.method === "GET") {
      return json({ ok: true, service: "GlobalNavigator Dify proxy" }, 200, corsHeaders);
    }
    if (url.pathname !== "/chat" || request.method !== "POST") {
      return json({ error: "Not found" }, 404, corsHeaders);
    }
    if (!env.DIFY_API_KEY) return json({ error: "Dify server secret is not configured" }, 503, corsHeaders);

    const contentLength = Number(request.headers.get("Content-Length") || 0);
    if (contentLength > 16_000) return json({ error: "Request is too large" }, 413, corsHeaders);

    const ip = request.headers.get("CF-Connecting-IP") || "unknown";
    const rate = await env.CHAT_RATE_LIMITER.limit({ key: ip });
    if (!rate.success) return json({ error: "Too many requests. Please wait a minute and try again." }, 429, corsHeaders);

    let input;
    try {
      const raw = await request.text();
      if (new TextEncoder().encode(raw).length > 16_000) return json({ error: "Request is too large" }, 413, corsHeaders);
      input = JSON.parse(raw);
    } catch {
      return json({ error: "Invalid JSON request" }, 400, corsHeaders);
    }

    const query = typeof input.query === "string" ? input.query.trim() : "";
    const user = typeof input.user === "string" ? input.user.trim() : "";
    const conversationId = typeof input.conversation_id === "string" ? input.conversation_id : "";
    if (!query || query.length > 10_000 || !/^[a-zA-Z0-9_-]{6,128}$/.test(user) || conversationId.length > 128) {
      return json({ error: "Invalid chat input" }, 400, corsHeaders);
    }

    let upstream;
    try {
      upstream = await fetch("https://api.dify.ai/v1/chat-messages", {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${env.DIFY_API_KEY}`,
          "Content-Type": "application/json",
          "Accept": "text/event-stream"
        },
        body: JSON.stringify({
          inputs: {},
          query,
          response_mode: "streaming",
          conversation_id: conversationId,
          user
        })
      });
    } catch {
      return json({ error: "Could not reach the Dify API" }, 502, corsHeaders);
    }

    const headers = new Headers(corsHeaders);
    headers.set("Cache-Control", "no-cache, no-transform");
    headers.set("X-Content-Type-Options", "nosniff");
    if (upstream.headers.has("Content-Type")) headers.set("Content-Type", upstream.headers.get("Content-Type"));
    if (upstream.ok) headers.set("X-Accel-Buffering", "no");
    return new Response(upstream.body, { status: upstream.status, headers });
  }
};

