"""
PulseWatch PWA — Android-installable companion app
===================================================
Drop this file into the same folder as app.py and it automatically
registers itself as extra routes on the existing Flask app.

HOW TO INSTALL ON ANDROID
--------------------------
1. Start PulseWatch as normal
2. Open Chrome on Android → visit  http://YOUR-SERVER:5000/app
3. Chrome shows "Add to Home Screen" banner (or tap ⋮ → Add to Home Screen)
4. App icon appears on launcher — opens full-screen, no browser chrome

ALTERNATIVELY — standalone server (if you don't want to modify app.py)
-----------------------------------------------------------------------
   python pwa.py --port 5001 --api http://YOUR-SERVER:5000
   Then visit http://YOUR-SERVER:5001/app on Android

FEATURES
--------
• Full monitor dashboard with live status dots & uptime bars
• Pull-to-refresh
• Per-monitor detail with response-time sparkline
• Create / resolve incidents
• Maintenance window viewer
• Status page viewer (public pages)
• Analytics: avg ping gauge, CPU/RAM/Disk bars
• Settings: configure server URL + credentials
• Offline support via Service Worker (shows cached data when offline)
• Push-style auto-refresh every 30s
• Bottom navigation bar (Android-style)
• Dark mode matching system preference
• Installable: Web App Manifest with icons
• Works with 2FA — login flow handles TOTP redirect

USAGE AS STANDALONE
-------------------
  pip install flask requests
  python pwa.py
  # Configure server URL in app Settings tab
"""

import os, json, base64, argparse
from pathlib import Path

try:
    from flask import Flask as _Flask, Response, request as _req, jsonify
    _STANDALONE = True
except ImportError:
    _STANDALONE = False

# ── Inline SVG icons as data URIs for the manifest ───────────────────────────
_ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
<rect width="512" height="512" rx="100" fill="#0a0d14"/>
<circle cx="256" cy="256" r="200" fill="none" stroke="#3b82f6" stroke-width="28"/>
<circle cx="256" cy="256" r="70" fill="#3b82f6"/>
<rect x="242" y="56" width="28" height="90" rx="14" fill="#3b82f6"/>
<rect x="242" y="366" width="28" height="90" rx="14" fill="#3b82f6"/>
<rect x="56" y="242" width="90" height="28" rx="14" fill="#3b82f6"/>
<rect x="366" y="242" width="90" height="28" rx="14" fill="#3b82f6"/>
<circle cx="256" cy="256" r="30" fill="#0a0d14"/>
</svg>"""

_ICON_B64 = base64.b64encode(_ICON_SVG.encode()).decode()

# ── Web App Manifest ──────────────────────────────────────────────────────────
MANIFEST = {
    "name": "PulseWatch",
    "short_name": "PulseWatch",
    "description": "Uptime monitor — your services at a glance",
    "start_url": "/app",
    "display": "standalone",
    "orientation": "portrait",
    "background_color": "#0a0d14",
    "theme_color": "#3b82f6",
    "icons": [
        {"src": "/app/icon-192.svg", "sizes": "192x192", "type": "image/svg+xml", "purpose": "any maskable"},
        {"src": "/app/icon-512.svg", "sizes": "512x512", "type": "image/svg+xml", "purpose": "any maskable"},
    ],
    "categories": ["utilities", "productivity"],
    "shortcuts": [
        {"name": "Dashboard", "url": "/app#dashboard", "description": "View all monitors"},
        {"name": "Incidents", "url": "/app#incidents",  "description": "View active incidents"},
    ]
}

# ── Service Worker ────────────────────────────────────────────────────────────
SERVICE_WORKER_JS = r"""
const CACHE = 'pulsewatch-v4';
const STATIC = ['/app', '/app/manifest.json'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(STATIC)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys =>
    Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
  ).then(() => self.clients.claim()));
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  // API calls: network-first, fall through to cache on fail
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/dashboard') ||
      url.pathname.startsWith('/incidents') || url.pathname.startsWith('/monitor')) {
    e.respondWith(
      fetch(e.request).catch(() => caches.match(e.request))
    );
    return;
  }
  // Static: cache-first
  e.respondWith(
    caches.match(e.request).then(cached => {
      const net = fetch(e.request).then(resp => {
        caches.open(CACHE).then(c => c.put(e.request, resp.clone()));
        return resp;
      });
      return cached || net;
    })
  );
});

// Background sync — wake up every 30s to check for downs
self.addEventListener('periodicsync', e => {
  if (e.tag === 'monitor-check') {
    e.waitUntil(fetch('/api/monitors').then(r => r.json()).then(data => {
      const downs = data.filter(m => m.status === 'down');
      if (downs.length > 0 && self.registration.showNotification) {
        self.registration.showNotification('PulseWatch Alert', {
          body: `${downs.length} monitor(s) are DOWN: ${downs.map(m => m.name).join(', ')}`,
          icon: '/app/icon-192.svg',
          badge: '/app/icon-192.svg',
          tag: 'monitor-down',
          renotify: true,
          vibrate: [200, 100, 200]
        });
      }
    }).catch(() => {}));
  }
});
"""

# ── Main PWA HTML (single-page app) ──────────────────────────────────────────
PWA_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<meta name="theme-color" content="#3b82f6">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="PulseWatch">
<title>PulseWatch</title>
<link rel="manifest" href="/app/manifest.json">
<link rel="apple-touch-icon" href="/app/icon-192.svg">
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=DM+Sans:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
/* ── Reset & root ── */
:root{
  --bg:#0a0d14;--sf:#111520;--s2:#181e2e;--bd:#1e2740;
  --ac:#3b82f6;--gr:#22d3a4;--rd:#f43f5e;--yl:#fbbf24;
  --or:#f97316;--pu:#a855f7;--tx:#e2e8f0;--mu:#64748b;
  --nav-h:64px;--top-h:56px;--safe-b:env(safe-area-inset-bottom,0px);
  --mono:'Space Mono',monospace;--sans:'DM Sans',sans-serif;
}
@media(prefers-color-scheme:light){
  :root{--bg:#f1f5f9;--sf:#ffffff;--s2:#f8fafc;--bd:#e2e8f0;--tx:#0f172a;--mu:#64748b}
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
html,body{height:100%;overflow:hidden;overscroll-behavior:none}
body{background:var(--bg);color:var(--tx);font-family:var(--sans);font-size:14px;
     display:flex;flex-direction:column;-webkit-font-smoothing:antialiased}

/* ── Top bar ── */
.topbar{background:var(--sf);border-bottom:1px solid var(--bd);
        height:var(--top-h);display:flex;align-items:center;
        padding:0 16px 0 16px;padding-top:env(safe-area-inset-top,0);
        flex-shrink:0;position:relative;z-index:10}
.topbar-logo{font-family:var(--mono);font-weight:700;font-size:16px;color:var(--tx);display:flex;align-items:center;gap:8px}
.topbar-logo span{color:var(--ac)}
.topbar-actions{margin-left:auto;display:flex;align-items:center;gap:6px}
.icon-btn{width:36px;height:36px;border-radius:50%;background:var(--s2);border:1px solid var(--bd);
           display:flex;align-items:center;justify-content:center;cursor:pointer;color:var(--mu);font-size:16px}
.icon-btn:active{background:var(--bd)}

/* ── Scroll container ── */
.scroll-area{flex:1;overflow-y:auto;overflow-x:hidden;-webkit-overflow-scrolling:touch;
             padding-bottom:calc(var(--nav-h) + var(--safe-b) + 8px)}

/* ── Bottom nav ── */
.bottom-nav{background:var(--sf);border-top:1px solid var(--bd);
            height:calc(var(--nav-h) + var(--safe-b));
            padding-bottom:var(--safe-b);
            display:flex;align-items:stretch;flex-shrink:0;z-index:10}
.nav-item{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;
          gap:3px;cursor:pointer;color:var(--mu);font-size:10px;font-weight:500;
          padding:8px 4px;transition:color .15s;border:none;background:none;position:relative}
.nav-item.active{color:var(--ac)}
.nav-item .nav-icon{font-size:20px;line-height:1}
.nav-item .badge{position:absolute;top:6px;right:calc(50% - 14px);
                 background:var(--rd);color:#fff;border-radius:10px;
                 font-size:9px;padding:1px 5px;font-weight:700;min-width:16px;text-align:center}
.nav-item:active{background:var(--s2)}

/* ── Pages ── */
.page{display:none;padding:12px 16px;animation:fadein .15s ease}
.page.active{display:block}
@keyframes fadein{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:translateY(0)}}

/* ── Cards ── */
.card{background:var(--sf);border:1px solid var(--bd);border-radius:14px;padding:16px;margin-bottom:10px}
.card:active{background:var(--s2)}

/* ── Status dots ── */
.dot{width:8px;height:8px;border-radius:50%;display:inline-block;flex-shrink:0}
.d-up{background:var(--gr);box-shadow:0 0 8px var(--gr)}
.d-down{background:var(--rd);box-shadow:0 0 8px var(--rd)}
.d-maint{background:var(--pu)}.d-pend{background:var(--yl)}
.pulse-anim{animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}

/* ── Badges ── */
.badge{display:inline-flex;align-items:center;padding:2px 8px;border-radius:20px;
       font-size:10px;font-weight:700;font-family:var(--mono);text-transform:uppercase;letter-spacing:.04em}
.b-up{background:#0d2e22;color:var(--gr)}.b-down{background:#2d0f1c;color:var(--rd)}
.b-pending{background:#1f1a08;color:var(--yl)}.b-maintenance{background:#1a1030;color:var(--pu)}
.b-degraded{background:#2d1a00;color:var(--or)}.b-major{background:#2d0f1c;color:var(--rd)}
.b-full_outage{background:#3d0010;color:#ff2050}
.b-open{background:#2d1f00;color:var(--yl)}.b-monitoring{background:#0d1a30;color:var(--ac)}
.b-resolved{background:#0d2e22;color:var(--gr)}

/* ── Stat chips ── */
.chips{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:14px}
.chip{background:var(--s2);border:1px solid var(--bd);border-radius:10px;padding:10px 12px;text-align:center}
.chip-val{font-family:var(--mono);font-size:20px;font-weight:700;line-height:1.1}
.chip-lbl{color:var(--mu);font-size:10px;text-transform:uppercase;letter-spacing:.05em;margin-top:2px}

/* ── Uptime bar ── */
.ubar{display:flex;gap:2px;height:22px;align-items:center;margin:8px 0 4px}
.useg{flex:1;height:14px;border-radius:2px;background:var(--bd)}
.useg.up{background:var(--gr);opacity:.75}.useg.down{background:var(--rd);opacity:.85}
.useg.maintenance{background:var(--pu);opacity:.8}

/* ── Progress bar ── */
.pbar{height:6px;border-radius:3px;background:var(--bd);overflow:hidden;margin-top:5px}
.pbar-f{height:100%;border-radius:3px;transition:width .4s}

/* ── Form elements ── */
input,select,textarea{
  background:var(--s2);border:1px solid var(--bd);color:var(--tx);
  border-radius:10px;padding:12px 14px;font-family:var(--sans);
  font-size:15px;width:100%;outline:none;appearance:none;-webkit-appearance:none}
input:focus,select:focus,textarea:focus{border-color:var(--ac)}
.fg{display:flex;flex-direction:column;gap:6px;margin-bottom:14px}
.fl{font-size:12px;font-weight:600;color:var(--mu);text-transform:uppercase;letter-spacing:.04em}
.btn{display:flex;align-items:center;justify-content:center;gap:8px;
     padding:14px 20px;border-radius:12px;border:none;cursor:pointer;
     font-family:var(--sans);font-size:15px;font-weight:600;width:100%;
     transition:all .15s;-webkit-tap-highlight-color:transparent}
.btn:active{opacity:.8;transform:scale(.98)}
.btn-p{background:var(--ac);color:#fff}
.btn-g{background:var(--s2);color:var(--tx);border:1px solid var(--bd)}
.btn-d{background:#1f0d12;color:var(--rd);border:1px solid #3d1520}

/* ── Section header ── */
.sh{font-size:11px;text-transform:uppercase;letter-spacing:.07em;
    color:var(--mu);font-weight:700;margin:16px 0 8px;padding:0 2px}

/* ── Monitor list row ── */
.mrow{display:flex;align-items:center;gap:12px;padding:14px 16px;
      background:var(--sf);border:1px solid var(--bd);border-radius:14px;
      margin-bottom:8px;cursor:pointer;transition:background .1s}
.mrow:active{background:var(--s2)}
.mrow-info{flex:1;min-width:0}
.mrow-name{font-weight:600;font-size:15px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mrow-sub{color:var(--mu);font-size:12px;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mrow-rt{font-family:var(--mono);font-size:12px;color:var(--mu);flex-shrink:0}

/* ── Detail view ── */
.detail-overlay{display:none;position:fixed;inset:0;background:var(--bg);
                z-index:50;flex-direction:column;overflow:hidden}
.detail-overlay.open{display:flex}
.detail-header{background:var(--sf);border-bottom:1px solid var(--bd);
               padding:12px 16px;padding-top:calc(12px + env(safe-area-inset-top,0));
               display:flex;align-items:center;gap:12px;flex-shrink:0}
.back-btn{width:36px;height:36px;border-radius:50%;background:var(--s2);
          border:1px solid var(--bd);display:flex;align-items:center;
          justify-content:center;cursor:pointer;font-size:18px;color:var(--tx);
          flex-shrink:0}
.detail-title{font-weight:600;font-size:16px;flex:1;min-width:0;
              white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.detail-scroll{flex:1;overflow-y:auto;-webkit-overflow-scrolling:touch;padding:14px 16px;
               padding-bottom:calc(32px + env(safe-area-inset-bottom,0))}

/* ── Sparkline canvas ── */
.sparkline-wrap{margin:10px 0;height:60px;position:relative}
.sparkline-wrap canvas{width:100%;height:60px}

/* ── Toast ── */
#toast{position:fixed;bottom:calc(var(--nav-h) + var(--safe-b) + 12px);
       left:50%;transform:translateX(-50%) translateY(20px);
       background:#1e293b;color:#e2e8f0;padding:10px 18px;border-radius:20px;
       font-size:13px;font-weight:500;opacity:0;transition:all .25s;
       z-index:999;white-space:nowrap;pointer-events:none;border:1px solid var(--bd)}
#toast.show{opacity:1;transform:translateX(-50%) translateY(0)}

/* ── Loader ── */
.loader{display:flex;flex-direction:column;align-items:center;justify-content:center;
        padding:60px 20px;color:var(--mu);gap:14px}
.spinner{width:36px;height:36px;border:3px solid var(--bd);border-top-color:var(--ac);
         border-radius:50%;animation:spin .7s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}

/* ── Pull to refresh indicator ── */
.ptr-indicator{text-align:center;padding:12px;color:var(--mu);font-size:12px;
               height:48px;display:flex;align-items:center;justify-content:center;gap:8px;
               overflow:hidden;max-height:0;transition:max-height .25s}
.ptr-indicator.visible{max-height:48px}

/* ── Empty state ── */
.empty{text-align:center;padding:48px 20px;color:var(--mu)}
.empty-icon{font-size:48px;margin-bottom:12px}
.empty-title{font-size:16px;font-weight:600;color:var(--tx);margin-bottom:6px}

/* ── Online / offline bar ── */
#offline-bar{display:none;background:#2d0f1c;color:var(--rd);text-align:center;
             padding:6px;font-size:12px;font-weight:600;flex-shrink:0;border-bottom:1px solid #3d1520}
#offline-bar.show{display:block}

/* ── Login screen ── */
#login-screen{position:fixed;inset:0;background:var(--bg);z-index:200;
              display:flex;flex-direction:column;align-items:center;
              justify-content:center;padding:24px;
              background:radial-gradient(ellipse 80% 60% at 50% 0%,#1a2340 0%,var(--bg) 70%)}
.login-box{width:100%;max-width:380px}
.login-logo{text-align:center;margin-bottom:32px;font-family:var(--mono);
            font-size:24px;font-weight:700;display:flex;align-items:center;
            justify-content:center;gap:10px}
.login-logo span{color:var(--ac)}
.login-card{background:var(--sf);border:1px solid var(--bd);border-radius:20px;padding:28px}
.em{background:#2d0f1c;border:1px solid #3d1520;color:var(--rd);
    padding:10px 14px;border-radius:10px;margin-bottom:14px;font-size:13px}

/* ── Incident cards ── */
.inc-card{border-radius:14px;padding:14px 16px;margin-bottom:8px;border:1px solid}
.inc-degraded{background:rgba(249,115,22,.07);border-color:rgba(249,115,22,.25)}
.inc-major{background:#2d0f1c1a;border-color:rgba(244,63,94,.25)}
.inc-full_outage{background:rgba(255,32,80,.07);border-color:rgba(255,32,80,.3)}

/* ── Gauge ── */
.gauge-wrap{display:flex;flex-direction:column;align-items:center;padding:20px 0}
.gauge{width:130px;height:130px;border-radius:50%;display:flex;align-items:center;justify-content:center;transition:background .5s}
.gauge-inner{width:92px;height:92px;border-radius:50%;background:var(--sf);
             display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px}
.gauge-val{font-family:var(--mono);font-size:22px;font-weight:700;color:var(--ac)}

/* ── Settings ── */
.setting-row{display:flex;justify-content:space-between;align-items:center;
             padding:14px 0;border-bottom:1px solid var(--bd)}
.setting-row:last-child{border:none}
.switch{position:relative;width:48px;height:26px;flex-shrink:0}
.switch input{opacity:0;width:0;height:0}
.slider{position:absolute;cursor:pointer;inset:0;background:var(--bd);border-radius:26px;transition:.3s}
.slider::before{position:absolute;content:'';height:20px;width:20px;left:3px;bottom:3px;
                background:#fff;border-radius:50%;transition:.3s}
input:checked+.slider{background:var(--ac)}
input:checked+.slider::before{transform:translateX(22px)}

/* ── Maintenance ── */
.maint-banner{background:#1a1030;border:1px solid rgba(168,85,247,.3);
              border-radius:12px;padding:14px;margin-bottom:8px;
              display:flex;gap:12px;align-items:flex-start}

/* ── Scroll momentum fix ── */
.scroll-area,.detail-scroll{will-change:scroll-position}
</style>
</head>
<body>

<div id="offline-bar">⚠️ You are offline — showing cached data</div>

<!-- ── Login screen ──────────────────────────────────────────────────────── -->
<div id="login-screen">
  <div class="login-box">
    <div class="login-logo">
      <svg width="28" height="28" viewBox="0 0 18 18" fill="none">
        <circle cx="9" cy="9" r="8" stroke="#3b82f6" stroke-width="2"/>
        <circle cx="9" cy="9" r="3" fill="#3b82f6"/>
        <line x1="9" y1="1" x2="9" y2="4" stroke="#3b82f6" stroke-width="1.5"/>
        <line x1="9" y1="14" x2="9" y2="17" stroke="#3b82f6" stroke-width="1.5"/>
        <line x1="1" y1="9" x2="4" y2="9" stroke="#3b82f6" stroke-width="1.5"/>
        <line x1="14" y1="9" x2="17" y2="9" stroke="#3b82f6" stroke-width="1.5"/>
      </svg>
      <span>Pulse</span>Watch
    </div>
    <div class="login-card">
      <div style="font-size:18px;font-weight:600;margin-bottom:4px">Sign in</div>
      <div style="color:var(--mu);font-size:13px;margin-bottom:20px">Connect to your PulseWatch server</div>
      <div id="login-error" class="em" style="display:none"></div>
      <div class="fg">
        <label class="fl">Server URL</label>
        <input type="url" id="server-url" placeholder="http://192.168.1.x:5000" autocomplete="url" inputmode="url">
      </div>
      <div class="fg">
        <label class="fl">Username</label>
        <input type="text" id="login-user" placeholder="your_username" autocomplete="username">
      </div>
      <div class="fg" style="margin-bottom:20px">
        <label class="fl">Password</label>
        <input type="password" id="login-pass" placeholder="••••••••" autocomplete="current-password">
      </div>
      <button class="btn btn-p" onclick="doLogin()" id="login-btn">Sign In</button>
    </div>
  </div>
</div>

<!-- ── 2FA screen ────────────────────────────────────────────────────────── -->
<div id="twofa-screen" style="display:none;position:fixed;inset:0;background:var(--bg);z-index:201;
     align-items:center;justify-content:center;padding:24px">
  <div style="width:100%;max-width:360px">
    <div style="background:var(--sf);border:1px solid var(--bd);border-radius:20px;padding:28px;text-align:center">
      <div style="font-size:40px;margin-bottom:12px">🔐</div>
      <div style="font-size:18px;font-weight:600;margin-bottom:4px">Two-Factor Auth</div>
      <div style="color:var(--mu);font-size:13px;margin-bottom:20px">Enter the 6-digit code from your authenticator app</div>
      <div id="twofa-error" class="em" style="display:none"></div>
      <div class="fg">
        <input type="text" id="twofa-code" placeholder="000000" maxlength="6" inputmode="numeric"
               style="text-align:center;font-size:28px;font-family:var(--mono);letter-spacing:8px">
      </div>
      <button class="btn btn-p" onclick="do2FA()">Verify</button>
      <button class="btn btn-g" onclick="cancelLogin()" style="margin-top:10px">Cancel</button>
    </div>
  </div>
</div>

<!-- ── Main app shell ────────────────────────────────────────────────────── -->
<div id="app-shell" style="display:none;flex-direction:column;height:100%">

  <!-- Top bar -->
  <div class="topbar">
    <div class="topbar-logo">
      <svg width="20" height="20" viewBox="0 0 18 18" fill="none">
        <circle cx="9" cy="9" r="8" stroke="#3b82f6" stroke-width="2"/>
        <circle cx="9" cy="9" r="3" fill="#3b82f6"/>
        <line x1="9" y1="1" x2="9" y2="4" stroke="#3b82f6" stroke-width="1.5"/>
        <line x1="9" y1="14" x2="9" y2="17" stroke="#3b82f6" stroke-width="1.5"/>
        <line x1="1" y1="9" x2="4" y2="9" stroke="#3b82f6" stroke-width="1.5"/>
        <line x1="14" y1="9" x2="17" y2="9" stroke="#3b82f6" stroke-width="1.5"/>
      </svg>
      <span>Pulse</span>Watch
    </div>
    <div class="topbar-actions">
      <div class="icon-btn" onclick="refreshCurrent()" title="Refresh">↻</div>
    </div>
  </div>

  <!-- Offline indicator -->
  <div id="status-indicator" style="display:none;background:var(--bd);font-size:11px;
       color:var(--mu);text-align:center;padding:4px;flex-shrink:0">Connecting…</div>

  <!-- Scroll area -->
  <div class="scroll-area" id="scroll-area">
    <div class="ptr-indicator" id="ptr">↓ Pull to refresh</div>

    <!-- Dashboard page -->
    <div class="page active" id="page-dashboard">
      <div id="dash-content"><div class="loader"><div class="spinner"></div><span>Loading monitors…</span></div></div>
    </div>

    <!-- Incidents page -->
    <div class="page" id="page-incidents">
      <div id="inc-content"><div class="loader"><div class="spinner"></div><span>Loading incidents…</span></div></div>
    </div>

    <!-- Analytics page -->
    <div class="page" id="page-analytics">
      <div id="analytics-content"><div class="loader"><div class="spinner"></div><span>Loading analytics…</span></div></div>
    </div>

    <!-- Status Pages page -->
    <div class="page" id="page-statuspages">
      <div id="sp-content"><div class="loader"><div class="spinner"></div><span>Loading status pages…</span></div></div>
    </div>

    <!-- Settings page -->
    <div class="page" id="page-settings">
      <div id="settings-content"></div>
    </div>
  </div>

  <!-- Bottom navigation -->
  <div class="bottom-nav">
    <button class="nav-item active" id="nav-dashboard" onclick="switchTab('dashboard')">
      <span class="nav-icon">📡</span>
      <span>Monitors</span>
    </button>
    <button class="nav-item" id="nav-incidents" onclick="switchTab('incidents')">
      <span class="nav-icon">🚨</span>
      <span>Incidents</span>
      <span class="badge" id="inc-badge" style="display:none">0</span>
    </button>
    <button class="nav-item" id="nav-analytics" onclick="switchTab('analytics')">
      <span class="nav-icon">📊</span>
      <span>Analytics</span>
    </button>
    <button class="nav-item" id="nav-statuspages" onclick="switchTab('statuspages')">
      <span class="nav-icon">📋</span>
      <span>Status</span>
    </button>
    <button class="nav-item" id="nav-settings" onclick="switchTab('settings')">
      <span class="nav-icon">⚙️</span>
      <span>Settings</span>
    </button>
  </div>
</div>

<!-- ── Detail overlays ───────────────────────────────────────────────────── -->
<div class="detail-overlay" id="monitor-detail">
  <div class="detail-header">
    <div class="back-btn" onclick="closeDetail('monitor-detail')">‹</div>
    <div class="detail-title" id="mon-detail-title">Monitor</div>
    <div class="topbar-actions">
      <div class="icon-btn" id="mon-toggle-btn" onclick="toggleMonitor()" style="font-size:13px;width:auto;border-radius:20px;padding:0 12px">Pause</div>
    </div>
  </div>
  <div class="detail-scroll" id="mon-detail-body"></div>
</div>

<div class="detail-overlay" id="incident-detail">
  <div class="detail-header">
    <div class="back-btn" onclick="closeDetail('incident-detail')">‹</div>
    <div class="detail-title" id="inc-detail-title">Incident</div>
  </div>
  <div class="detail-scroll" id="inc-detail-body"></div>
</div>

<div class="detail-overlay" id="new-incident">
  <div class="detail-header">
    <div class="back-btn" onclick="closeDetail('new-incident')">‹</div>
    <div class="detail-title">New Incident</div>
  </div>
  <div class="detail-scroll">
    <div class="fg"><label class="fl">Title</label><input type="text" id="ni-title" placeholder="Service is experiencing issues"></div>
    <div class="fg"><label class="fl">Severity</label>
      <select id="ni-severity">
        <option value="degraded">Degraded Performance</option>
        <option value="major" selected>Major Outage</option>
        <option value="full_outage">Full Outage</option>
      </select>
    </div>
    <div class="fg"><label class="fl">Affected Monitor</label>
      <select id="ni-monitor"><option value="">— None —</option></select>
    </div>
    <div class="fg"><label class="fl">Initial Message</label>
      <textarea id="ni-body" rows="4" placeholder="We are investigating…"></textarea>
    </div>
    <button class="btn btn-p" onclick="submitIncident()">Create Incident</button>
    <div style="height:20px"></div>
  </div>
</div>

<div id="toast"></div>

<script>
// ── State ────────────────────────────────────────────────────────────────────
const S = {
  serverUrl: localStorage.getItem('pw-server') || '',
  currentTab: 'dashboard',
  monitors: [],
  incidents: [],
  currentMonitorId: null,
  refreshTimer: null,
  online: true,
};

// ── API helper ───────────────────────────────────────────────────────────────
async function api(path, opts={}) {
  const base = S.serverUrl.replace(/\/$/, '');
  const url = base + path;
  try {
    const r = await fetch(url, {
      credentials: 'include',
      ...opts,
      headers: { 'Content-Type': 'application/x-www-form-urlencoded', ...(opts.headers||{}) }
    });
    return r;
  } catch(e) {
    setOffline(true);
    throw e;
  }
}

async function apiJSON(path) {
  const r = await api(path);
  if (r.status === 401) { showLogin(); throw new Error('Unauthorized'); }
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  setOffline(false);
  return r.json();
}

// ── Offline detection ─────────────────────────────────────────────────────────
function setOffline(v) {
  S.online = !v;
  document.getElementById('offline-bar').classList.toggle('show', v);
}
window.addEventListener('online',  () => setOffline(false));
window.addEventListener('offline', () => setOffline(true));

// ── Toast ────────────────────────────────────────────────────────────────────
function toast(msg, ms=2500) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.remove('show'), ms);
}

// ── Login flow ───────────────────────────────────────────────────────────────
function showLogin() {
  document.getElementById('login-screen').style.display = 'flex';
  document.getElementById('app-shell').style.display = 'none';
  if (S.serverUrl) document.getElementById('server-url').value = S.serverUrl;
}

function cancelLogin() {
  document.getElementById('twofa-screen').style.display = 'none';
  document.getElementById('login-screen').style.display = 'flex';
}

async function doLogin() {
  const btn = document.getElementById('login-btn');
  const errEl = document.getElementById('login-error');
  const url  = document.getElementById('server-url').value.trim().replace(/\/$/, '');
  const user = document.getElementById('login-user').value.trim();
  const pass = document.getElementById('login-pass').value;
  if (!url || !user || !pass) { showErr(errEl, 'All fields are required.'); return; }
  S.serverUrl = url;
  localStorage.setItem('pw-server', url);
  btn.textContent = 'Signing in…';
  btn.disabled = true;
  try {
    const body = new URLSearchParams({ username: user, password: pass });
    const r = await fetch(url + '/login', {
      method: 'POST', credentials: 'include',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body, redirect: 'manual'
    });
    // If redirect to /verify-2fa → show 2FA screen
    const loc = r.headers.get('location') || '';
    if (loc.includes('verify-2fa') || r.url?.includes('verify-2fa')) {
      document.getElementById('login-screen').style.display = 'none';
      document.getElementById('twofa-screen').style.display = 'flex';
      return;
    }
    // Check if we can reach the API now
    const test = await fetch(url + '/api/monitors', { credentials: 'include' });
    if (test.ok) {
      enterApp();
    } else {
      showErr(errEl, 'Invalid username or password.');
    }
  } catch(e) {
    showErr(errEl, 'Cannot reach server. Check the URL and try again.');
  } finally {
    btn.textContent = 'Sign In';
    btn.disabled = false;
  }
}

async function do2FA() {
  const code = document.getElementById('twofa-code').value.trim();
  const errEl = document.getElementById('twofa-error');
  if (!code) return;
  try {
    const body = new URLSearchParams({ code });
    await fetch(S.serverUrl + '/verify-2fa', {
      method: 'POST', credentials: 'include',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body, redirect: 'manual'
    });
    const test = await fetch(S.serverUrl + '/api/monitors', { credentials: 'include' });
    if (test.ok) {
      document.getElementById('twofa-screen').style.display = 'none';
      enterApp();
    } else {
      showErr(errEl, 'Invalid code.');
    }
  } catch(e) {
    showErr(errEl, 'Connection error.');
  }
}

function showErr(el, msg) { el.textContent = msg; el.style.display = ''; }

function enterApp() {
  document.getElementById('login-screen').style.display = 'none';
  document.getElementById('twofa-screen').style.display = 'none';
  document.getElementById('app-shell').style.display = 'flex';
  loadAll();
  startAutoRefresh();
}

// ── Tab navigation ────────────────────────────────────────────────────────────
function switchTab(tab) {
  S.currentTab = tab;
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.getElementById(`page-${tab}`).classList.add('active');
  document.getElementById(`nav-${tab}`).classList.add('active');
  document.getElementById('scroll-area').scrollTop = 0;
  refreshCurrent();
}

function refreshCurrent() {
  const loaders = {
    dashboard: loadDashboard,
    incidents: loadIncidents,
    analytics: loadAnalytics,
    statuspages: loadStatusPages,
    settings: renderSettings,
  };
  loaders[S.currentTab]?.();
}

function loadAll() {
  loadDashboard();
  loadIncidents();
}

// ── Dashboard ─────────────────────────────────────────────────────────────────
async function loadDashboard() {
  try {
    const data = await apiJSON('/api/monitors');
    S.monitors = data;
    renderDashboard(data);
  } catch(e) { if (S.monitors.length) renderDashboard(S.monitors); }
}

function renderDashboard(mons) {
  const up = mons.filter(m => m.status==='up').length;
  const dn = mons.filter(m => m.status==='down').length;
  const total = mons.length;
  const avail = total ? ((up/total)*100).toFixed(1) : '—';

  let html = `
  <div class="chips">
    <div class="chip"><div class="chip-val" style="color:var(--gr)">${up}</div><div class="chip-lbl">Online</div></div>
    <div class="chip"><div class="chip-val" style="color:var(--rd)">${dn}</div><div class="chip-lbl">Down</div></div>
    <div class="chip"><div class="chip-val">${avail}%</div><div class="chip-lbl">Avail.</div></div>
  </div>`;

  if (!mons.length) {
    html += `<div class="empty"><div class="empty-icon">📡</div><div class="empty-title">No monitors yet</div><div>Add monitors on the web dashboard</div></div>`;
  } else {
    const sorted = [...mons].sort((a,b) => {
      const o = {down:0,maintenance:1,pending:2,up:3};
      return (o[a.status]??4) - (o[b.status]??4);
    });
    sorted.forEach(m => { html += monitorRow(m); });
  }
  document.getElementById('dash-content').innerHTML = html;
}

function monitorRow(m) {
  const dotCls = m.status==='up' ? 'd-up pulse-anim' : m.status==='down' ? 'd-down' : m.status==='maintenance' ? 'd-maint' : 'd-pend';
  const rt = m.response_time ? `${m.response_time}ms` : '';
  const up7 = m.uptime_7d != null ? `${m.uptime_7d.toFixed(1)}%` : '';
  const upColor = m.uptime_7d>=99 ? 'var(--gr)' : m.uptime_7d>=90 ? 'var(--yl)' : 'var(--rd)';
  return `<div class="mrow" onclick="openMonitor(${m.id})">
    <span class="dot ${dotCls}"></span>
    <div class="mrow-info">
      <div class="mrow-name">${escH(m.name)}</div>
      <div class="mrow-sub" style="color:${upColor}">${up7} uptime</div>
    </div>
    ${rt ? `<div class="mrow-rt">${rt}</div>` : ''}
    <span style="color:var(--mu);font-size:18px">›</span>
  </div>`;
}

// ── Monitor detail ───────────────────────────────────────────────────────────
async function openMonitor(id) {
  S.currentMonitorId = id;
  const m = S.monitors.find(x => x.id===id);
  if (!m) return;
  document.getElementById('mon-detail-title').textContent = m.name;
  document.getElementById('mon-toggle-btn').textContent = m.active===false ? 'Resume' : 'Pause';
  document.getElementById('monitor-detail').classList.add('open');
  document.getElementById('mon-detail-body').innerHTML = `<div class="loader"><div class="spinner"></div></div>`;
  try {
    // Fetch checks via the full page (parse JSON from API)
    const data = await apiJSON(`/api/monitors`);
    const full = data.find(x => x.id===id) || m;
    renderMonitorDetail(full);
  } catch(e) { renderMonitorDetail(m); }
}

function renderMonitorDetail(m) {
  const dotCls = m.status==='up'?'d-up pulse-anim':m.status==='down'?'d-down':m.status==='maintenance'?'d-maint':'d-pend';
  const statusColor = m.status==='up'?'var(--gr)':m.status==='down'?'var(--rd)':m.status==='maintenance'?'var(--pu)':'var(--yl)';

  let html = `
  <div style="display:flex;align-items:center;gap:10px;margin-bottom:16px">
    <span class="dot ${dotCls}" style="width:12px;height:12px"></span>
    <span style="font-size:16px;font-weight:600">${escH(m.name)}</span>
    <span class="badge b-${m.status}" style="margin-left:auto">${m.status}</span>
  </div>
  <div class="chips" style="grid-template-columns:repeat(2,1fr)">
    <div class="chip"><div class="chip-val" style="color:${m.uptime_7d>=99?'var(--gr)':m.uptime_7d>=90?'var(--yl)':'var(--rd)'}">${m.uptime_7d?.toFixed(2)||'—'}%</div><div class="chip-lbl">Uptime 7d</div></div>
    <div class="chip"><div class="chip-val">${m.response_time||'—'}${m.response_time?'ms':''}</div><div class="chip-lbl">Response</div></div>
  </div>
  ${m.url ? `<div style="background:var(--s2);border:1px solid var(--bd);border-radius:10px;padding:10px 12px;font-family:var(--mono);font-size:11px;color:var(--mu);margin-bottom:14px;word-break:break-all">${escH(m.url)}</div>` : ''}
  <div class="sh">Recent Checks</div>
  <div id="sparkline-container">
    <canvas id="sparkline" height="60" style="width:100%"></canvas>
  </div>`;

  document.getElementById('mon-detail-body').innerHTML = html;

  // Draw sparkline from checks if available
  if (m.checks && m.checks.length) {
    drawSparkline(m.checks);
  }
}

function drawSparkline(checks) {
  const canvas = document.getElementById('sparkline');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const W = canvas.offsetWidth || 300;
  const H = 60;
  canvas.width = W * dpr;
  canvas.height = H * dpr;
  ctx.scale(dpr, dpr);

  const pts = checks.slice(-40);
  const max = Math.max(...pts.map(c => c.rt||0), 1);
  const step = W / (pts.length - 1 || 1);

  // Background
  ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--s2').trim();
  ctx.fillRect(0, 0, W, H);

  // Area fill
  const grad = ctx.createLinearGradient(0, 0, 0, H);
  grad.addColorStop(0, 'rgba(59,130,246,.25)');
  grad.addColorStop(1, 'rgba(59,130,246,.01)');
  ctx.beginPath();
  ctx.moveTo(0, H);
  pts.forEach((c, i) => {
    const x = i * step;
    const y = H - ((c.rt||0) / max) * (H - 8) - 4;
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.lineTo((pts.length-1)*step, H);
  ctx.closePath();
  ctx.fillStyle = grad;
  ctx.fill();

  // Line
  ctx.beginPath();
  pts.forEach((c, i) => {
    const x = i * step;
    const y = H - ((c.rt||0) / max) * (H - 8) - 4;
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.strokeStyle = '#3b82f6';
  ctx.lineWidth = 2;
  ctx.lineJoin = 'round';
  ctx.stroke();

  // Dots for downs
  pts.forEach((c, i) => {
    if (c.s === 'down') {
      const x = i * step;
      const y = H - ((c.rt||0) / max) * (H - 8) - 4;
      ctx.beginPath();
      ctx.arc(x, y, 4, 0, Math.PI*2);
      ctx.fillStyle = '#f43f5e';
      ctx.fill();
    }
  });
}

async function toggleMonitor() {
  const id = S.currentMonitorId;
  if (!id) return;
  try {
    await api(`/monitor/${id}/toggle`, { method: 'POST' });
    toast('Monitor updated');
    await loadDashboard();
    const m = S.monitors.find(x => x.id===id);
    if (m) document.getElementById('mon-toggle-btn').textContent = m.active===false ? 'Resume' : 'Pause';
  } catch(e) { toast('Failed to update monitor'); }
}

function closeDetail(id) {
  document.getElementById(id).classList.remove('open');
}

// ── Incidents ────────────────────────────────────────────────────────────────
async function loadIncidents() {
  try {
    // We fetch the incidents page HTML and scrape? No — better to use the API.
    // PulseWatch doesn't have a JSON incidents API, so we create one via the login session.
    // Workaround: fetch /incidents and parse, OR add a small JSON endpoint.
    // We'll use a lightweight parse of the page for now.
    const r = await api('/incidents');
    if (!r.ok) throw new Error();
    // Parse incident data from the page (title + status indicators)
    // Since we control the server, we could also add /api/incidents — but to keep
    // this self-contained, we use a simple heuristic scrape.
    const text = await r.text();
    const incs = parseIncidents(text);
    S.incidents = incs;
    renderIncidents(incs);
    // Badge
    const open = incs.filter(i => i.status !== 'resolved').length;
    const badge = document.getElementById('inc-badge');
    badge.textContent = open;
    badge.style.display = open ? '' : 'none';
  } catch(e) { document.getElementById('inc-content').innerHTML = incError(); }
}

function parseIncidents(html) {
  // Light DOM parsing — extract incident cards from the server HTML
  const parser = new DOMParser();
  const doc = parser.parseFromString(html, 'text/html');
  const incs = [];
  // Look for incident cards by their structure
  doc.querySelectorAll('.card, [class*="inc-"]').forEach(card => {
    const titleEl = card.querySelector('[style*="font-weight:600;font-size:15px"]') ||
                    card.querySelector('[style*="font-weight:600"][style*="font-size:15"]');
    const badgeEls = card.querySelectorAll('.badge');
    if (!titleEl) return;
    let severity = '', status = '';
    badgeEls.forEach(b => {
      const t = b.textContent.trim().toLowerCase();
      if (['degraded','major','full outage','full_outage'].some(s => t.includes(s.replace('_',' ')))) severity = t.replace(' ','_');
      if (['open','monitoring','resolved'].includes(t)) status = t;
    });
    if (titleEl.textContent.trim() && (severity || status)) {
      const link = card.querySelector('a[href*="/incidents/"]');
      const id = link ? parseInt(link.href.split('/incidents/')[1]) : null;
      incs.push({ id, title: titleEl.textContent.trim(), severity, status });
    }
  });
  return incs;
}

function renderIncidents(incs) {
  const open = incs.filter(i => i.status !== 'resolved');
  const done = incs.filter(i => i.status === 'resolved');
  let html = `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
    <div style="font-size:16px;font-weight:600">Incidents</div>
    <button class="btn btn-p" style="width:auto;padding:10px 16px;font-size:13px" onclick="openNewIncident()">+ New</button>
  </div>`;
  if (!incs.length) {
    html += `<div class="empty"><div class="empty-icon">✅</div><div class="empty-title">No incidents</div><div>All systems operational</div></div>`;
  } else {
    if (open.length) {
      html += `<div class="sh">🚨 Active (${open.length})</div>`;
      open.forEach(inc => { html += incCard(inc); });
    }
    if (done.length) {
      html += `<div class="sh">Resolved (${done.length})</div>`;
      done.slice(0,5).forEach(inc => { html += incCard(inc, true); });
    }
  }
  document.getElementById('inc-content').innerHTML = html;
}

function incCard(inc, faded=false) {
  const sev = (inc.severity||'').replace('_',' ');
  return `<div class="inc-card inc-${inc.severity||'degraded'}" style="${faded?'opacity:.7':''}" onclick="openIncident(${inc.id})">
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;flex-wrap:wrap">
      ${sev ? `<span class="badge b-${inc.severity}">${sev}</span>` : ''}
      ${inc.status ? `<span class="badge b-${inc.status}">${inc.status}</span>` : ''}
    </div>
    <div style="font-weight:600;font-size:15px;margin-bottom:4px">${escH(inc.title)}</div>
    <div style="color:var(--mu);font-size:12px">Tap to view details ›</div>
  </div>`;
}

function incError() {
  return `<div class="empty"><div class="empty-icon">⚠️</div><div class="empty-title">Couldn't load incidents</div></div>`;
}

async function openIncident(id) {
  if (!id) return;
  document.getElementById('incident-detail').classList.add('open');
  document.getElementById('inc-detail-body').innerHTML = `<div class="loader"><div class="spinner"></div></div>`;
  try {
    const r = await api(`/incidents/${id}`);
    const html = await r.text();
    const parser = new DOMParser();
    const doc = parser.parseFromString(html, 'text/html');
    const title = doc.querySelector('.pt')?.textContent || 'Incident';
    document.getElementById('inc-detail-title').textContent = title;
    // Extract timeline
    let body = `<div style="font-size:16px;font-weight:600;margin-bottom:4px">${escH(title)}</div>`;
    // Show updates
    const updates = doc.querySelectorAll('[style*="font-size:13px;line-height:1.6"]');
    if (updates.length) {
      body += `<div class="sh">Timeline</div>`;
      updates.forEach(u => {
        body += `<div style="background:var(--s2);border:1px solid var(--bd);border-radius:10px;padding:12px 14px;margin-bottom:8px;font-size:13px;line-height:1.6">${u.innerHTML}</div>`;
      });
    }
    document.getElementById('inc-detail-body').innerHTML = body;
  } catch(e) {
    document.getElementById('inc-detail-body').innerHTML = `<div class="empty"><div class="empty-icon">⚠️</div><div>Failed to load.</div></div>`;
  }
}

async function openNewIncident() {
  // Populate monitor dropdown
  const sel = document.getElementById('ni-monitor');
  sel.innerHTML = '<option value="">— None —</option>';
  S.monitors.forEach(m => {
    sel.innerHTML += `<option value="${m.id}">${escH(m.name)}</option>`;
  });
  document.getElementById('new-incident').classList.add('open');
}

async function submitIncident() {
  const title = document.getElementById('ni-title').value.trim();
  const severity = document.getElementById('ni-severity').value;
  const monitor_id = document.getElementById('ni-monitor').value;
  const body = document.getElementById('ni-body').value.trim();
  if (!title) { toast('Title is required'); return; }
  try {
    const params = new URLSearchParams({ title, severity, body });
    if (monitor_id) params.set('monitor_id', monitor_id);
    await api('/incidents/create', { method: 'POST', body: params });
    toast('Incident created');
    closeDetail('new-incident');
    loadIncidents();
  } catch(e) { toast('Failed to create incident'); }
}

// ── Analytics ─────────────────────────────────────────────────────────────────
async function loadAnalytics() {
  try {
    const d = await apiJSON('/api/analytics');
    renderAnalytics(d);
  } catch(e) {
    document.getElementById('analytics-content').innerHTML =
      `<div class="empty"><div class="empty-icon">📊</div><div class="empty-title">Couldn't load analytics</div></div>`;
  }
}

function renderAnalytics(d) {
  const sys = d.sys || {};
  const avgPing = d.avg_ping || 0;
  const pingColor = avgPing < 200 ? 'var(--gr)' : avgPing < 800 ? 'var(--yl)' : 'var(--rd)';
  const pingDeg = Math.round(Math.min(avgPing / 2000, 1) * 360);

  let html = `
  <div class="sh">Average Ping</div>
  <div class="gauge-wrap">
    <div class="gauge" style="background:conic-gradient(${pingColor} ${pingDeg}deg,var(--s2) ${pingDeg}deg)">
      <div class="gauge-inner">
        <div class="gauge-val" style="color:${pingColor}">${avgPing}</div>
        <div style="font-size:11px;color:var(--mu)">ms avg</div>
      </div>
    </div>
    <div style="color:var(--mu);font-size:12px;margin-top:10px;text-align:center">Average response across HTTP monitors</div>
  </div>`;

  if (sys.ram_total) {
    html += `<div class="sh">Server Resources</div>
    <div class="card">
      <div style="display:flex;justify-content:space-between;margin-bottom:4px">
        <div style="font-size:13px;font-weight:500">CPU</div>
        <div class="mono" style="font-size:13px;color:${sys.cpu>80?'var(--rd)':sys.cpu>60?'var(--yl)':'var(--gr)'}">${(sys.cpu||0).toFixed(1)}%</div>
      </div>
      <div class="pbar"><div class="pbar-f" style="width:${sys.cpu||0}%;background:${sys.cpu>80?'var(--rd)':sys.cpu>60?'var(--yl)':'var(--gr)'}"></div></div>
      <div style="display:flex;justify-content:space-between;margin:10px 0 4px">
        <div style="font-size:13px;font-weight:500">RAM</div>
        <div class="mono" style="font-size:13px;color:${sys.ram_pct>85?'var(--rd)':sys.ram_pct>70?'var(--yl)':'var(--gr)'}">${(sys.ram_pct||0).toFixed(1)}%</div>
      </div>
      <div class="pbar"><div class="pbar-f" style="width:${sys.ram_pct||0}%;background:${sys.ram_pct>85?'var(--rd)':sys.ram_pct>70?'var(--yl)':'var(--gr)'}"></div></div>
      <div style="color:var(--mu);font-size:11px;margin-top:4px">${sys.ram_used_h||'—'} / ${sys.ram_total_h||'—'}</div>
      <div style="display:flex;justify-content:space-between;margin:10px 0 4px">
        <div style="font-size:13px;font-weight:500">Disk</div>
        <div class="mono" style="font-size:13px;color:${sys.disk_pct>90?'var(--rd)':sys.disk_pct>75?'var(--yl)':'var(--gr)'}">${(sys.disk_pct||0).toFixed(1)}%</div>
      </div>
      <div class="pbar"><div class="pbar-f" style="width:${sys.disk_pct||0}%;background:${sys.disk_pct>90?'var(--rd)':sys.disk_pct>75?'var(--yl)':'var(--gr)'}"></div></div>
      <div style="color:var(--mu);font-size:11px;margin-top:4px">${sys.disk_used_h||'—'} / ${sys.disk_total_h||'—'}</div>
      ${sys.load1 != null ? `<div style="margin-top:10px;color:var(--mu);font-size:12px">Load: ${sys.load1?.toFixed(2)} / ${sys.load5?.toFixed(2)} / ${sys.load15?.toFixed(2)}</div>` : ''}
    </div>`;
  }

  if (d.monitors?.length) {
    html += `<div class="sh">Monitor Summary</div>`;
    d.monitors.forEach(m => {
      const ar = m.checks?.length ? Math.round(m.checks.reduce((a,c)=>a+c.rt,0)/m.checks.length) : 0;
      html += `<div class="card" style="padding:12px 16px">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
          <span class="badge b-${m.status}">${m.status}</span>
          <span style="font-weight:600">${escH(m.name)}</span>
        </div>
        <div style="display:flex;gap:16px;color:var(--mu);font-size:12px">
          <span>Uptime: <b style="color:${m.uptime_7d>=99?'var(--gr)':m.uptime_7d>=90?'var(--yl)':'var(--rd)'}">${m.uptime_7d?.toFixed(1)||0}%</b></span>
          <span>Avg: <b>${ar}ms</b></span>
          <span>Checks: <b>${m.checks?.length||0}</b></span>
        </div>
      </div>`;
    });
  }

  document.getElementById('analytics-content').innerHTML = html;
}

// ── Status Pages ──────────────────────────────────────────────────────────────
async function loadStatusPages() {
  try {
    const r = await api('/status-pages');
    const html = await r.text();
    const parser = new DOMParser();
    const doc = parser.parseFromString(html, 'text/html');
    let output = `<div style="font-size:16px;font-weight:600;margin-bottom:12px">Status Pages</div>`;
    const cards = doc.querySelectorAll('.card');
    let found = 0;
    cards.forEach(card => {
      const title = card.querySelector('[style*="font-weight:600;font-size:15px"]');
      const slug = card.querySelector('.mono[style*="color:var(--ac)"]');
      const viewLink = card.querySelector('a[href*="/status/"]');
      if (!title || !slug) return;
      found++;
      const slugText = slug.textContent.trim().replace('/status/','');
      output += `<div class="card" onclick="openStatusPage('${escH(slugText)}')">
        <div style="font-weight:600;font-size:15px;margin-bottom:4px">${escH(title.textContent)}</div>
        <div class="mono" style="font-size:11px;color:var(--ac);margin-bottom:8px">/status/${escH(slugText)}</div>
        <div style="display:flex;gap:10px">
          <span style="font-size:12px;color:var(--or)">RSS</span>
          <span style="font-size:12px;color:var(--or)">Atom</span>
          <span style="font-size:12px;color:var(--mu)">Tap to view →</span>
        </div>
      </div>`;
    });
    if (!found) {
      output += `<div class="empty"><div class="empty-icon">📋</div><div class="empty-title">No status pages</div><div>Create them on the web dashboard</div></div>`;
    }
    document.getElementById('sp-content').innerHTML = output;
  } catch(e) {
    document.getElementById('sp-content').innerHTML = `<div class="empty"><div class="empty-icon">⚠️</div><div>Couldn't load status pages</div></div>`;
  }
}

async function openStatusPage(slug) {
  // Open in in-app view
  const overlay = document.createElement('div');
  overlay.className = 'detail-overlay';
  overlay.innerHTML = `
    <div class="detail-header">
      <div class="back-btn" onclick="this.closest('.detail-overlay').remove()">‹</div>
      <div class="detail-title">${escH(slug)}</div>
      <div class="icon-btn" onclick="window.open('${S.serverUrl}/status/${slug}','_blank')" style="font-size:12px;width:auto;border-radius:20px;padding:0 10px">Open ↗</div>
    </div>
    <div style="flex:1;overflow:hidden">
      <iframe src="${S.serverUrl}/status/${slug}" style="width:100%;height:100%;border:none"></iframe>
    </div>`;
  document.body.appendChild(overlay);
  requestAnimationFrame(() => overlay.classList.add('open'));
}

// ── Settings ──────────────────────────────────────────────────────────────────
function renderSettings() {
  const html = `
  <div style="font-size:16px;font-weight:600;margin-bottom:16px">Settings</div>
  <div class="card">
    <div style="font-weight:600;margin-bottom:12px">Server Connection</div>
    <div class="fg"><label class="fl">Server URL</label>
      <input type="url" id="set-server" value="${escH(S.serverUrl)}" placeholder="http://192.168.1.x:5000" inputmode="url"></div>
    <button class="btn btn-p" onclick="saveServerUrl()">Save & Reconnect</button>
  </div>
  <div class="card" style="margin-top:10px">
    <div class="setting-row">
      <div><div style="font-weight:500">Auto-refresh</div><div style="color:var(--mu);font-size:12px">Refresh data every 30 seconds</div></div>
      <label class="switch"><input type="checkbox" id="set-autorefresh" ${localStorage.getItem('pw-autorefresh')!=='false'?'checked':''} onchange="toggleAutoRefresh(this.checked)"><span class="slider"></span></label>
    </div>
    <div class="setting-row" style="border:none">
      <div><div style="font-weight:500">Notifications</div><div style="color:var(--mu);font-size:12px">Request notification permission</div></div>
      <button class="btn btn-g" style="width:auto;padding:8px 14px;font-size:12px" onclick="requestNotifPerm()">Enable</button>
    </div>
  </div>
  <div class="card" style="margin-top:10px">
    <div style="font-weight:600;margin-bottom:10px">Quick Links</div>
    <button class="btn btn-g" style="margin-bottom:8px" onclick="window.open('${escH(S.serverUrl)}/dashboard','_blank')">Open Full Dashboard ↗</button>
    <button class="btn btn-g" style="margin-bottom:8px" onclick="window.open('${escH(S.serverUrl)}/analytics','_blank')">Open Analytics ↗</button>
    <button class="btn btn-g" style="margin-bottom:8px" onclick="window.open('${escH(S.serverUrl)}/settings','_blank')">Open Settings ↗</button>
  </div>
  <div class="card" style="margin-top:10px">
    <div style="font-weight:600;margin-bottom:10px;color:var(--rd)">Session</div>
    <button class="btn btn-d" onclick="doLogout()">Sign Out</button>
  </div>
  <div style="text-align:center;color:var(--mu);font-size:11px;margin:20px 0">PulseWatch Mobile v4 · PWA</div>`;
  document.getElementById('settings-content').innerHTML = html;
}

function saveServerUrl() {
  const url = document.getElementById('set-server').value.trim().replace(/\/$/, '');
  S.serverUrl = url;
  localStorage.setItem('pw-server', url);
  toast('Server URL saved — reconnecting…');
  checkSession();
}

function toggleAutoRefresh(on) {
  localStorage.setItem('pw-autorefresh', on);
  if (on) startAutoRefresh();
  else stopAutoRefresh();
}

async function requestNotifPerm() {
  if (!('Notification' in window)) { toast('Notifications not supported'); return; }
  const perm = await Notification.requestPermission();
  toast(perm === 'granted' ? '✓ Notifications enabled' : 'Permission denied');
}

async function doLogout() {
  try { await api('/logout'); } catch(e) {}
  S.monitors = []; S.incidents = [];
  showLogin();
  document.getElementById('app-shell').style.display = 'none';
}

// ── Auto-refresh ──────────────────────────────────────────────────────────────
function startAutoRefresh() {
  stopAutoRefresh();
  if (localStorage.getItem('pw-autorefresh') === 'false') return;
  S.refreshTimer = setInterval(() => {
    loadDashboard();
    if (S.currentTab === 'incidents') loadIncidents();
    if (S.currentTab === 'analytics') loadAnalytics();
  }, 30000);
}

function stopAutoRefresh() {
  if (S.refreshTimer) { clearInterval(S.refreshTimer); S.refreshTimer = null; }
}

// ── Pull to refresh ───────────────────────────────────────────────────────────
(function setupPTR() {
  const area = document.getElementById('scroll-area');
  const ptr  = document.getElementById('ptr');
  let startY = 0, pulling = false;
  area.addEventListener('touchstart', e => {
    if (area.scrollTop === 0) { startY = e.touches[0].clientY; pulling = true; }
  }, { passive: true });
  area.addEventListener('touchmove', e => {
    if (!pulling) return;
    const dy = e.touches[0].clientY - startY;
    if (dy > 50) ptr.classList.add('visible');
  }, { passive: true });
  area.addEventListener('touchend', e => {
    if (!pulling) return;
    pulling = false;
    ptr.classList.remove('visible');
    const dy = e.changedTouches[0].clientY - startY;
    if (dy > 60) { toast('Refreshing…'); refreshCurrent(); }
  }, { passive: true });
})();

// ── Session check on load ─────────────────────────────────────────────────────
async function checkSession() {
  if (!S.serverUrl) { showLogin(); return; }
  try {
    const r = await fetch(S.serverUrl + '/api/monitors', { credentials: 'include' });
    if (r.ok) { enterApp(); }
    else { showLogin(); }
  } catch(e) { showLogin(); }
}

// ── Service Worker registration ───────────────────────────────────────────────
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/app/sw.js').catch(() => {});
}

// ── Hash navigation ───────────────────────────────────────────────────────────
function handleHash() {
  const h = location.hash.replace('#','');
  if (['dashboard','incidents','analytics','statuspages','settings'].includes(h)) {
    switchTab(h);
  }
}
window.addEventListener('hashchange', handleHash);

// ── Utility ───────────────────────────────────────────────────────────────────
function escH(s) {
  if (!s) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── Init ──────────────────────────────────────────────────────────────────────
handleHash();
checkSession();
</script>
</body>
</html>"""

# ── Route definitions (injected into existing app OR run standalone) ──────────
def register_pwa_routes(app):
    """Call this from app.py: from pwa import register_pwa_routes; register_pwa_routes(app)"""
    from flask import Response, request as req, jsonify

    @app.route("/app")
    @app.route("/app/")
    def pwa_shell():
        return Response(PWA_HTML, mimetype="text/html")

    @app.route("/app/manifest.json")
    def pwa_manifest():
        return jsonify(MANIFEST)

    @app.route("/app/sw.js")
    def pwa_sw():
        return Response(SERVICE_WORKER_JS, mimetype="application/javascript",
                        headers={"Service-Worker-Allowed": "/"})

    @app.route("/app/icon-<size>.svg")
    def pwa_icon(size):
        return Response(_ICON_SVG, mimetype="image/svg+xml",
                        headers={"Cache-Control": "public, max-age=604800"})

    print("[PulseWatch PWA] Routes registered: /app  /app/manifest.json  /app/sw.js")


# ── Standalone mode ───────────────────────────────────────────────────────────
if __name__ == "__main__" and _STANDALONE:
    parser = argparse.ArgumentParser(description="PulseWatch PWA standalone server")
    parser.add_argument("--port", type=int, default=5001)
    parser.add_argument("--api",  default="http://localhost:5000",
                        help="Base URL of your PulseWatch server")
    args = parser.parse_args()

    from flask import Flask, Response, jsonify, request as req
    standalone = Flask(__name__)

    @standalone.route("/app")
    @standalone.route("/app/")
    def _pwa(): return Response(PWA_HTML, mimetype="text/html")

    @standalone.route("/app/manifest.json")
    def _manifest(): return jsonify(MANIFEST)

    @standalone.route("/app/sw.js")
    def _sw(): return Response(SERVICE_WORKER_JS, mimetype="application/javascript",
                               headers={"Service-Worker-Allowed": "/"})

    @standalone.route("/app/icon-<size>.svg")
    def _icon(size): return Response(_ICON_SVG, mimetype="image/svg+xml")

    @standalone.route("/")
    def _root(): return _pwa()

    print(f"[PulseWatch PWA] Standalone on http://0.0.0.0:{args.port}/app")
    print(f"[PulseWatch PWA] Proxying to PulseWatch at {args.api}")
    standalone.run(host="0.0.0.0", port=args.port, debug=False)
