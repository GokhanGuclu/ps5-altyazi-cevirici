"""OBS Tarayici kaynagi icin altyazi sayfasi.

OBS'in Metin (GDI+) kaynagi dosyayi saniyede bir kontrol eder; kisa altyazilar o arada
gelip gidebilir. Bu sunucu ceviriyi Server-Sent Events ile sayfaya aninda iter.
Yalnizca standart kutuphane kullanir ve yalnizca 127.0.0.1'i dinler.
"""
import json
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGE = """<!doctype html>
<html lang="tr">
<head>
<meta charset="utf-8">
<title>Altyazı</title>
<style>
  html, body { margin: 0; height: 100%; overflow: hidden; background: transparent; }
  #box { position: absolute; left: 6%; right: 6%; bottom: var(--bottom); text-align: center;
         font-family: 'Segoe UI', Arial, sans-serif; font-weight: 700; color: #fff;
         font-size: var(--size); line-height: 1.25; }
  .line { display: block; paint-order: stroke fill; -webkit-text-stroke: var(--stroke) #000;
          text-shadow: 0 2px 6px rgba(0,0,0,.8); animation: in .12s ease-out; }
  .prev { font-size: .78em; opacity: .6; margin-bottom: .2em; }
  @keyframes in { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
</style>
</head>
<body>
<div id="box"></div>
<script>
  // URL ayarlari: ?size=46&bottom=14  (size: piksel, bottom: ekran yuksekliginin yuzdesi)
  const q = new URLSearchParams(location.search);
  const size = +(q.get('size') || 46);
  document.documentElement.style.setProperty('--size', size + 'px');
  document.documentElement.style.setProperty('--stroke', Math.max(4, Math.round(size / 7)) + 'px');
  document.documentElement.style.setProperty('--bottom', (q.get('bottom') || 14) + 'vh');
  const box = document.getElementById('box');
  let shown = [];
  function render(lines) {
    if (JSON.stringify(lines) === JSON.stringify(shown)) return;
    box.replaceChildren(...lines.map((text, i) => {
      const el = document.createElement('span');
      el.className = 'line' + (i < lines.length - 1 ? ' prev' : '');
      el.textContent = text;
      // Ayni satir guncelleniyorsa (harf harf beliren yazi) gecis animasyonu tekrarlanmasin.
      if (shown.includes(text) || (i === lines.length - 1 && shown.length && text.startsWith(shown[shown.length - 1].slice(0, 8)))) el.style.animation = 'none';
      return el;
    }));
    shown = lines;
  }
  const events = new EventSource('/events');
  events.onmessage = e => render(JSON.parse(e.data).lines || []);
</script>
</body>
</html>
"""


class OverlayServer:
    def __init__(self, port):
        self.port = port
        self.clients = set()
        self.lock = threading.Lock()
        self.last = json.dumps({'lines': []})
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.path.startswith('/events'):
                    server.stream(self)
                elif self.path == '/' or self.path.startswith('/?'):
                    body = PAGE.encode('utf-8')
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/html; charset=utf-8')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_error(404)

        self.httpd = ThreadingHTTPServer(('127.0.0.1', port), Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f'http://127.0.0.1:{self.port}'

    def stream(self, handler):
        handler.send_response(200)
        handler.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        handler.send_header('Cache-Control', 'no-cache')
        handler.end_headers()
        inbox = queue.Queue()
        with self.lock:
            self.clients.add(inbox)
            inbox.put(self.last)  # yeni baglanan sayfa guncel altyaziyi hemen gorsun
        try:
            while True:
                try:
                    message = f'data: {inbox.get(timeout=15)}\n\n'
                except queue.Empty:
                    message = ': ping\n\n'  # baglanti canli kalsin
                handler.wfile.write(message.encode('utf-8'))
                handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            with self.lock:
                self.clients.discard(inbox)

    def publish(self, lines):
        data = json.dumps({'lines': lines}, ensure_ascii=False)
        with self.lock:
            self.last = data
            for inbox in self.clients:
                inbox.put(data)
