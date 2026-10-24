"""Altyazi motoru: ekrandan OCR ile okur, NLLB ile Turkceye cevirir, altyazi.txt'ye yazar.

Arayuzden bagimsizdir; durum degisikliklerini on_update(dict) ile bildirir.
on_update is parcaciklarindan cagrilir, arayuz bunu kendi ana dongusune tasimalidir.
"""
import difflib
import json
import os
import re
import shutil
import threading
import time
import queue
from collections import OrderedDict
from pathlib import Path

import cv2
import mss
import numpy as np
import pytesseract

BASE = Path(__file__).resolve().parent
OUTPUT = BASE / 'altyazi.txt'
CONFIG = BASE / 'ayarlar.json'
HF_MODEL = 'facebook/nllb-200-distilled-600M'
DEFAULTS = {
    'region': None,
    'tesseract': r'C:\Program Files\Tesseract-OCR\tesseract.exe',
    'model': r'C:\AI-Models\nllb-200',
    'text_mode': 'light',  # light: acik renk yazi, dark: koyu renk yazi, none: esikleme yok
    'threshold': 170,
}
POLL = 0.12          # goruntu kontrol araligi (sn)
CHANGE_LIMIT = 2.0   # bu ortalama farkin altindaki kareler "degismedi" sayilir
CLEAR_AFTER = 1.3    # bu kadar sure yazi gorunmezse altyazi silinir (sn)
SIMILAR = 0.9        # bu oranin ustundeki iki okuma ayni metin sayilir
CACHE_SIZE = 500
SPEAKER = re.compile(r"^((?:[A-Z][\w'.-]*\s?){1,3}):\s*(.+)$")


def screen_capture():
    # mss 10 ile mss.mss yerini mss.MSS'e birakti.
    return getattr(mss, 'MSS', mss.mss)()


def monitors():
    """(sanal ekran, ana monitor) dondurur; sanal ekran tum monitorleri kapsar."""
    with screen_capture() as sct:
        screens = sct.monitors
    primary = next((m for m in screens[1:] if m.get('is_primary')), screens[1])
    return screens[0], primary


def load_config():
    config = dict(DEFAULTS)
    if CONFIG.exists():
        try:
            data = json.loads(CONFIG.read_text(encoding='utf-8'))
        except Exception:
            data = {}
        if 'left' in data:  # eski surum: dosyada yalnizca alan vardi
            data = {'region': data}
        config.update({k: v for k, v in data.items() if k in DEFAULTS})
    return config


def save_config(config):
    CONFIG.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding='utf-8')


def find_tesseract(path):
    if path and Path(path).exists():
        return path
    return shutil.which('tesseract')


def resolve_model(path):
    if path and (Path(path) / 'config.json').exists():
        return path, True
    return HF_MODEL, False


def write_output(text):
    # Gecici dosya + os.replace: OBS hicbir zaman yarim yazilmis dosya okumaz.
    tmp = OUTPUT.with_suffix('.tmp')
    tmp.write_text(text, encoding='utf-8')
    for _ in range(5):
        try:
            os.replace(tmp, OUTPUT)
            return
        except PermissionError:  # OBS dosyayi o an okuyor olabilir
            time.sleep(0.02)
    OUTPUT.write_text(text, encoding='utf-8')


def binarize(gray, mode, limit):
    # Tesseract en iyi beyaz zemin uzerinde siyah yaziyi okur.
    if mode == 'light':
        return cv2.threshold(gray, limit, 255, cv2.THRESH_BINARY_INV)[1]
    if mode == 'dark':
        return cv2.threshold(gray, 255 - limit, 255, cv2.THRESH_BINARY)[1]
    return gray


def clean_text(raw):
    text = re.sub(r'(\w)-\s*\n\s*(\w)', r'\1\2', raw)  # satir sonu tirelemesi
    text = ' '.join(text.split())
    return text.strip(' |_~')  # Tesseract'in kenarlarda urettigi cop karakterler


def similar(a, b):
    if not a or not b:
        return False
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio() >= SIMILAR


class Translator:
    def __init__(self, model_path, local):
        # Agir kutuphaneler burada yuklenir; arayuz hemen acilir.
        import torch
        from transformers import AutoTokenizer, AutoModelForSeq2SeqLM
        self.torch = torch
        self.device = 'cuda' if torch.cuda.is_available() else 'cpu'
        self.tokenizer = AutoTokenizer.from_pretrained(model_path, src_lang='eng_Latn',
                                                       local_files_only=local)
        self.model = AutoModelForSeq2SeqLM.from_pretrained(model_path, local_files_only=local)
        self.model = self.model.to(self.device).eval()
        self.target_id = self.tokenizer.convert_tokens_to_ids('tur_Latn')
        self._translate('Hello.')  # ilk cagri (CUDA isinmasi) birkac saniye surer; ilk altyazi beklemesin

    def translate(self, text):
        # "AD: konusma" biciminde yalnizca konusma cevrilir.
        match = SPEAKER.match(text)
        if match:
            return f'{match.group(1).strip()}: {self._translate(match.group(2))}'
        return self._translate(text)

    def _translate(self, text):
        with self.torch.inference_mode():
            inputs = self.tokenizer(text, return_tensors='pt', truncation=True,
                                    max_length=256).to(self.device)
            result = self.model.generate(**inputs, forced_bos_token_id=self.target_id,
                                         max_new_tokens=110, num_beams=1, do_sample=False)
        return self.tokenizer.batch_decode(result, skip_special_tokens=True)[0].strip()


class Engine:
    """on_update'e giden anahtarlar: state (loading/ready/stopped/error), status,
    english, turkish, ocr_ms, tr_ms, device."""

    def __init__(self, config, on_update):
        self.config = config
        self.on_update = on_update
        self.translator = None
        self.stop_event = threading.Event()
        self.stop_event.set()
        self.threads = []
        self.generation = 0
        self.gen_lock = threading.Lock()
        self.cache = OrderedDict()
        self.failed = False

    def ui(self, **values):
        self.on_update(values)

    def busy(self):
        return any(t.is_alive() for t in self.threads)

    def running(self):
        return self.busy() and not self.stop_event.is_set()

    def start(self, region):
        """Baslatir; baslatilamazsa kullaniciya gosterilecek hata metnini dondurur."""
        if not region:
            return 'Önce altyazı alanını seçin.'
        if self.busy():
            return None if self.running() else 'Önceki oturum hâlâ kapanıyor, birkaç saniye sonra tekrar deneyin.'
        tesseract = find_tesseract(self.config['tesseract'])
        if not tesseract:
            return (f"Tesseract bulunamadı: {self.config['tesseract']}\n"
                    "Yolu ayarlar.json içindeki 'tesseract' alanına yazın.")
        pytesseract.pytesseract.tesseract_cmd = tesseract
        write_output('')
        self.failed = False
        # Her oturumun kendi durdurma sinyali ve kuyrugu var; eski is parcaciklari yenilere karismaz.
        stop = self.stop_event = threading.Event()
        pending = queue.Queue(maxsize=1)
        self.threads = [threading.Thread(target=self.translation_loop, args=(stop, pending), daemon=True),
                        threading.Thread(target=self.run, args=(stop, pending, dict(region)), daemon=True)]
        for thread in self.threads:
            thread.start()
        return None

    def stop(self):
        self.stop_event.set()

    def enqueue(self, pending, text):
        with self.gen_lock:
            self.generation += 1
            job = (self.generation, text)
        try:
            while True:
                pending.get_nowait()
        except queue.Empty:
            pass
        pending.put_nowait(job)

    def clear_output(self):
        with self.gen_lock:
            self.generation += 1
            write_output('')
        self.ui(english='', turkish='')

    def cached_translate(self, english):
        if english in self.cache:
            self.cache.move_to_end(english)
            return self.cache[english]
        turkish = self.translator.translate(english)
        self.cache[english] = turkish
        if len(self.cache) > CACHE_SIZE:
            self.cache.popitem(last=False)
        return turkish

    def translation_loop(self, stop, pending):
        try:
            if self.translator is None:
                model_path, local = resolve_model(self.config['model'])
                source = 'yerel klasörden' if local else "HuggingFace'ten (ilk açılışta indirilir)"
                self.ui(state='loading', status=f'Model yükleniyor {source}...')
                self.translator = Translator(model_path, local)
            device = self.translator.device.upper()
            self.ui(state='ready', status='Altyazılar izleniyor', device=device)
            while not stop.is_set():
                try:
                    generation, english = pending.get(timeout=0.2)
                except queue.Empty:
                    continue
                started = time.perf_counter()
                turkish = self.cached_translate(english)
                ms = int((time.perf_counter() - started) * 1000)
                with self.gen_lock:
                    if generation != self.generation or stop.is_set():
                        continue
                    write_output(turkish)
                self.ui(turkish=turkish, tr_ms=ms)
        except Exception as exc:
            self.failed = True
            stop.set()
            self.ui(state='error', status=f'Model hatası: {exc}')

    def run(self, stop, pending, region):
        mode, limit = self.config['text_mode'], int(self.config['threshold'])
        committed = ''  # ceviriye gonderilen son metin
        candidate = ''  # bir kez okunmus, onay bekleyen metin
        visible = False
        last_seen = time.monotonic()
        previous_small = None
        try:
            with screen_capture() as sct:
                while not stop.is_set():
                    gray = cv2.cvtColor(np.asarray(sct.grab(region)), cv2.COLOR_BGRA2GRAY)
                    # Degisim yazi maskesi uzerinden olculur; arkadaki hareketli sahne OCR'yi tetiklemez.
                    small = cv2.resize(binarize(gray, mode, limit), (64, 32), interpolation=cv2.INTER_AREA)
                    changed = previous_small is None or np.mean(cv2.absdiff(small, previous_small)) >= CHANGE_LIMIT
                    if changed:
                        previous_small = small
                        started = time.perf_counter()
                        big = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
                        raw = pytesseract.image_to_string(binarize(big, mode, limit), lang='eng', config='--psm 6')
                        self.ui(ocr_ms=int((time.perf_counter() - started) * 1000))
                        current = clean_text(raw)
                        visible = len(current) >= 5 and re.search('[A-Za-z]', current) is not None
                        if not visible:
                            candidate = ''
                        else:
                            last_seen = time.monotonic()
                            if similar(current, committed):
                                candidate = ''  # ayni altyazinin gurultulu okumasi
                            elif similar(current, candidate):
                                committed, candidate = current, ''
                                self.ui(english=current)
                                self.enqueue(pending, current)
                            else:
                                candidate = current  # harf harf beliren yazi olabilir; dogrulanmayi bekle
                    elif candidate:
                        # Goruntu sabit kaldi: aday metin tamamlanmis demektir.
                        committed, candidate = candidate, ''
                        self.ui(english=committed)
                        self.enqueue(pending, committed)
                    if not visible and committed and time.monotonic() - last_seen > CLEAR_AFTER:
                        committed = ''
                        self.clear_output()
                    stop.wait(POLL)
        except Exception as exc:
            self.failed = True
            stop.set()
            self.ui(state='error', status=f'OCR hatası: {exc}')
        else:
            if not self.failed:  # hata mesaji 'Durduruldu' ile ezilmesin
                self.ui(state='stopped', status='Durduruldu')
