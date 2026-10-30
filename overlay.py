"""OBS Tarayici kaynagi icin altyazi sayfasi.

OBS'in Metin (GDI+) kaynagi dosyayi saniyede bir kontrol eder; kisa altyazilar o arada
gelip gidebilir. Bu sunucu ceviriyi Server-Sent Events ile sayfaya aninda iter.
Yalnizca standart kutuphane kullanir ve yalnizca 127.0.0.1'i dinler.
Sayfanin kendisi altyazi_sayfasi.html dosyasidir; OBS'e dosya olarak da eklenebilir.
"""
import json
import queue
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PAGE_FILE = Path(__file__).resolve().parent / 'altyazi_sayfasi.html'


class ExclusiveServer(ThreadingHTTPServer):
    # Windows'ta SO_REUSEADDR iki programin ayni portu sessizce paylasmasina izin verir;
    # o zaman OBS yanlis programa baglanabilir. Port dolu ise acikca hata verilsin.
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


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
                    body = PAGE_FILE.read_bytes()
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/html; charset=utf-8')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_error(404)

        self.httpd = ExclusiveServer(('127.0.0.1', port), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f'http://127.0.0.1:{self.port}'

    def stream(self, handler):
        handler.send_response(200)
        handler.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        handler.send_header('Cache-Control', 'no-cache')
        # Sayfa OBS'e yerel dosya olarak eklendiginde (file://) baglanabilsin.
        handler.send_header('Access-Control-Allow-Origin', '*')
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
