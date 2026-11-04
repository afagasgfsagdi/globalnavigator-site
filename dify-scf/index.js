const http = require('node:http');
const { Readable } = require('node:stream');

const PORT = Number(process.env.PORT || 9000);
const ALLOWED_ORIGINS = new Set([
  'https://afagasgfsagdi.github.io',
]);
const WINDOW_MS = 60_000;
const MAX_REQUESTS_PER_IP = 12;
const MAX_BODY_BYTES = 16_000;
const buckets = new Map();

function sendJson(res, status, body, headers = {}) {
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store',
    ...headers,
  });
  res.end(JSON.stringify(body));
}

function corsHeaders(origin) {
  return {
    'Access-Control-Allow-Origin': origin,
    'Access-Control-Allow-Methods': 'POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Access-Control-Max-Age': '86400',
    'Vary': 'Origin',
  };
}

function isAllowedOrigin(origin) {
  return ALLOWED_ORIGINS.has(origin);
}

function requestIp(req) {
  // Tencent's gateway supplies these forwarding headers. They are used only
  // for a lightweight per-instance throttle, never for access control.
  return String(req.headers['x-real-ip'] || req.headers['x-forwarded-for'] || 'unknown')
    .split(',')[0].trim().slice(0, 80);
}

function consumeRateLimit(ip) {
  const now = Date.now();
  const current = buckets.get(ip);
  if (!current || now - current.startedAt >= WINDOW_MS) {
    buckets.set(ip, { startedAt: now, count: 1 });
    return true;
  }
  current.count += 1;
  return current.count <= MAX_REQUESTS_PER_IP;
}

async function readJsonBody(req) {
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > MAX_BODY_BYTES) throw Object.assign(new Error('Request is too large'), { status: 413 });
    chunks.push(chunk);
  }
  try {
    return JSON.parse(Buffer.concat(chunks).toString('utf8'));
  } catch {
    throw Object.assign(new Error('Invalid JSON request'), { status: 400 });
  }
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url || '/', 'http://localhost');
  const origin = String(req.headers.origin || '');

  // Health is intentionally public and contains no secrets, so it can be
  // opened directly on mobile to distinguish network reachability.
  if (url.pathname === '/health' && req.method === 'GET') {
    return sendJson(res, 200, { ok: true, service: 'GlobalNavigator Dify relay' }, {
      'Access-Control-Allow-Origin': '*',
    });
  }

  if (!isAllowedOrigin(origin)) {
    return sendJson(res, 403, { error: 'Origin not allowed' });
  }
  const cors = corsHeaders(origin);

  if (req.method === 'OPTIONS') {
    res.writeHead(204, cors);
    return res.end();
  }
  if (url.pathname !== '/chat' || req.method !== 'POST') {
    return sendJson(res, 404, { error: 'Not found' }, cors);
  }
  if (!process.env.DIFY_API_KEY) {
    return sendJson(res, 503, { error: 'Dify server secret is not configured' }, cors);
  }

  const declaredLength = Number(req.headers['content-length'] || 0);
  if (declaredLength > MAX_BODY_BYTES) {
    return sendJson(res, 413, { error: 'Request is too large' }, cors);
  }
  if (!consumeRateLimit(requestIp(req))) {
    return sendJson(res, 429, { error: 'Too many requests. Please wait a minute and try again.' }, cors);
  }

  let input;
  try {
    input = await readJsonBody(req);
  } catch (error) {
    return sendJson(res, error.status || 400, { error: error.message }, cors);
  }

  const query = typeof input.query === 'string' ? input.query.trim() : '';
  const user = typeof input.user === 'string' ? input.user.trim() : '';
  const conversationId = typeof input.conversation_id === 'string' ? input.conversation_id : '';
  if (!query || query.length > 10_000 || !/^[a-zA-Z0-9_-]{6,128}$/.test(user) || conversationId.length > 128) {
    return sendJson(res, 400, { error: 'Invalid chat input' }, cors);
  }

  let upstream;
  try {
    upstream = await fetch('https://api.dify.ai/v1/chat-messages', {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${process.env.DIFY_API_KEY}`,
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
      },
      body: JSON.stringify({
        inputs: {},
        query,
        response_mode: 'streaming',
        conversation_id: conversationId,
        user,
      }),
      signal: AbortSignal.timeout(120_000),
    });
  } catch (error) {
    const message = error?.name === 'TimeoutError' ? 'Dify API request timed out' : 'Could not reach Dify API';
    return sendJson(res, 502, { error: message }, cors);
  }

  const headers = {
    ...cors,
    'Cache-Control': 'no-cache, no-transform',
    'X-Content-Type-Options': 'nosniff',
  };
  const contentType = upstream.headers.get('content-type');
  if (contentType) headers['Content-Type'] = contentType;
  if (upstream.ok) headers['X-Accel-Buffering'] = 'no';
  res.writeHead(upstream.status, headers);
  if (!upstream.body) return res.end();
  Readable.fromWeb(upstream.body).pipe(res);
});

server.requestTimeout = 130_000;
server.headersTimeout = 135_000;
server.listen(PORT, '0.0.0.0');

