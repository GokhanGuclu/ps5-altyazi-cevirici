import ctypes
import os
import sys

# Qt olceklemesi kapali: Qt ve mss ayni fiziksel piksel koordinatlarini kullanir.
# Arayuz boyutlari asagida px() ile ekran olcegine gore elle buyutulur.
os.environ['QT_ENABLE_HIGHDPI_SCALING'] = '0'

from PySide6.QtCore import QObject, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QApplication, QFrame, QGraphicsDropShadowEffect, QHBoxLayout,
                               QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget)

import motor

SCALE = 1.0
STATES = {  # durum: (nokta rengi, rozet metni)
    'idle': ('#4a5578', 'Beklemede'),
    'loading': ('#ffb020', 'Yükleniyor'),
    'ready': ('#2f7bff', 'Hazır'),
    'stopped': ('#4a5578', 'Durduruldu'),
    'error': ('#ff5470', 'Hata'),
}
MINIMIZE_AFTER_MS = 2500


def px(value):
    return int(round(value * SCALE))


def stylesheet():
    return f"""
    QWidget#root {{ background: #0b0f1a; }}
    QFrame#card {{ background: #121a2b; border: 1px solid #1f2b45; border-radius: {px(14)}px; }}
    QLabel {{ color: #e6ecff; background: transparent; }}
    QLabel#title {{ font-size: 13pt; font-weight: 600; }}
    QLabel#badge {{ background: #16314f; color: #7fb2ff; border-radius: {px(10)}px;
                    padding: {px(3)}px {px(10)}px; font-size: 8.5pt; }}
    QLabel#region {{ background: #0b1220; border: 1px dashed #2f7bff; border-radius: {px(10)}px;
                     color: #8e9ac0; padding: {px(12)}px; font-size: 9.5pt; }}
    QLabel#status {{ color: #7f8bb0; font-size: 9pt; }}
    QLabel#small {{ color: #7f8bb0; font-size: 8.5pt; }}
    QLabel#english {{ color: #9aa6c8; font-size: 9.5pt; }}
    QLabel#turkish {{ color: #ffffff; font-size: 13pt; font-weight: 500; }}
    QFrame#divider {{ background: #1f2b45; max-height: 1px; border: none; }}
    QPushButton {{ color: #e6ecff; border: none; border-radius: {px(10)}px;
                   padding: {px(11)}px {px(14)}px; font-size: 10pt; }}
    QPushButton#select {{ background: #1b2740; }}
    QPushButton#select:hover {{ background: #24345a; }}
    QPushButton#start {{ font-weight: 700;
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2f7bff, stop:1 #6a5cff); }}
    QPushButton#start:hover {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #4a8dff, stop:1 #7d70ff); }}
    QPushButton#start[running="true"] {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #e0445f, stop:1 #ff7a59); }}
    QPushButton:disabled {{ background: #1b2233; color: #56607e; }}
    """


def app_icon():
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.Antialiasing)
    gradient = QLinearGradient(0, 0, 64, 64)
    gradient.setColorAt(0, QColor('#2f7bff'))
    gradient.setColorAt(1, QColor('#6a5cff'))
    p.setBrush(gradient)
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(2, 2, 60, 60, 14, 14)
    p.setPen(QColor('white'))
    p.setFont(QFont('Segoe UI', 20, QFont.Bold))
    p.drawText(pixmap.rect(), Qt.AlignCenter, 'TR')
    p.end()
    return QIcon(pixmap)


def dark_title_bar(widget):
    if sys.platform != 'win32':
        return
    try:  # Windows 11 / 10 20H1+: koyu pencere basligi
        value = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(int(widget.winId()), 20, ctypes.byref(value),
                                                   ctypes.sizeof(value))
    except Exception:
        pass


def describe_region(region):
    if not region:
        return 'Alan seçilmedi — önce “Alan Seç” ile altyazı bölgesini çizin'
    size = f"{region['width']}×{region['height']}"
    try:
        virtual, primary = motor.monitors()
    except Exception:
        return f'Alan: {size}'
    cx = region['left'] + region['width'] / 2
    cy = region['top'] + region['height'] / 2
    inside = primary['left'] <= cx < primary['left'] + primary['width'] and \
        primary['top'] <= cy < primary['top'] + primary['height']
    if inside:
        where = 'Ana ekran'
    elif cx < primary['left']:
        where = 'Sol ekran'
    elif cx >= primary['left'] + primary['width']:
        where = 'Sağ ekran'
    else:
        where = 'Üst ekran' if cy < primary['top'] else 'Alt ekran'
    return f'Alan: {size}  ·  {where}'


class Bridge(QObject):
    # Motorun is parcaciklarindan gelen guncellemeleri Qt ana dongusune tasir.
    update = Signal(dict)


class Selector(QWidget):
    selected = Signal(object)

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setCursor(Qt.CrossCursor)
        virtual, primary = motor.monitors()  # tum monitorleri kapsayan sanal ekran
        self.origin = QPoint(virtual['left'], virtual['top'])
        self.setGeometry(virtual['left'], virtual['top'], virtual['width'], virtual['height'])
        self.hint_at = QPoint(primary['left'] - virtual['left'] + px(40),
                              primary['top'] - virtual['top'] + px(40))
        self.start = self.end = None
        self.done = False

    def show_and_focus(self):
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()

    def selection(self):
        return QRect(self.start, self.end).normalized() if self.start and self.end else None

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(5, 10, 20, 150))
        rect = self.selection()
        if rect:
            # Neredeyse saydam (tam saydam olursa tiklamalar alttaki pencereye gecer)
            p.setCompositionMode(QPainter.CompositionMode_Source)
            p.fillRect(rect, QColor(0, 0, 0, 1))
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)
            p.setPen(QPen(QColor('#2f7bff'), px(3)))
            p.drawRect(rect)
            p.setFont(QFont('Segoe UI', 10, QFont.Bold))
            p.setPen(QColor('#e6ecff'))
            p.drawText(rect.left(), rect.top() - px(8), f'{rect.width()} × {rect.height()}')
        p.setFont(QFont('Segoe UI', 15, QFont.Bold))
        hint = 'Altyazı alanını fareyle çizin'
        sub = 'ESC veya sağ tık: İptal'
        box = QRect(self.hint_at, QPoint(self.hint_at.x() + px(470), self.hint_at.y() + px(78)))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(18, 26, 43, 230))
        p.drawRoundedRect(box, px(12), px(12))
        p.setPen(QColor('#ffffff'))
        p.drawText(box.adjusted(px(18), px(10), 0, 0), Qt.AlignLeft | Qt.AlignTop, hint)
        p.setFont(QFont('Segoe UI', 10))
        p.setPen(QColor('#8e9ac0'))
        p.drawText(box.adjusted(px(18), 0, 0, -px(12)), Qt.AlignLeft | Qt.AlignBottom, sub)

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            self.finish(None)
            return
        self.start = self.end = event.position().toPoint()
        self.update()

    def mouseMoveEvent(self, event):
        if self.start:
            self.end = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton or not self.start:
            return
        self.end = event.position().toPoint()
        rect = self.selection()
        if rect.width() >= 50 and rect.height() >= 20:
            self.finish({'left': rect.left() + self.origin.x(), 'top': rect.top() + self.origin.y(),
                         'width': rect.width(), 'height': rect.height()})
        else:
            self.finish(None)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.finish(None)

    def finish(self, region):
        if self.done:
            return
        self.done = True
        self.close()
        self.selected.emit(region)


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.config = motor.load_config()
        self.bridge = Bridge()
        self.bridge.update.connect(self.apply_update)
        self.engine = motor.Engine(self.config, self.bridge.update.emit)
        self.state = 'idle'
        self.device = None
        self.ocr_ms = self.tr_ms = None
        self.minimize_when_ready = False
        self.selector = None

        self.setObjectName('root')
        self.setWindowTitle('PS5 Altyazı Çevirici')
        self.setWindowIcon(app_icon())
        self.setStyleSheet(stylesheet())
        self.setFixedWidth(px(440))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(px(16), px(16), px(16), px(16))
        card = QFrame(objectName='card')
        outer.addWidget(card)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(px(20), px(18), px(20), px(18))
        layout.setSpacing(px(12))

        header = QHBoxLayout()
        header.setSpacing(px(10))
        self.dot = QLabel()
        self.dot.setFixedSize(px(10), px(10))
        self.glow = QGraphicsDropShadowEffect(blurRadius=px(14), offset=QPoint(0, 0))
        self.dot.setGraphicsEffect(self.glow)
        title = QLabel('Altyazı Çevirici', objectName='title')
        self.badge = QLabel(objectName='badge')
        header.addWidget(self.dot)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self.badge, alignment=Qt.AlignVCenter)
        layout.addLayout(header)

        self.region_label = QLabel(objectName='region', alignment=Qt.AlignCenter, wordWrap=True)
        layout.addWidget(self.region_label)

        buttons = QHBoxLayout()
        buttons.setSpacing(px(10))
        self.select_button = QPushButton('Alan Seç', objectName='select', cursor=Qt.PointingHandCursor)
        self.start_button = QPushButton(objectName='start', cursor=Qt.PointingHandCursor)
        self.select_button.clicked.connect(self.select)
        self.start_button.clicked.connect(self.toggle)
        buttons.addWidget(self.select_button, 1)
        buttons.addWidget(self.start_button, 2)
        layout.addLayout(buttons)

        self.status = QLabel(objectName='status', wordWrap=True)
        layout.addWidget(self.status)

        layout.addWidget(QFrame(objectName='divider'))
        self.english = QLabel('—', objectName='english', wordWrap=True)
        self.turkish = QLabel('—', objectName='turkish', wordWrap=True)
        self.english.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.turkish.setTextInteractionFlags(Qt.TextSelectableByMouse)
        for name, label in (('EN', self.english), ('TR', self.turkish)):
            block = QVBoxLayout()
            block.setSpacing(px(3))
            block.addWidget(QLabel(name, objectName='small'))
            block.addWidget(label)
            layout.addLayout(block)
        layout.addWidget(QFrame(objectName='divider'))
        layout.addWidget(QLabel('OBS: Metin (GDI+) → Dosyadan oku → altyazi.txt', objectName='small'))

        self.region_label.setText(describe_region(self.config['region']))
        self.set_status('Hazır olduğunuzda Başlat’a basın.' if self.config['region']
                        else 'Önce altyazı alanını seçin.')
        self.refresh()
        dark_title_bar(self)

    # --- durum gosterimi ---

    def refresh(self):
        color, text = STATES[self.state]
        self.dot.setStyleSheet(f'background: {color}; border-radius: {px(5)}px;')
        self.glow.setColor(QColor(color))
        self.glow.setEnabled(self.state in ('ready', 'loading', 'error'))
        self.badge.setText(f'{self.device} · {text}' if self.device else text)
        running = self.engine.running()
        self.start_button.setText('■  Durdur' if running else '▶  Başlat')
        self.start_button.setProperty('running', 'true' if running else 'false')
        self.start_button.style().unpolish(self.start_button)
        self.start_button.style().polish(self.start_button)
        self.select_button.setEnabled(not self.engine.busy())

    def set_status(self, text):
        parts = [text]
        if self.ocr_ms is not None:
            parts.append(f'OCR {self.ocr_ms} ms')
        if self.tr_ms is not None:
            parts.append(f'Çeviri {self.tr_ms} ms')
        self.status_text = text
        self.status.setText('  ·  '.join(parts))

    def apply_update(self, values):
        if 'device' in values:
            self.device = values['device']
        if 'ocr_ms' in values:
            self.ocr_ms = values['ocr_ms']
        if 'tr_ms' in values:
            self.tr_ms = values['tr_ms']
        if 'english' in values:
            self.english.setText(values['english'] or '—')
        if 'turkish' in values:
            self.turkish.setText(values['turkish'] or '—')
        self.set_status(values.get('status', self.status_text))
        if 'state' in values:
            self.state = values['state']
            if self.state == 'ready' and self.minimize_when_ready:
                self.minimize_when_ready = False
                QTimer.singleShot(MINIMIZE_AFTER_MS, self.auto_minimize)
            elif self.state in ('stopped', 'error'):
                QTimer.singleShot(400, self.refresh)  # ceviri is parcacigi biraz sonra biter
        self.refresh()

    def auto_minimize(self):
        if self.engine.running():
            self.showMinimized()

    # --- eylemler ---

    def select(self):
        if self.engine.busy():
            return
        self.showMinimized()
        QTimer.singleShot(300, self.open_selector)

    def open_selector(self):
        self.selector = Selector()
        self.selector.selected.connect(self.selected)
        self.selector.show_and_focus()

    def selected(self, region):
        self.showNormal()
        self.activateWindow()
        if not region:
            return
        self.config['region'] = region
        motor.save_config(self.config)
        self.region_label.setText(describe_region(region))
        self.set_status('Alan kaydedildi. Başlatabilirsiniz.')

    def toggle(self):
        if self.engine.running():
            self.engine.stop()
            self.set_status('Durduruluyor...')
            QTimer.singleShot(400, self.refresh)
            return
        error = self.engine.start(self.config['region'])
        if error:
            QMessageBox.warning(self, 'Başlatılamadı', error)
            return
        self.ocr_ms = self.tr_ms = None
        self.english.setText('—')
        self.turkish.setText('—')
        self.minimize_when_ready = True
        if self.engine.translator is not None:  # model zaten yuklu: 'ready' hemen gelecek
            self.state = 'ready'
        self.set_status('Başlatılıyor...')
        self.refresh()

    def closeEvent(self, event):
        self.engine.stop()
        super().closeEvent(event)


def main():
    global SCALE
    app = QApplication(sys.argv)
    SCALE = max(1.0, app.primaryScreen().logicalDotsPerInch() / 96)
    app.setFont(QFont('Segoe UI', 10))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
