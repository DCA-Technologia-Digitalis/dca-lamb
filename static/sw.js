// DV Saude — Service Worker
// Versao: 1.0.0
// Estrategia: Network-first para paginas, Cache-first para assets estaticos

const CACHE_VERSION = 'dv-saude-v1';
const STATIC_CACHE = 'dv-static-v1';

const STATIC_ASSETS = [
  '/static/logo.png',
  '/static/manifest.json',
  '/static/sw.js',
];

const OFFLINE_FALLBACK_PAGE = '/login';

// ── Instalacao ──────────────────────────────────────────────────────────────
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE)
      .then((cache) => cache.addAll(STATIC_ASSETS).catch(() => {}))
      .then(() => self.skipWaiting())
  );
});

// ── Ativacao ─────────────────────────────────────────────────────────────────
self.addEventListener('activate', (event) => {
  const validCaches = [CACHE_VERSION, STATIC_CACHE];
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => !validCaches.includes(k)).map((k) => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

// ── Interceptacao de requisicoes ─────────────────────────────────────────────
self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Ignora requisicoes nao-GET e de outras origens
  if (request.method !== 'GET' || url.origin !== self.location.origin) return;

  // Assets estaticos: cache-first
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(cacheFirst(request));
    return;
  }

  // Paginas da aplicacao: network-first com fallback
  if (request.headers.get('accept')?.includes('text/html')) {
    event.respondWith(networkFirstWithFallback(request));
    return;
  }
});

async function cacheFirst(request) {
  const cached = await caches.match(request);
  if (cached) return cached;
  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(STATIC_CACHE);
      cache.put(request, response.clone());
    }
    return response;
  } catch {
    return new Response('Asset indisponivel offline.', { status: 503 });
  }
}

async function networkFirstWithFallback(request) {
  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(CACHE_VERSION);
      cache.put(request, response.clone());
    }
    return response;
  } catch {
    const cached = await caches.match(request);
    if (cached) return cached;
    const fallback = await caches.match(OFFLINE_FALLBACK_PAGE);
    if (fallback) return fallback;
    return new Response(
      '<html><body style="font-family:sans-serif;text-align:center;padding:40px">' +
      '<h2>DV Saude</h2><p>Sem conexao com a internet.</p>' +
      '<p>Verifique sua conexao e tente novamente.</p></body></html>',
      { headers: { 'Content-Type': 'text/html; charset=utf-8' }, status: 503 }
    );
  }
}

// ── Mensagens do cliente ─────────────────────────────────────────────────────
self.addEventListener('message', (event) => {
  if (event.data?.type === 'SKIP_WAITING') self.skipWaiting();
  if (event.data?.type === 'GET_VERSION') {
    event.ports[0]?.postMessage({ version: CACHE_VERSION });
  }
});
