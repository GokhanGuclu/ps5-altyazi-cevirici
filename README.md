# 🎮 PS5 Altyazı Çevirici

**Oyunlardaki İngilizce altyazıları anlık olarak Türkçeye çeviren, tamamen bilgisayarınızda çalışan ücretsiz bir araç.**

Türkçe dil desteği olmayan bir oyunu oynarken altyazıları anlamakta zorlanıyorsanız bu araç tam size göre. Ekranın altyazı çıkan kısmını okur, Türkçeye çevirir ve çeviriyi OBS üzerinden oyun görüntüsünün üstüne yazar. İnternet bağlantısı, hesap ya da ücretli bir çeviri servisi gerekmez; her şey sizin bilgisayarınızda, sizin ekran kartınızda çalışır.

<p align="center">
  <img src="docs/arayuz.png" alt="PS5 Altyazı Çevirici arayüzü" width="440">
</p>

> ⚠️ **Kişisel kullanım içindir.** Çeviri modeli (Meta NLLB-200) yalnızca ticari olmayan kullanıma izin veren CC-BY-NC-4.0 lisansına sahiptir. Ayrıntılar için [Lisans](#-lisans) bölümüne bakın.

---

## 📑 İçindekiler

- [Nasıl çalışır?](#-nasıl-çalışır)
- [Neler gerekiyor?](#-neler-gerekiyor)
- [Kurulum (adım adım)](#-kurulum-adım-adım)
- [Kullanım](#-kullanım)
- [OBS ayarı](#-obs-ayarı)
- [İlk test: kurulumunuzu deneyin](#-i̇lk-test-kurulumunuzu-deneyin)
- [PS5'i bağlama (HDMI yakalama kartı)](#-ps5i-bağlama-hdmi-yakalama-kartı)
- [Ayarlar](#️-ayarlar)
- [Sorun giderme](#-sorun-giderme)
- [Sık sorulan sorular](#-sık-sorulan-sorular)
- [Proje yapısı](#-proje-yapısı)
- [Lisans](#-lisans)

---

## 🔍 Nasıl çalışır?

```
 Oyun görüntüsü          Ekranın altyazı           Metin tanıma           Yapay zekâ çevirisi        OBS
 (OBS / video)   ───▶   bölgesi yakalanır   ───▶   (Tesseract OCR)  ───▶  (NLLB-200, GPU)     ───▶  altyazi.txt
                         her 120 ms'de bir          "Stay close, boy."     "Yakın dur, evlat."        ekrana yazar
```

1. **Yakalama:** Seçtiğiniz ekran bölgesinin görüntüsü saniyede yaklaşık 8 kez alınır.
2. **Değişim kontrolü:** Görüntüdeki yazı değişmediyse hiçbir şey yapılmaz. Böylece işlemci ve ekran kartı boşuna yorulmaz. Arkadaki oyun sahnesinin hareket etmesi bu kontrolü tetiklemez; yalnızca yazının kendisi takip edilir.
3. **Metin tanıma (OCR):** Yazı değiştiyse görüntü temizlenir (beyaz yazı arka plandan ayrılır) ve [Tesseract](https://github.com/tesseract-ocr/tesseract) ile metne çevrilir.
4. **Doğrulama:** Harf harf beliren altyazılar yarım çevrilmesin diye metin, görüntü durulana kadar beklenir. OCR'nin küçük okuma hataları (ör. `I'm` yerine `l'm`) yeni bir altyazı sayılmaz; bu sayede ekrandaki çeviri titremez.
5. **Çeviri:** Metin, Meta'nın [NLLB-200](https://huggingface.co/facebook/nllb-200-distilled-600M) modeliyle Türkçeye çevrilir. `Kratos: Stay close.` gibi konuşmacı adı içeren satırlarda yalnızca konuşma çevrilir, isim olduğu gibi kalır.
6. **Gösterim:** Çeviri `altyazi.txt` dosyasına yazılır. OBS bu dosyayı okuyup yazıyı ekranda gösterir. Altyazı ekrandan kaybolunca çeviri de 1,3 saniye içinde silinir.

**Hız:** Bir NVIDIA ekran kartında bir altyazının ekranda belirmesinden çevirinin görünmesine kadar geçen süre genellikle **0,5 – 1 saniyedir**.

---

## 🧰 Neler gerekiyor?

| Gereksinim | Açıklama |
|---|---|
| **Windows 10 / 11** | Uygulama Windows için geliştirildi ve test edildi. |
| **Python 3.10 veya üzeri** | Python 3.13 ile test edildi. |
| **Tesseract OCR** | Ekrandaki yazıyı okuyan ücretsiz program. |
| **NVIDIA ekran kartı** *(önerilir)* | Ekran kartı olmadan da çalışır ama çeviri birkaç kat yavaşlar. |
| **~3 GB boş disk alanı** | Çeviri modeli yaklaşık 2,5 GB yer kaplar. |
| **OBS Studio** | Çeviriyi oyun görüntüsünün üstünde göstermek için. |

---

## 📦 Kurulum (adım adım)

Aşağıdaki adımları sırayla izleyin. Komutları **PowerShell** ya da **Komut İstemi**'ne yapıştırabilirsiniz.

### 1. Python'u kurun

[python.org/downloads](https://www.python.org/downloads/) adresinden en güncel Python'u indirin.

> 💡 Kurulumun ilk ekranında **"Add python.exe to PATH"** kutusunu mutlaka işaretleyin.

Kurulumun çalıştığını şu komutla kontrol edebilirsiniz:

```bash
python --version
```

### 2. Tesseract OCR'yi kurun

1. [Tesseract'ın Windows sürümünü](https://github.com/UB-Mannheim/tesseract/wiki) indirin (`tesseract-ocr-w64-setup-...exe`).
2. Kurulumu varsayılan ayarlarla tamamlayın. Program şu klasöre kurulur:
   `C:\Program Files\Tesseract-OCR\`
3. İngilizce dil paketi varsayılan olarak gelir; başka bir şey seçmenize gerek yok.

> Tesseract'ı farklı bir klasöre kurduysanız, yolunu daha sonra [ayarlar](#️-ayarlar) dosyasına yazmanız yeterli.

### 3. Projeyi indirin

Git kullanıyorsanız:

```bash
git clone https://github.com/GokhanGuclu/ps5-altyazi-cevirici.git
```

Git kullanmıyorsanız bu sayfanın üstündeki yeşil **Code** düğmesine basıp **Download ZIP** ile indirin ve klasörü bir yere çıkarın.

Sonra proje klasörüne girin:

```bash
cd ps5-altyazi-cevirici
```

### 4. Python paketlerini kurun

```bash
python -m pip install -r requirements.txt
```

Bu adım birkaç dakika sürebilir.

### 5. Ekran kartı desteğini açın (NVIDIA kullanıyorsanız)

`pip`'in kurduğu PyTorch çoğu zaman yalnızca işlemcide (CPU) çalışan sürümdür. Ekran kartınızı kullanmak için CUDA destekli sürümü kurun:

```bash
python -m pip install torch --index-url https://download.pytorch.org/whl/cu128
```

> Ekran kartınıza ve sürücünüze uygun komutu [pytorch.org/get-started](https://pytorch.org/get-started/locally/) sayfasındaki seçiciden de alabilirsiniz.

Kontrol etmek için şu komutu çalıştırın. Sonuç `True` ise ekran kartı kullanılacak demektir:

```bash
python -c "import torch; print(torch.cuda.is_available())"
```

### 6. Çeviri modelini indirin

Modeli bir kez indirip bilgisayarınızda saklamak en hızlı ve sorunsuz yoldur:

```bash
hf download facebook/nllb-200-distilled-600M --local-dir "C:\AI-Models\nllb-200"
```

> Bu adımı atlarsanız model, uygulama ilk açıldığında otomatik olarak indirilir. Modeli başka bir klasöre indirdiyseniz yolunu [ayarlar](#️-ayarlar) dosyasındaki `model` alanına yazın.

### 7. Uygulamayı başlatın

```bash
python cevirici_gui.py
```

🎉 Kurulum tamamlandı!

---

## 🕹️ Kullanım

Uygulama, oyuna başlamadan önce bir kez ayarlayıp küçülttüğünüz bir kontrol paneli olarak tasarlandı.

1. **Oyun görüntüsünü ekranda açın.** Bu bir OBS önizlemesi, yakalama kartı görüntüsü ya da bir YouTube videosu olabilir.
2. **"Alan Seç"** düğmesine basın. Ekran kararır.
3. Fareyle **altyazının çıktığı bandı** çizin. İpuçları:
   - Soldan sağa, en uzun altyazıyı alacak kadar geniş tutun.
   - Yüksekliği bir-iki satır yazıyı alacak kadar tutun. Gereğinden büyük bir alan OCR'yi yavaşlatır.
   - Vazgeçmek için **ESC**'ye ya da **sağ tık**'a basın.
   - Birden fazla monitörünüz varsa hepsinde seçim yapabilirsiniz.
4. **"▶ Başlat"** düğmesine basın.
   - İlk açılışta model yaklaşık 20 saniyede yüklenir; bu sırada rozette **"Yükleniyor"** yazar.
   - Model hazır olunca rozet **"CUDA · Hazır"** (ya da **"CPU · Hazır"**) olur.
   - Pencere birkaç saniye sonra kendiliğinden görev çubuğuna küçülür.
5. Oynayın! Çeviriler OBS'te görünür.
6. İşiniz bitince pencereyi görev çubuğundan açıp **"■ Durdur"**'a basın ya da pencereyi kapatın.

Seçtiğiniz alan kaydedilir; bir sonraki açılışta doğrudan **Başlat**'a basabilirsiniz.

---

## 📺 OBS ayarı

1. OBS'te **Kaynaklar** bölümünde **+** düğmesine basın ve **Metin (GDI+)** seçin.
2. **Dosyadan oku** kutusunu işaretleyin.
3. **Metin dosyası** olarak proje klasöründeki `altyazi.txt` dosyasını seçin.
4. Yazı tipini, boyutunu ve rengini istediğiniz gibi ayarlayın. Okunabilirlik için öneriler:
   - Kalın bir yazı tipi (ör. *Segoe UI Bold*, 40–56 pt)
   - **Anahat** (outline) açık, siyah, 4–6 px
   - Gerekirse yarı saydam bir **arka plan**
5. Metni ekranın altına, orijinal altyazının biraz üstüne yerleştirin.

> 💡 Uygulama dosyayı her seferinde tek hamlede değiştirir; OBS hiçbir zaman yarım yazılmış bir çeviri göstermez.

---

## 🧪 İlk test: kurulumunuzu deneyin

Oyunu bağlamadan önce kurulumunuzun çalıştığını görmek için projeyle birlikte bir test sayfası gelir:

1. `test_altyazi.html` dosyasına çift tıklayıp tarayıcıda açın, **F11** ile tam ekran yapın.
2. Sayfada, oyun sahnesi gibi sürekli değişen bir arka planın üstünde İngilizce altyazılar belirir. Her üçüncü altyazı harf harf yazılır.
3. Uygulamada **Alan Seç** ile altyazı bandını seçin ve **Başlat**'a basın.
4. Pencerede **EN** satırında İngilizce metni, **TR** satırında çevirisini görmelisiniz:

| EN | TR |
|---|---|
| Kratos: Stay close, boy. | Kratos: Yakın dur, evlat. |
| We need to leave before the guards come back. | Muhafızlar dönmeden gitmeliyiz. |
| Atreus: Father, look! There is something in the water. | Atreus: Baba, bak, suda bir şey var. |

YouTube'da altyazılı bir oyun videosu açarak da test edebilirsiniz.

---

## 🎮 PS5'i bağlama (HDMI yakalama kartı)

> 🚧 **Bu bölüm geliştirme aşamasında.** Yakalama kartıyla yapılan testler tamamlandıkça güncellenecek.

Uygulama ekranda görünen her şeyi okuyabildiği için PS5 görüntüsünü bilgisayar ekranına getirmeniz yeterli. Önerilen kurulum:

```
PS5 ──HDMI──▶ Yakalama kartı ──USB──▶ Bilgisayar (OBS: Video Yakalama Aygıtı)
                     │
                     └──HDMI çıkış (passthrough)──▶ TV / Monitör
```

1. **PS5'te HDCP'yi kapatın:** *Ayarlar → Sistem → HDMI → HDCP'yi Etkinleştir* seçeneğini kapatın. Açık kalırsa yakalama kartı siyah ekran gösterir.
2. OBS'te **Video Yakalama Aygıtı** kaynağı ekleyip yakalama kartınızı seçin.
3. OBS önizlemesini ya da **Projektör** penceresini ekranda açın.
4. Uygulamada **Alan Seç** ile oyunun altyazı bölgesini seçin ve **Başlat**'a basın.

> ℹ️ **PS Remote Play** görüntüsü korumalıdır ve ekran yakalama yazılımlarına siyah görünür. Bu yüzden Remote Play ile çalışmaz; yakalama kartı gerekir.

---

## ⚙️ Ayarlar

Uygulama ayarlarını proje klasöründeki `ayarlar.json` dosyasında tutar. Bu dosya ilk alan seçiminde otomatik oluşturulur. Örnek bir dosya için [`ayarlar.example.json`](ayarlar.example.json) dosyasına bakabilirsiniz.

| Ayar | Varsayılan | Ne işe yarar? |
|---|---|---|
| `region` | — | Seçilen altyazı alanı. **Alan Seç** ile otomatik yazılır. |
| `tesseract` | `C:\Program Files\Tesseract-OCR\tesseract.exe` | Tesseract'ın yolu. Bulunamazsa sistem `PATH`'inde aranır. |
| `model` | `C:\AI-Models\nllb-200` | Çeviri modelinin klasörü. Klasör yoksa model HuggingFace'ten indirilir. |
| `text_mode` | `light` | Altyazının rengi: `light` (beyaz/açık yazı — çoğu oyun), `dark` (koyu yazı), `none` (görüntü temizleme kapalı). |
| `threshold` | `170` | Yazıyı arka plandan ayırma eşiği (0–255). |

**`threshold` nasıl ayarlanır?**
- Altyazı **okunmuyor** ya da eksik okunuyorsa → değeri **düşürün** (ör. `140`).
- Arka plandaki parlak nesneler **anlamsız harfler** olarak okunuyorsa → değeri **yükseltin** (ör. `200`).

Ayarı değiştirdikten sonra uygulamayı yeniden başlatın.

---

## 🛠️ Sorun giderme

<details>
<summary><b>"Tesseract bulunamadı" hatası alıyorum</b></summary>

Tesseract kurulu değil ya da farklı bir klasörde. [2. adımdaki](#2-tesseract-ocryi-kurun) gibi kurun veya `ayarlar.json` içindeki `tesseract` alanına `tesseract.exe` dosyasının tam yolunu yazın. Yolda ters eğik çizgileri ikişer yazmayı unutmayın:

```json
"tesseract": "D:\\Programlar\\Tesseract-OCR\\tesseract.exe"
```
</details>

<details>
<summary><b>Rozette "CPU" yazıyor, çeviri yavaş</b></summary>

PyTorch'un ekran kartı destekli sürümü kurulu değil. [5. adımdaki](#5-ekran-kartı-desteğini-açın-nvidia-kullanıyorsanız) komutu çalıştırın. NVIDIA sürücünüzün güncel olduğundan da emin olun.
</details>

<details>
<summary><b>EN satırı boş kalıyor, hiçbir şey okunmuyor</b></summary>

- Seçtiğiniz alanın altyazıyı tamamen kapsadığından emin olun. Alanı yeniden, biraz daha geniş seçin.
- `threshold` değerini düşürün (ör. `140`).
- Oyununuzun altyazıları koyu renkliyse `text_mode` değerini `dark` yapın.
</details>

<details>
<summary><b>EN satırında anlamsız karakterler çıkıyor</b></summary>

Arka plandaki parlak alanlar yazı sanılıyor. `threshold` değerini yükseltin (ör. `200`) ve alanı yalnızca altyazı bandını kapsayacak şekilde daraltın.
</details>

<details>
<summary><b>Model yüklenirken "huggingface-hub" ile ilgili bir hata alıyorum</b></summary>

`hf` komut satırı aracı bazen `huggingface-hub` paketini, `transformers` 4.x ile uyumsuz bir sürüme (1.0 ve üzeri) yükseltir. Uyumlu sürüme dönmek için:

```bash
python -m pip install "huggingface-hub>=0.34,<1.0"
```
</details>

<details>
<summary><b>Seçtiğim alan ile okunan alan farklı görünüyor</b></summary>

Uygulama Windows ekran ölçeklendirmesini (%125, %150...) dikkate alır. Uygulamanın eski bir sürümünü kullandıysanız kayıtlı alan kaymış olabilir; **Alan Seç** ile alanı bir kez yeniden seçin.
</details>

<details>
<summary><b>OCR süresi yüksek (200 ms üzeri)</b></summary>

Seçtiğiniz alan büyük olabilir. Alanı yalnızca altyazı bandını kapsayacak kadar daraltın; yüksekliği 100–150 pikseli geçmesin.
</details>

---

## ❓ Sık sorulan sorular

**İnternet bağlantısı gerekiyor mu?**
Yalnızca kurulum ve modelin ilk indirilmesi için. Sonrasında uygulama tamamen çevrimdışı çalışır; hiçbir metin dışarı gönderilmez.

**Hangi dillerden çeviri yapıyor?**
Şu an yalnızca İngilizceden Türkçeye. NLLB-200 modeli 200 dili desteklediği için ileride başka diller de eklenebilir.

**Çeviri kalitesi nasıl?**
Günlük konuşma dilindeki kısa cümlelerde oldukça iyi. Oyuna özgü terimlerde, isimlerde ve çok uzun cümlelerde hatalar olabilir. Çevrimdışı ve anlık çalışan bir model için iyi bir denge sunar.

**Oyunu yavaşlatır mı?**
Çeviri ekran kartında birkaç yüz milisaniye sürer ve yalnızca altyazı değiştiğinde çalışır. PS5 ile oynarken bilgisayarınızın performansı oyunu etkilemez.

**Mac veya Linux'ta çalışır mı?**
Kod büyük ölçüde platformdan bağımsızdır ama yalnızca Windows'ta test edildi.

---

## 🗂️ Proje yapısı

```
ps5-altyazi-cevirici/
├── cevirici_gui.py        # Arayüz (PySide6): kontrol paneli ve alan seçici
├── motor.py               # Motor: ekran yakalama, OCR, çeviri, altyazi.txt'ye yazma
├── test_altyazi.html      # Kurulumu denemek için test sayfası
├── ayarlar.example.json   # Örnek ayar dosyası
├── requirements.txt       # Python paketleri
├── docs/                  # README görselleri
└── LICENSE
```

Çalışma sırasında oluşan `ayarlar.json` ve `altyazi.txt` dosyaları kişiseldir ve depoya eklenmez.

---

## 🤝 Katkıda bulunma

Hata bildirimleri, öneriler ve katkılar memnuniyetle karşılanır! Bir sorunla karşılaşırsanız [Issues](https://github.com/GokhanGuclu/ps5-altyazi-cevirici/issues) bölümünde bildirebilirsiniz. Bildirirken şunları eklemeniz çok yardımcı olur:

- Uygulama penceresinin ekran görüntüsü (rozet ve OCR / Çeviri süreleriyle birlikte)
- Hangi oyunu ya da videoyu kullandığınız
- `ayarlar.json` içindeki `text_mode` ve `threshold` değerleri

---

## 📄 Lisans

- **Kaynak kod:** [MIT Lisansı](LICENSE). Kodu dilediğiniz gibi kullanabilir, değiştirebilir ve paylaşabilirsiniz.
- **Çeviri modeli:** [facebook/nllb-200-distilled-600M](https://huggingface.co/facebook/nllb-200-distilled-600M), Meta tarafından **CC-BY-NC-4.0** lisansıyla yayımlanmıştır. Yalnızca **ticari olmayan** kullanıma izin verir. Model bu depoda dağıtılmaz; kurulum sırasında doğrudan HuggingFace'ten indirilir.
- **Tesseract OCR:** Apache 2.0 lisanslıdır.

---

<p align="center">
  Türkçe oynamak isteyen oyuncular için ❤️ ile yapıldı.
</p>
