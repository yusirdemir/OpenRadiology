# OpenRadiology Desktop — tasarım dokümanı

- **Tarih:** 2026-09-06
- **Durum:** onaylandı, uygulama planı bekliyor
- **Kapsam:** Tauri v2 tabanlı, macOS ve Windows üzerinde tek tıkla çalışan masaüstü uygulaması
- **Çekirdek:** bu deponun `openrad` Python paketi (v0.4.0), değiştirilmeden kütüphane olarak kullanılır

---

## 1. Problem ve konumlandırma

`openrad/create_report.py` modül başlığı ürünün ne olduğunu tek cümlede söyler:
*"Evidence ledger: prepare, register, check and finish a review session. **Does not interpret images.**"*

OpenRadiology bir teşhis modeli değil, **deterministik bir görüntüleme tezgâhı ve kanıt kütüğüdür**:

| Yetenek | Modül | Çıktı |
|---|---|---|
| Sadık, kalibre render | `ct_render`, `mr_render`, `pet_render`, `ct_zoom`, `kroma` | PNG kontakt sayfaları + `render_index.json` |
| Kalibre ölçüm | `measure`, `suv` | mm / HU / SUVbw, write-once kanıt dosyası + SHA-256 |
| Sistematik kapsama | `create_report.REGIONS` | CT 18, MR 11, PT 10 anatomik bölge |
| Doğrulama | `create_report.check` | kapsama, referans, hash, karşılaştırma mantığı |
| Rapor | `create_report.finish` | hekim raporu + hasta rehberi, çift dilli (`tr`/`en`) |
| Mahremiyet | `anonymize`, `blind_lock` | PS3.15 basic profile, SHA-256 mühür |

Yorumu yapan taraf motorun **dışındadır**: `skills/openrad/SKILL.md` ile sürülen bir LLM ajanı ya da bir insan.

### 1.1 Masaüstünün asıl kozu: bakışın mekanik olarak kanıtlanması

`SKILL.md` kural #1 şunu itiraf eder:

> *"Marking a page `reviewed: true` without opening it is falsification; `openrad check` cannot detect it, so you must not do it."*

CLI ekranı görmez, dolayısıyla bunu **denetleyemez**. Masaüstü uygulaması ekranı kendisi çizer, dolayısıyla **denetleyebilir.** Bu, ürünün CLI'a göre tek ve gerçek üstünlüğüdür ve tasarımın merkezindedir (bkz. §5, Bakış Tasdiki).

---

## 2. Mimari

### 2.1 Süreç topolojisi

```
┌─ Tauri v2 (Rust) ─────────────────────────────────────────────┐
│  pencere · native dosya diyalogları · sidecar süpervizörü      │
│  · tek-örnek kilidi · updater · köprü el sıkışma dosyası       │
│                                                                │
│  ┌─ WebView (React 19 + TS + Vite + Tailwind v4 + WebGL2) ─┐   │
│  │   HTTP/JSON  +  SSE  +  binary ArrayBuffer              │   │
│  └───────────────┬─────────────────────────────────────────┘   │
└──────────────────┼─────────────────────────────────────────────┘
                   │  127.0.0.1:<OS-atanmış port>  ·  Bearer token
        ┌──────────▼───────────────────────────────────┐
        │  openrad-server  (Python sidecar)            │
        │  VolumeCache(LRU) · measure · create_report  │
        │  · *_render · anonymize · MCP köprü ucu      │
        └──────────────────────────────────────────────┘
```

### 2.2 Mimari kararlar ve gerekçeleri

**K1 — Kalıcı Python sidecar, çağrı başına CLI `spawn` değil.**
Ağır varlık çözülmüş hacimdir (`dcmlib.Volume.vol`, tipik toraks BT'de 400×512×512 int16 ≈ 200 MB). Çağrı başına yeniden decode akıcılığı imkânsız kılar. Sidecar hacmi bir kez çözer, LRU önbellekte tutar.

**K2 — Sayısal çekirdek Rust'a taşınmaz.**
RescaleSlope/Intercept, gantry tilt kontrolü, SUVbw dönüşümü, region growing tek yerde kalır. Rust'ta yeniden yazmak kanıt kütüğünden sessizce sapma riski üretir. Python tek doğruluk kaynağıdır; Rust yalnızca kabuk ve süpervizördür.

**K3 — Tauri IPC yerine localhost HTTP.**
Tauri IPC payload'ı JSON'a serialize eder; 512 KB'lık ham kesitleri scroll hızında taşıyamaz. `fetch → ArrayBuffer → WebGL texture` yolu doğrudandır. SSE uzun render işleri için bedava ilerleme akışı sağlar.

**K4 — Pencereleme (WW/WL) GPU'da yapılır.**
Kesit ham `int16` olarak bir kez indirilir; fragment shader `(v − (wc − ww/2)) / ww` uygular. WW/WL sürüklemek tek uniform güncellemesidir: sıfır decode, sıfır ağ, 60 fps.

**K5 — Çekirdek pakete dokunulmaz.**
`openrad/server/` yeni bir alt paket olarak eklenir ve `openrad.dcmlib`, `openrad.measure`, `openrad.create_report` modüllerini içeriden çağırır. `session.schema.json` **değiştirilmez**; uygulamaya özgü veri (bakış tasdikleri) work_dir içinde ayrı bir dosyada tutulur, böylece `openrad check` davranışı aynen korunur ve CLI ile masaüstü aynı oturumu paylaşabilir.

**K6 — Uygulama LLM taşımaz; MCP köprüsü açar.**
API anahtarı istenmez, hasta verisi hiçbir koşulda makineden çıkmaz. Kullanıcının mevcut Claude Code / Claude Desktop / Cursor kurulumu köprüye bağlanır (bkz. §7).

### 2.3 Güvenlik modeli

- Sidecar `127.0.0.1` üzerine `port=0` ile bağlanır; açılışta stdout'a tek satır `{"port":…,"token":…}` yazar. Rust bunu okur ve WebView'a enjekte eder.
- Her istek `Authorization: Bearer <token>` ister; `Origin`/`Host` doğrulanır; token her açılışta yenidir (256-bit, `secrets.token_urlsafe`).
- `GET /files/**` yalnızca aktif work_dir ve studies_root altındaki yollara izin verir (gerçek yol çözümlemesiyle path-jail).
- Sidecar dışarıya hiçbir bağlantı açmaz. Ağ erişimi yoktur.
- Köprü el sıkışma dosyası `0600` izinle kullanıcı app-data dizinine yazılır ve uygulama kapanınca silinir.

---

## 3. Sidecar API

`openrad/server/` — stdlib `http.server` üzerine kurulu, ek bağımlılık yok (paket şu an bağımlılık-disiplinli: pydicom, numpy, scipy, Pillow).

| Yöntem ve yol | İş | Yanıt |
|---|---|---|
| `GET /health` | canlılık | `{"version","pid"}` |
| `GET /doctor` | `openrad.doctor` | JSON tanı |
| `POST /studies/scan` | klasör ağacını tara | çalışma kartları (tarih, modaliteler, seri sayısı, uyarılar) |
| `POST /studies/inventory` | `dicom_inventory` | seri tablosu (matris, spacing, kernel, timing) |
| `GET /series/{uid}/meta` | `Volume.geometry_summary()` | shape, z dizisi, spacing, plane, modalite, pencere ön ayarları, tilt bilgisi |
| `GET /series/{uid}/slice/{k}` | `plane=ax\|cor\|sag`, `mip=<mm>` | **ham int16 LE ArrayBuffer**; başlıklarda `X-Sop-Uid`, `X-Z-Mm`, `X-Mm-Per-Px`, `X-Shape` |
| `GET /series/{uid}/atlas` | 64 px filmstrip atlası | tek PNG + JSON indeks |
| `POST /measure` | `measure` sarmalayıcı | değer, birim, yöntem, `evidence_file`, `sha256`, `ref` |
| `POST /session/prepare` | `create_report.prepare` | session yolu |
| `GET/PUT /session` | ledger oku/yaz | `session.json` |
| `POST /session/register` | render edilmiş PNG'leri kaydet | sayfa listesi |
| `POST /session/check` | `create_report.check` | bulgular listesi (tıklanabilir) |
| `POST /session/finish` | `create_report.finish` | rapor + rehber yolları ve hash'leri |
| `POST /render/{ct\|mr\|pet\|zoom\|passport}` | uzun iş başlat | `{"job_id"}` |
| `GET /jobs/{id}/events` | SSE | `progress` / `log` / `done` / `error` |
| `POST /attest` | bakış tasdiki yaz | kabul/ret |
| `POST /anonymize`, `POST /lock` | de-identifikasyon, mühür | yollar ve hash'ler |
| `GET /files/**` | work_dir varlık servisi | PNG / MD |
| `POST /mcp` | köprü JSON-RPC ucu | MCP yanıtı |

**İş modeli:** render/anonymize gibi uzun işler bir thread havuzunda çalışır, `job_id` döner, ilerleme SSE ile akar, iptal edilebilir.

---

## 4. Frontend

**Yığın:** React 19 + TypeScript + Vite + Tailwind v4 + zustand (durum) + TanStack Query (sunucu durumu) + özel WebGL2 renderer. Ağır görüntüleme kütüphanesi (Cornerstone3D) **kullanılmaz**: DICOM ayrıştırma ve kalibrasyon Python'da kalmalı (K2), bize yalnızca ham voxel'i ekrana basan ince bir katman gerekli.

### 4.1 `SliceCanvas` (WebGL2)

- Kesit başına bir `R16I` texture; 3–5 kesitlik ring buffer, texture havuzu geri dönüştürülür (GC baskısı yok).
- Fragment shader: pencereleme + opsiyonel PET renk LUT'u + CT/PET füzyon karışımı tek geçişte.
- Scroll: tekerlek / ok tuşları / scrubber. Aşırı scroll'da bekleyen istekler `AbortController` ile iptal.
- Idle'da ±8 kesit prefetch; öncelik kuyruğu görünür kesite en yakın olanı önce alır.
- Ölçüm ve anotasyon ayrı bir 2D canvas katmanında çizilir (keskin çizgi, DPI-aware).

### 4.2 Ekranlar

**① Çalışma Rafı.** Klasör sürükle-bırak veya native diyalog. Kartlar: tarih, modalite, seri sayısı, `doctor` uyarıları. 2–3 çalışma seçilip "Karşılaştır" ile kronolojik oturum açılır (sıra DICOM `StudyDate`/`StudyTime`'dan gelir, klasör adından asla).

**② Workspace — üç bölge.**

- **Sol · Anatomik Tarama Rayı.** `session.regions` birebir: CT'de 18, MR'da 11, PET'te 10 madde; durum çipleri `pending` / `finding` / `no_finding` / `limited` / `not_covered`. Üstte kapsama yüzdesi. Bir bölgeye tıklamak viewer'ı o bölgenin `refs`/`pages` kaydına götürür.
- **Orta · Viewer.** Pencere ön ayarları (lung/soft/bone/brain/PET), AX/COR/SAG sekmeleri, MIP slab kaydırıcısı, piksel-ızgara zoom modu (`ct_zoom` eşleniği), ölçüm araçları: mesafe, dairesel ROI, 2D/3D region-grow tohumu, extent kutusu.
- **Sağ · Kanıt Paneli.** Odaktaki iddia tek nesne olarak: hekim metni, hasta dilinde açıklama, `confidence`/`priority`, ölçümler (`değer · birim · yöntem · sha256`), ve tıklanabilir adres çipleri — `S8 · #219 · r248 c301` — viewer tam o piksele uçar.

> **Sert ürün kuralı.** Arayüz hiçbir zaman kendi hesapladığı bir sayıyı göstermez. Ekrandaki her mm/HU/SUV, `POST /measure` yanıtından gelen ve kanıt dosyası hash'i taşıyan değerdir. Bu, `SKILL.md` kural #3'ün UI karşılığıdır. Serbest ölçüm aracı sürüklenirken canlı bir "taslak" uzunluk gösterilebilir, ancak bu değer görsel olarak *taslak* işaretlenir ve ledger'a asla yazılamaz.

**③ Zaman Çizelgesi (karşılaştırma).** Senkron viewer'lar **indeksle değil, hasta koordinatıyla** kilitlenir (`Volume.patient_point()`), böylece farklı spacing'li çekimlerde aynı anatomi karşı karşıya gelir. Swipe/blend karşılaştırma. Altta değişim tablosu: her iddianın `timeline`'ı, `new/increased/decreased/stable/resolved/indeterminate/not_comparable` verdikti ve tarihlere göre ölçüm sparkline'ı; lezyon başına waterfall çubuğu. Her verdikt **iki uçtan da** kanıt ister; eksikse UI verdikti yazmaya izin vermez.

**④ Rapor.** Hekim raporu ve hasta rehberi yan yana, canlı önizleme. Yanında `check` paneli: `finish`'i engelleyen her madde (kapsanmamış bölge, tasdiksiz sayfa, hash uyuşmazlığı, tek uçlu karşılaştırma) tıklanabilir bir yapılacak maddesidir. PDF/Markdown dışa aktarım, `blind_lock` ile SHA-256 mühür.

**⑤ Ajan Paneli.** MCP köprüsünün canlı dökümü (bkz. §7).

### 4.3 Dil

Arayüz `tr`/`en`, `openrad/locales/*.json` ile aynı anahtar setinden beslenir; bölge adları ve rapor başlıkları zaten orada. UI dili ile rapor dili ayrı seçilebilir.

---

## 5. Bakış Tasdiki (view attestation) — ayırt edici özellik

**Amaç:** `reviewed: true` bayrağı yalnızca gerçekten çizilmiş piksel karşılığında yazılabilsin.

**Mekanizma.** `SliceCanvas` render döngüsü, görüntülenen her kesit için bir olay üretir:

```
ViewEvent { series_uid, sop_uid, plane, first_paint_ts, visible_ms,
            screen_px_per_image_px, ww, wl, window_focused }
```

Bir kesit **bakılmış** sayılır ancak ve ancak:
1. ekranda görüntülenen kesit olarak kesintisiz ≥ `dwell_ms` (varsayılan 400 ms) kaldıysa, **ve**
2. bu süre boyunca pencere odaklıysa ve belge görünürse (`document.visibilityState === "visible"`), **ve**
3. büyütme ≥ 1 ekran pikseli / görüntü pikseli ise.

Tasdikler `POST /attest` ile sidecar'a gider ve work_dir içinde `attestations.jsonl` dosyasına, kesit SOP UID'si ve zaman damgasıyla eklenir. `session.json` şeması **değişmez**.

**Bağlama kuralı.** Sidecar `pages[].reviewed = true` yazmayı yalnızca şu iki durumda kabul eder:
- sayfanın `sources[].sop_uid` kümesindeki her SOP için tasdik varsa (native kesitleri viewer'da gezerek okuma yolu), **veya**
- sayfa PNG'sinin kendisi viewer'da açılıp dwell koşulunu sağladıysa (kontakt sayfası okuma yolu).

Bu, CLI akışından daha güçlüdür ve `openrad check` ile tam uyumludur: `check` hiç değişmez, uygulama yalnızca ona ulaşan girdiyi dürüstleştirir. Uygulama ek olarak bir "Kanıt Bütünlüğü" paneli gösterir: tasdik kapsamı, tasdiksiz sayfalar, hash uyuşmazlıkları.

**Sınır ve dürüstlük.** Tasdik "kullanıcı baktı" değil, "uygulama gösterdi ve kullanıcı oradaydı" demektir. Bu ayrım UI'da açıkça yazılır; abartılı bir güvence iddia edilmez.

---

## 6. Performans bütçesi

| Ölçüt | Hedef |
|---|---|
| Uygulama açılışı → Çalışma Rafı | < 1.5 s |
| Klasör tarama, 500 dosyalık çalışma | < 3 s (yalnızca header okuma, `read_headers` `stop_before_pixels`) |
| Seri açma (400 kesit BT) → ilk kesit ekranda | < 2.5 s (kademeli: ilk kesit hemen, hacim arka planda) |
| Kesit değiştirme (önbellekte) | < 16 ms |
| WW/WL sürükleme | 60 fps, ağ trafiği yok |
| MPR yeniden hesabı | < 100 ms (hacim RAM'de) |
| Bellek tavanı | yapılandırılabilir, varsayılan 4 GB; LRU tahliyesi |

---

## 7. MCP köprüsü

Uygulama LLM taşımaz. Bunun yerine kullanıcının mevcut ajanı uygulamanın **canlı** durumuna bağlanır.

**Taşıma.** MCP istemcileri stdio konuşur. `openrad-mcp-bridge` adlı ince bir stdio shim ikilisi gönderilir; istemci onu başlatır, shim app-data dizinindeki el sıkışma dosyasını (`{port, token, pid}`, `0600`) okur ve JSON-RPC'yi `POST /mcp`'ye proxy'ler. **Uygulama açık değilse** shim mevcut `openrad.mcp` sunucusuna in-process düşer; hiçbir şey bozulmaz.

**Kazanç.** Köprü aktifken ajan uygulamanın hacim önbelleğini ve açık oturumunu paylaşır: çift decode yok, ve ajanın her adımı UI'da anında görünür.

**`page_view` anlamının yükseltilmesi.** Köprü üzerinden `page_view` çağrıldığında sidecar sayfayı **uygulama penceresinde açar**, gerçek boyama ve dwell koşulunu bekler, ancak ondan sonra görüntüyü döndürür ve tasdiki yazar. Pencere gizli/simge durumundaysa araç, ajana kullanıcıdan pencereyi öne getirmesini istemesini söyleyen bir hata döndürür. Böylece kural #1 ajan için de mekanik hale gelir.

**Onay.** Bir köprü ilk kez bağlandığında uygulama istemci adını göstererek kullanıcıdan izin ister. Token yetenek belirtecidir; uygulama kapanınca dosya silinir.

**Ajan Paneli (⑤).** MCP çağrılarının canlı dökümü — *"S8 akciğer sayfası render edildi → sayfa 12/44 açıldı (dwell 0.6 s) → karaciğer ROI ölçüldü: 47 HU"*. Her satır tıklanınca o görünüm birebir yeniden üretilir. Büyük bir "Ajan bağlı" göstergesi ve tek tıkla bağlantı kesme.

---

## 8. Paketleme ve dağıtım

- **Sidecar:** PyInstaller `--onedir` → `app/src-tauri/binaries/openrad-server-<target-triple>`; Tauri `externalBin`. Gömülü: numpy, scipy, pydicom, Pillow **ve** `pylibjpeg` + `pylibjpeg-libjpeg` + `pylibjpeg-openjpeg` (sıkıştırılmış transfer sözdizimleri kutudan çıkar çıkmaz çalışsın).
- **Köprü shim'i:** aynı PyInstaller derlemesinden ikinci bir giriş noktası (`openrad-mcp-bridge`).
- **macOS:** arm64 ve x86_64 ayrı derleme, codesign + notarize, `.dmg`.
- **Windows:** NSIS kurulumu, imza.
- **CI:** GitHub Actions matrisi (`macos-14`, `macos-13`, `windows-latest`); mevcut `.github/workflows/ci.yml` yanına `desktop.yml`.
- **Yerel önkoşul:** Rust toolchain kurulu değil (`cargo` bulunamadı); ilk adımda kurulur. Node 24.16 ✓, Python 3.9.6 ✓.

---

## 9. Test stratejisi

- **Sunucu (pytest):** `tests/test_pipeline.py::make_study` sentetik DICOM üreticisi yeniden kullanılır. Her uç nokta için: mutlu yol, kimlik doğrulama reddi, path-jail kaçış denemesi, iptal edilen iş.
- **Sözleşme:** `PUT /session` ve `POST /session/*` yanıtları `openrad/schema/session.schema.json`'a karşı yeniden doğrulanır (jsonschema, dev bağımlılığı zaten var).
- **Tasdik mantığı:** dwell/odak/zoom eşiklerinin birim testleri; tasdiksiz `reviewed` yazma denemesinin reddedildiğinin testi.
- **Frontend (vitest):** zustand store, kesit önbelleği, prefetch kuyruğu, pencereleme matematiği.
- **Uçtan uca (Playwright, Tauri webview'ında):** klasör aç → seri listele → gez → ölç → check → finish.
- **Kritik regresyon:** motorun kendi test paketi (`tests/`) her CI koşusunda yeşil kalmalı; sunucu çekirdeği değiştirmez.

---

## 10. Depo yerleşimi

```
openrad/              çekirdek — değiştirilmez
openrad/server/       YENİ · sidecar (HTTP + SSE + iş kuyruğu + tasdik defteri)
openrad/mcp/          mevcut · köprü için genişletilir (stdio shim + HTTP proxy)
app/                  YENİ · Tauri v2 uygulaması
app/src/                    React + TS frontend
app/src/gl/                 WebGL2 renderer
app/src-tauri/              Rust kabuk, sidecar süpervizörü, el sıkışma
docs/superpowers/specs/     tasarım dokümanları
```

---

## 11. Fazlar

| Faz | Teslim | Bitti sayılma ölçütü |
|---|---|---|
| 0 | Rust toolchain, Tauri v2 iskeleti, boş pencere açılıyor | `npm run tauri dev` pencere açıyor |
| 1 | Sidecar çekirdeği: `/health`, `/doctor`, `/studies/scan`, `/series/meta`, `/series/slice`, VolumeCache | pytest yeşil; `curl` ham kesit dönüyor |
| 2 | Tauri süpervizörü: sidecar başlat/öldür, port+token el sıkışması, sağlıklı kapanış | uygulama kapanınca süreç kalmıyor |
| 3 | WebGL viewer: kesit, WW/WL, AX/COR/SAG, MIP, filmstrip, prefetch | 400 kesitlik seride akıcı gezinme, WW/WL 60 fps |
| 4 | Oturum ledger'ı + Anatomik Tarama Rayı + Bakış Tasdiki | tasdiksiz `reviewed` reddediliyor; `check` çalışıyor |
| 5 | Ölçüm araçları + Kanıt Paneli | ekrandaki her sayının bir `sha256`'sı var |
| 6 | Zaman Çizelgesi / karşılaştırma | hasta koordinatıyla senkron kilit; değişim tablosu |
| 7 | Rapor ekranı, dışa aktarım, mühür | `finish` uygulamadan üretiliyor |
| 8 | MCP köprüsü + Ajan Paneli | Claude Code bağlanıp okuyor, adımlar canlı görünüyor |
| 9 | Paketleme, imzalama, CI | macOS `.dmg` ve Windows kurulumu çıkıyor |

---

## 12. Kapsam dışı (YAGNI)

- Uygulama içi LLM / API anahtarı yönetimi — MCP köprüsü bunu gereksiz kılar.
- PACS / DICOMweb ağ bağlantısı — yerel klasör yeterli, ağ yüzeyi mahremiyet riski.
- 3D volume rendering / segmentasyon — motorun kanıt modeli 2D adreslere (SOP + row/col) dayanır.
- Çoklu kullanıcı, sunucu dağıtımı, bulut senkronu.
- `session.schema.json` şema değişikliği — v2 korunur.

---

## 13. Bilinen riskler

| Risk | Azaltma |
|---|---|
| PyInstaller ile scipy/numpy paket boyutu ve import süresi | `--onedir`, gereksiz alt modüllerin hariç tutulması, lazy import; ölçüm hedefi < 2 s soğuk açılış |
| macOS notarization sidecar ikilisini reddetme riski | ikili `Contents/MacOS` altına gömülür, hardened runtime + `--options=runtime`, entitlement gerekmez |
| Büyük hacimlerde bellek | LRU tavanı + kullanıcıya görünür bellek göstergesi + tahliye uyarısı |
| Bakış tasdikinin abartılı yorumlanması | UI'da açık ifade: "uygulama gösterdi", "kullanıcı anladı" değil |
| Çekirdek API'sinin sürüm kayması | sunucu yalnızca kararlı yüzeyleri (`Volume`, `main()` giriş noktaları) kullanır; sözleşme testleri CI'da |
