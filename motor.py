"""Altyazi motoru: goruntuyu OCR ile okur, NLLB ile Turkceye cevirir, altyazi.txt'ye yazar.

Goruntu iki kaynaktan gelebilir:
  camera: OBS Sanal Kamera (yakalama kartinin tam 1080p goruntusu, pencereler araya giremez)
  screen: ekranin secilen bolgesi

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

from overlay import OverlayServer

BASE = Path(__file__).resolve().parent
OUTPUT = BASE / 'altyazi.txt'
CONFIG = BASE / 'ayarlar.json'
HF_MODEL = 'facebook/nllb-200-distilled-600M'
DEFAULTS = {
    'source': 'camera',               # camera: OBS Sanal Kamera, screen: ekran bolgesi
    'camera': 'OBS Virtual Camera',
    'camera_region': None,            # kamera karesi icindeki altyazi alani
    'region': None,                   # ekrandaki altyazi alani
    'tesseract': r'C:\Program Files\Tesseract-OCR\tesseract.exe',
    'model': r'C:\AI-Models\nllb-200',
    'text_mode': 'light',  # light: acik renk yazi, dark: koyu renk yazi, none: esikleme yok
    'min_contrast': 25,    # yazinin cevresinden en az bu kadar parlak (koyu) olmasi gerekir
    'min_confidence': 45,  # Tesseract'in bu puanin altinda okudugu kelimeler atilir
    'overlay_port': 8765,  # OBS Tarayici kaynagi: http://127.0.0.1:8765
}
POLL = 0.05          # goruntu kontrol araligi (sn)
CHANGE_GRID = (160, 40)  # degisim kontrolu icin kucultulmus yazi maskesi
CHANGE_PIXELS = 8        # bundan az piksel degistiyse kare "degismedi" sayilir
CLEAR_AFTER = 0.7    # bu kadar sure yazi gorunmezse altyazi silinir (sn)
MIN_SHOW = 1.5       # her ceviri en az bu kadar ekranda kalir (sn)
KEEP_PREVIOUS = 2.5  # yeni altyazi gelince, bundan kisa sure gorunmus onceki ceviri ustte kalir (sn)
SIMILAR = 0.9        # bu oranin ustundeki iki okuma ayni metin sayilir
CACHE_SIZE = 500
TEXT_KERNEL = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15))  # harf kalinligindan genis
OCR_LETTER_HEIGHT = 16   # OCR'den once harfler bu yukseklige olceklenir (px)
CAMERA_SIZE = (1920, 1080)
WORD = re.compile(r"[\"'“‘(\[*-]*[A-Za-z0-9][A-Za-z0-9'’.-]*[.,!?:;…)\]\"'”’*]*")
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


def list_cameras():
    """DirectShow kamera adlari; sirasi cv2.CAP_DSHOW indeksleriyle aynidir.
    COM kullandigi icin ana is parcacigindan cagrilmalidir."""
    try:
        from pygrabber.dshow_graph import FilterGraph
        return FilterGraph().get_input_devices()
    except Exception:
        return []


def find_camera(name):
    """(indeks, hata metni) dondurur."""
    names = list_cameras()
    if name in names:
        return names.index(name), None
    return None, (f"'{name}' bulunamadı.\n"
                  "OBS'i açın ve sağ alttaki 'Sanal Kamerayı Başlat' düğmesine basın.")


def open_camera(index):
    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_SIZE[0])
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_SIZE[1])
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


def grab_camera_frame(index):
    """Alan secimi icin tek kare (BGR) dondurur; okunamazsa None."""
    cap = open_camera(index)
    frame = None
    try:
        for _ in range(5):  # ilk kareler bos/siyah gelebilir
            ok, image = cap.read()
            if ok:
                frame = image
    finally:
        cap.release()
    return frame


class CameraReader:
    """Kameradan surekli okur, her zaman en yeni kareyi tutar (bekleyen kare birikmez)."""

    def __init__(self, index):
        self.index = index
        self.frame = None
        self.error = None
        self.lock = threading.Lock()
        self.ready = threading.Event()
        self.closed = threading.Event()
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()
        if not self.ready.wait(5):
            self.error = self.error or 'Kameradan görüntü alınamadı.'
        if self.error:
            self.close()
            raise RuntimeError(self.error)

    def loop(self):
        cap = open_camera(self.index)  # acan ve okuyan ayni is parcacigi olsun
        try:
            if not cap.isOpened():
                self.error = 'Kamera açılamadı. Başka bir program kullanıyor olabilir.'
                self.ready.set()
                return
            while not self.closed.is_set():
                ok, frame = cap.read()
                if not ok:
                    time.sleep(0.01)
                    continue
                with self.lock:
                    self.frame = frame
                self.ready.set()
        finally:
            cap.release()

    def latest(self):
        with self.lock:
            return self.frame

    def close(self):
        self.closed.set()


def active_region(config):
    return config['camera_region'] if config['source'] == 'camera' else config['region']


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


def text_mask(gray, mode, min_contrast):
    """Altyaziyi arka plandan ayirir; beyaz zemin uzerinde siyah yazi dondurur (Tesseract boyle sever).

    Sabit parlaklik esigi ise yaramaz: bazi oyunlarda yazi koyu gridir (~120) ve arkadaki gomlek,
    el gibi nesneler yazidan parlaktir. Bunun yerine yerel kontrasta bakilir (morfolojik top-hat):
    cevresinden parlak INCE cizgiler yazi sayilir, genis parlak alanlar elenir. Ardindan ayni satira
    dizilmeyen ya da harf boyutunda olmayan parcalar atilir."""
    if mode == 'none':
        return cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    op = cv2.MORPH_TOPHAT if mode == 'light' else cv2.MORPH_BLACKHAT
    contrast = cv2.morphologyEx(gray, op, TEXT_KERNEL)
    blank = np.full_like(gray, 255)
    if np.percentile(contrast, 99.7) < min_contrast:
        return blank  # belirgin yazi yok; Otsu bos karede gurultuyu yazi sanmasin
    ink = cv2.threshold(contrast, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    return 255 - keep_text_components(ink)


def keep_text_components(ink):
    """Yalnizca ayni satirlara dizilmis, harf boyutundaki parcalari birakir (255 = yazi)."""
    n, labels, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    boxes = [(i, *stats[i][:4]) for i in range(1, n) if stats[i][4] >= 4 and stats[i][3] >= 3]
    tall = [h for _, _, _, _, h in boxes if h >= 6]
    if not tall:
        return np.zeros_like(ink)
    height = float(np.median(tall))  # tipik harf yuksekligi
    # Harften buyuk lekeler ve ince yatay yaylar (arka plandaki kenarlar) elenir.
    candidates = [b for b in boxes if b[4] <= 1.8 * height and b[3] <= 4 * height
                  and not (b[4] < 0.5 * height and b[3] > 2.5 * b[4])]
    # Satirlara grupla: dikey merkezleri yakin olan parcalar ayni satirdadir.
    candidates.sort(key=lambda b: b[2] + b[4] / 2)
    lines, current = [], []
    for box in candidates:
        center = box[2] + box[4] / 2
        if current and center - (current[-1][2] + current[-1][4] / 2) > 0.6 * height:
            lines.append(current)
            current = []
        current.append(box)
    if current:
        lines.append(current)
    keep = np.zeros(n, bool)
    for line in lines:
        letters = [b for b in line if b[4] >= 0.5 * height]
        if len(letters) < 3:  # tek basina duran leke satir sayilmaz
            continue
        # Harfler yan yana sik dizilir; arka plandan kalan lekeler satira seyrek dagilir.
        left = min(b[1] for b in letters)
        right = max(b[1] + b[3] for b in letters)
        if sum(b[3] for b in letters) >= 0.35 * (right - left):
            keep[[b[0] for b in line]] = True
    return np.where(keep[labels], 255, 0).astype(np.uint8)


def clean_text(raw):
    text = re.sub(r'(\w)-\s*\n\s*(\w)', r'\1\2', raw)  # satir sonu tirelemesi
    text = ' '.join(text.split())
    text = re.sub(r'(?<![^\s.,!?;:])\|(?=\s|$)', 'I', text)  # tek basina "|" aslinda "I" zamiridir
    return text.strip(' |_~')  # Tesseract'in kenarlarda urettigi cop karakterler


def crop_to_ink(image, pad=12):
    """Binarize goruntuyu yazinin (siyah pikseller) cevresinden kirpar; yazi yoksa None."""
    ink = image < 128
    rows = np.where(ink.sum(axis=1) > 2)[0]
    cols = np.where(ink.sum(axis=0) > 1)[0]
    if not len(rows) or not len(cols):
        return None
    h, w = image.shape
    return image[max(0, rows[0] - pad):min(h, rows[-1] + pad + 1),
                 max(0, cols[0] - pad):min(w, cols[-1] + pad + 1)]


def text_lines(image):
    """Goruntudeki yazi satirlarinin (ust, alt) sinirlari.
    Piksel yogunluguna degil harflerin dikey merkezlerine bakilir: birbirine cok yakin iki
    satir birlesmez (kuyruklu harfler araya girse de), tek kelimelik satir da kaybolmaz."""
    ink = (image < 128).astype(np.uint8)
    stats = cv2.connectedComponentsWithStats(ink, connectivity=8)[2][1:]
    stats = [s for s in stats if s[3] >= 4]
    if not stats:
        return []
    height = float(np.median([s[3] for s in stats]))
    letters = sorted((s for s in stats if s[3] >= 0.6 * height), key=lambda s: s[1] + s[3] / 2)
    groups, current = [], []
    for s in letters:
        if current and (s[1] + s[3] / 2) - (current[-1][1] + current[-1][3] / 2) > 0.7 * height:
            groups.append(current)
            current = []
        current.append(s)
    if current:
        groups.append(current)
    return [(min(s[1] for s in g), max(s[1] + s[3] for s in g)) for g in groups if len(g) >= 2]


def letter_height(image):
    """Tipik harf yuksekligi: murekkep parcalarinin yuksekliklerinin medyani."""
    ink = (image < 128).astype(np.uint8)
    stats = cv2.connectedComponentsWithStats(ink, connectivity=8)[2][1:]
    heights = [h for h in stats[:, 3] if h >= 4] if len(stats) else []
    return float(np.median(heights)) if heights else float(image.shape[0])


def normalize_text_size(image):
    """Kirpilmis yaziyi Tesseract'in en iyi okudugu harf yuksekligine olcekler.
    Olcum: cok buyuk yazida Tesseract kisa kelimeleri ("Run!") hic gormeyebiliyor, cok
    kucukte harfleri karistiriyor. Satir yuksekligi yerine harf yuksekligi kullanilir;
    birbirine cok yakin iki satir tek satir sanilip yazi kuculmesin."""
    image = crop_to_ink(image, pad=0)
    if image is None:
        return None
    factor = OCR_LETTER_HEIGHT / letter_height(image)
    if abs(factor - 1) > 0.1:
        image = cv2.resize(image, None, fx=factor, fy=factor,
                           interpolation=cv2.INTER_AREA if factor < 1 else cv2.INTER_CUBIC)
        image = cv2.threshold(image, 128, 255, cv2.THRESH_BINARY)[1]
    pad = OCR_LETTER_HEIGHT
    return cv2.copyMakeBorder(image, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=255)


def read_text(image, min_confidence):
    """Tesseract'in guvenle okudugu kelimeleri satir satir birlestirir."""
    image = normalize_text_size(image)
    if image is None:
        return ''
    lines = text_lines(image)
    # Blok modu (6) kisa yazilari ("Run!", "No, no.") cogu zaman hic gormez; tek satirlik
    # altyazi dogrudan satir moduyla (7) okunur. Cok satirlida blok modu, olmazsa satir satir.
    if len(lines) <= 1:
        return ocr_words(image, min_confidence, 7)
    text = ocr_words(image, min_confidence, 6)
    if text:
        return text
    h, pad = image.shape[0], OCR_LETTER_HEIGHT
    return clean_text('\n'.join(ocr_words(image[max(0, a - pad):min(h, b + pad)], min_confidence, 7)
                                for a, b in lines))


def ocr_words(image, min_confidence, psm):
    data = pytesseract.image_to_data(image, lang='eng', config=f'--psm {psm}',
                                     output_type=pytesseract.Output.DICT)
    lines = OrderedDict()
    for i, word in enumerate(data['text']):
        word = word.strip()
        if not word or float(data['conf'][i]) < min_confidence:
            continue
        key = (data['block_num'][i], data['par_num'][i], data['line_num'][i])
        lines.setdefault(key, []).append(word)
    return clean_text('\n'.join(' '.join(words) for words in lines.values()))


def looks_like_text(text):
    # Arka plandaki nesnelerden cikan "& i Ne. . t ; wl" gibi okumalari eler ("No, no." gibi
    # kisa gercek altyazilar kalir): parcalarin cogu kelime gibi olmali (harflerden olusan,
    # yalnizca kenarlarinda noktalama tasiyan) ve en az bir parca iki harfli olmali.
    # Tek basina "-", "*", "..." gibi isaretler (diyalog cizgisi, sarki yildizi) hesaba katilmaz;
    # "I" ve "a" disindaki tek karakterlik parcalar kelime sayilmaz.
    tokens = [t for t in text.split() if not re.fullmatch(r'[-*.…—–]+', t)]
    if not tokens:
        return False
    words = [t for t in tokens if WORD.fullmatch(t) and
             (sum(c.isalnum() for c in t) >= 2 or t.strip('"\'“”‘’.,!?:;()[]*') in ('I', 'a', 'A'))]
    if not any(sum(c.isalpha() for c in w) >= 2 for w in words):
        return False
    return len(words) >= 0.75 * len(tokens)


def similar(a, b):
    if not a or not b:
        return False
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio() >= SIMILAR


def subtitle_change(current, committed):
    """'new' (yeni altyazi), 'grow' (harf harf beliren yazi buyudu) ya da None (ayni altyazi)."""
    if not committed:
        return 'new'
    if len(current) > len(committed) + 2 and similar(committed, current[:len(committed)]):
        return 'grow'
    # Ayni altyazinin gurultulu okumasi ya da eksik okunmus parcasi: atla.
    if similar(current, committed) or current.lower() in committed.lower():
        return None
    return 'new'


def sentences(text):
    return [s for s in re.split(r'(?<=[.!?…])\s+(?:-\s*)?', text.strip()) if s]


def tidy_translation(source, translated):
    # NLLB altyazi verisiyle egitildigi icin kisa ifadeleri tekrarlar ("Go!" -> "Git! Git!")
    # ve basina diyalog cizgisi koyar. Kaynaktaki cumle sayisindan fazlasini ve kaynakta
    # olmayan cizgiyi atar.
    if not source.lstrip().startswith('-'):
        translated = re.sub(r'^\s*-\s*', '', translated)
    parts = sentences(translated)
    keep = max(1, len(sentences(source)))
    return ' '.join(parts[:keep]) if len(parts) > keep else translated


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
        return tidy_translation(text, self._generate(text))

    def _generate(self, text):
        with self.torch.inference_mode():
            inputs = self.tokenizer(text, return_tensors='pt', truncation=True,
                                    max_length=256).to(self.device)
            # Cikti girdiden cok uzun olamaz; tekrar cezasi "X X X X..." donguleri onler.
            limit = min(110, int(inputs['input_ids'].shape[1] * 1.8) + 4)
            result = self.model.generate(**inputs, forced_bos_token_id=self.target_id,
                                         max_new_tokens=limit, num_beams=1, do_sample=False,
                                         repetition_penalty=1.2, no_repeat_ngram_size=3)
        return self.tokenizer.batch_decode(result, skip_special_tokens=True)[0].strip()


class Engine:
    """on_update'e giden anahtarlar: state (loading/ready/stopped/error), status,
    english, turkish, ocr_ms, tr_ms, total_ms, device."""

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
        self.lines = []  # ekrandaki ceviriler: [metin, gosterilme zamani]
        self.overlay = None

    def ui(self, **values):
        self.on_update(values)

    def busy(self):
        return any(t.is_alive() for t in self.threads)

    def running(self):
        return self.busy() and not self.stop_event.is_set()

    def start(self):
        """Baslatir; baslatilamazsa kullaniciya gosterilecek hata metnini dondurur.
        Kamera aramasi COM kullandigi icin ana is parcacigindan cagrilmalidir."""
        region = active_region(self.config)
        if not region:
            return 'Önce altyazı alanını seçin.'
        if self.busy():
            return None if self.running() else 'Önceki oturum hâlâ kapanıyor, birkaç saniye sonra tekrar deneyin.'
        camera = None
        if self.config['source'] == 'camera':
            camera, error = find_camera(self.config['camera'])
            if error:
                return error
        tesseract = find_tesseract(self.config['tesseract'])
        if not tesseract:
            return (f"Tesseract bulunamadı: {self.config['tesseract']}\n"
                    "Yolu ayarlar.json içindeki 'tesseract' alanına yazın.")
        pytesseract.pytesseract.tesseract_cmd = tesseract
        if self.overlay is None:
            try:
                self.overlay = OverlayServer(int(self.config['overlay_port']))
            except OSError:  # port dolu (ornegin uygulama iki kez acik): yalnizca altyazi.txt kullanilir
                self.overlay = None
        with self.gen_lock:
            self.lines = []
            self.publish()
        self.failed = False
        # Her oturumun kendi durdurma sinyali ve kuyrugu var; eski is parcaciklari yenilere karismaz.
        stop = self.stop_event = threading.Event()
        pending = queue.Queue(maxsize=1)
        self.threads = [threading.Thread(target=self.translation_loop, args=(stop, pending), daemon=True),
                        threading.Thread(target=self.run, args=(stop, pending, dict(region), camera),
                                         daemon=True)]
        for thread in self.threads:
            thread.start()
        return None

    def stop(self):
        self.stop_event.set()

    def enqueue(self, pending, text, detected, replace):
        with self.gen_lock:
            self.generation += 1
            job = (self.generation, text, detected, replace)
        try:
            while True:
                pending.get_nowait()
        except queue.Empty:
            pass
        pending.put_nowait(job)

    def publish(self):
        # gen_lock altinda cagrilir.
        texts = [text for text, _ in self.lines]
        write_output('\n'.join(texts))
        if self.overlay:
            self.overlay.publish(texts)

    def show(self, turkish, replace):
        now = time.monotonic()
        if replace and self.lines:
            self.lines[-1] = [turkish, self.lines[-1][1]]
        else:
            # Hizli gecen bir altyazinin cevirisi hemen kaybolmasin: kisa sure once gelmisse ustte kalir.
            previous = [line for line in self.lines[-1:] if now - line[1] < KEEP_PREVIOUS]
            self.lines = previous + [[turkish, now]]
        self.publish()

    def can_clear(self):
        return not self.lines or time.monotonic() - self.lines[-1][1] >= MIN_SHOW

    def clear_output(self):
        with self.gen_lock:
            self.generation += 1
            self.lines = []
            self.publish()
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
                    generation, english, detected, replace = pending.get(timeout=0.2)
                except queue.Empty:
                    continue
                started = time.perf_counter()
                turkish = self.cached_translate(english)
                done = time.perf_counter()
                with self.gen_lock:
                    if generation != self.generation or stop.is_set():
                        continue
                    self.show(turkish, replace)
                self.ui(turkish=turkish, tr_ms=int((done - started) * 1000),
                        total_ms=int((time.perf_counter() - detected) * 1000))
        except Exception as exc:
            self.failed = True
            stop.set()
            self.ui(state='error', status=f'Model hatası: {exc}')

    def run(self, stop, pending, region, camera):
        mode, min_contrast = self.config['text_mode'], float(self.config['min_contrast'])
        min_confidence = float(self.config['min_confidence'])
        committed = ''  # ceviriye gonderilen son metin
        visible = False
        last_seen = time.monotonic()
        previous_small = None
        reader = sct = None
        try:
            if camera is not None:
                reader = CameraReader(camera)
            else:
                sct = screen_capture()
            while not stop.is_set():
                gray = self.grab(reader, sct, region)
                if gray is None:
                    stop.wait(POLL)
                    continue
                # Degisim yazi maskesi uzerinden olculur; arkadaki hareketli sahne OCR'yi tetiklemez.
                # Ortalama fark yerine degisen piksel sayisina bakilir: "No." yerine "Go!" gelmesi
                # gibi kucuk degisiklikler de yakalanir.
                mask = text_mask(gray, mode, min_contrast)
                small = cv2.resize(mask, CHANGE_GRID, interpolation=cv2.INTER_AREA)
                changed = (previous_small is None or
                           np.count_nonzero(cv2.absdiff(small, previous_small) > 48) >= CHANGE_PIXELS)
                if changed:
                    previous_small = small
                    detected = time.perf_counter()
                    current = read_text(mask, min_confidence)
                    self.ui(ocr_ms=int((time.perf_counter() - detected) * 1000))
                    visible = looks_like_text(current)
                    if not visible and np.mean(small < 128) > 0.005:
                        # Karede yazi izi var ama okunamadi (yazi henuz tam cizilmemis olabilir):
                        # kare degismese de bir sonraki turda yeniden dene.
                        previous_small = None
                    if visible:
                        last_seen = time.monotonic()
                        change = subtitle_change(current, committed)
                        if change:
                            committed = current
                            self.ui(english=current)
                            self.enqueue(pending, current, detected, change == 'grow')
                elif visible:
                    last_seen = time.monotonic()  # kare degismedi: altyazi hala ekranda
                if (not visible and committed and time.monotonic() - last_seen > CLEAR_AFTER
                        and self.can_clear()):
                    committed = ''
                    self.clear_output()
                stop.wait(POLL)
        except Exception as exc:
            self.failed = True
            stop.set()
            self.ui(state='error', status=f'Görüntü hatası: {exc}')
        else:
            self.clear_output()  # durdurulunca ekranda eski ceviri kalmasin
            if not self.failed:  # hata mesaji 'Durduruldu' ile ezilmesin
                self.ui(state='stopped', status='Durduruldu')
        finally:
            if reader:
                reader.close()
            if sct:
                sct.close()

    @staticmethod
    def grab(reader, sct, region):
        if reader is None:
            return cv2.cvtColor(np.asarray(sct.grab(region)), cv2.COLOR_BGRA2GRAY)
        frame = reader.latest()
        if frame is None:
            return None
        h, w = frame.shape[:2]
        x, y = max(0, region['left']), max(0, region['top'])
        crop = frame[y:min(h, y + region['height']), x:min(w, x + region['width'])]
        return cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.size else None
