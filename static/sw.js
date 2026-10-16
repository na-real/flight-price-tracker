/**
 * SKYTRACKER — SERVICE WORKER
 * Network-resilient static caching with strict privacy safeguards.
 * 
 * Rules:
 * - Pre-cache core luxury UI assets (CSS, JS, Fonts, Icons).
 * - Network-First for HTML navigation so real-time flight data is always fresh.
 * - Stale-While-Revalidate for static assets.
 * - NEVER cache /api/* requests, authentication routes, or private session telemetry.
 */

const CACHE_NAME = 'skytracker-shell-v1';

const STATIC_ASSETS = [
    '/',
    '/static/style.css',
    '/static/app.js',
    '/static/appearance.js',
    '/static/backdrops.js',
    '/static/atmosphere.js',
    '/static/scene3d.js',
    '/static/favicon.svg',
    '/static/manifest.json'
];

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => {
            return cache.addAll(STATIC_ASSETS);
        }).then(() => self.skipWaiting())
    );
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((keys) => {
            return Promise.all(
                keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
            );
        }).then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', (event) => {
    const url = new URL(event.request.url);

    // 1. Strictly bypass service worker for API endpoints, POST/PUT methods, and auth routes
    if (
        event.request.method !== 'GET' ||
        url.pathname.startsWith('/api/') ||
        url.pathname === '/login' ||
        url.pathname === '/signup' ||
        url.pathname === '/logout' ||
        url.pathname === '/dashboard'
    ) {
        return;
    }

    // 2. Navigation requests: Network-First with Cache Fallback
    if (event.request.mode === 'navigate') {
        event.respondWith(
            fetch(event.request)
                .then((networkResponse) => {
                    if (networkResponse.status === 200) {
                        const copy = networkResponse.clone();
                        caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
                    }
                    return networkResponse;
                })
                .catch(() => {
                    return caches.match(event.request).then((cached) => {
                        return cached || caches.match('/');
                    });
                })
        );
        return;
    }

    // 3. Static Assets: Stale-While-Revalidate
    if (url.pathname.startsWith('/static/')) {
        event.respondWith(
            caches.match(event.request).then((cached) => {
                const fetchPromise = fetch(event.request).then((networkResponse) => {
                    if (networkResponse.status === 200) {
                        const copy = networkResponse.clone();
                        caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
                    }
                    return networkResponse;
                }).catch(() => {});
                return cached || fetchPromise;
            })
        );
    }
});
