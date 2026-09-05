# OpenRadiology rapor ve kanıt spesifikasyonu (Türkçe)

Bir olağan çalıştırma, çıktı dizininde (varsayılan `reports/`; `paths.output_dir` ayarı veya
`openrad finish --output-dir` ile değiştirilebilir) iki Markdown belge üretir: `<kök>__rapor.md` ve `<kök>__aciklama.md`.
Envanter, PNG sayfaları, render indeksi, oturum JSON'u ve ölçüm logları yok sayılan çalışma
önbelleğinde kalır (`.cache/create-report/run-*/`). Nihai belgeler yalnızca oturum defterinden
üretilir; tarihli kaynak kayıtlara asla yorum yazılmaz.

## 1. Profesyonel rapor yapısı (RSNA/ESR yapılandırılmış raporlama sırası)

`openrad finish` aşağıdaki bölümleri bu sırayla yazar. İnceleyici oturumu doldurur; üretici metin
uydurmaz.

| Bölüm | Oturum kaynağı | İçerik kuralları |
|---|---|---|
| Başlık uyarısı | sabit | "AI ön inceleme raporu — hekim onaylı resmi rapor değildir"; inceleyen, körlük durumu, bağlam beyanı |
| Tetkikler ve klinik bilgi | `studies[]`, `clinical_context` | DICOM başlıklarından çekim tarihleri, modaliteler, klasör; varsa klinik soru |
| Teknik ve karşılaştırma | `*:technique*` bölgeleri, `comparison.reason` | Cihaz, kernel, kesit kalınlığı/aralığı, kontrast, kapsam, SUV geçerliliği (PET), protokol yeterliliği (MR); karşılaştırma yöntemi veya "önceki tetkik karşılaştırılmadı" |
| Bulgular | `studies[].regions` | Kontrol listesi sırasıyla her anatomik bölge için bir satır; kapsamlı negatifler ("Kapsanan kesitlerde, bu ön incelemede … saptanmadı"); bulgu/kısıtlı için **kalın** |
| Sonuç | `claims[]` | Numaralı, en önemli önce; yöntemiyle ölçüm satırları; her bulgu için karşılaştırma durumu |
| Zaman içindeki değişim | `claims[].timeline` | Yalnız karşılaştırma modunda; her tetkik tarihi için bir satır |
| Öneriler | `recommendations[]` | İsteğe bağlı; kılavuz temelli, asla tedavi takvimi değil |
| Kısıtlar | `limitations[]`, kimlik notu | Kapsam, duyarlılık, teknik ve bağlamsal sınırlar |
| Görüntü kaynakları | `claims[].refs` | Her bulgu için Study/Series/SOP UID ve satır/sütun; girdi SHA-256 parmak izi |

### Terminoloji
RadLex uyumlu terimler kullanılır (radlex.org): "nodül" (< 30 mm), "kitle" (≥ 30 mm), "buzlu cam
dansitesi", "konsolidasyon", "lenf nodu kısa aks", "kontrast tutulumu". Taraf, lob/segment veya IASLC
istasyonu ve ölçüm düzlemi belirtilir. İçeriksiz kaçamak ifadelerden ("dışlanamaz") kaçınılır; kullanılırsa
neyin çözeceği yazılır.

## 2. Oturum düzenlemeleri

Her bölge için `text` (profesyonel) ve `explanation` (sade Türkçe) gerekir: bulgunun, normal görünümün
veya kısıtın eksiksiz anlatımı; terimler açıklanır, yeni iddia eklenmez. Her `studies[].regions` girdisi
`status` (`finding`, `no_finding`, `limited`, `not_covered`) taşır. `finding`/`no_finding` ayrıca `refs`
(gerçek DICOM Study/Series/SOP UID'leri) ve `pages` (gerçekten incelenmiş kayıtlı PNG'lerin mutlak
yolları) gerektirir. `limited`/`not_covered` için açık neden yazılır ve `limitations` içinde de yer alır.
Genel "normal" ifadeleri yasaktır.

Her bulgu (`claim`) için `priority` (`important`/`routine`/`incidental`), `confidence`
(`low`/`moderate`/`high`) ve `patient: {meaning, importance, uncertainty, discuss_with_doctor}` gerekir.
Karşılaştırmalarda `timeline`, verilen her StudyUID için bir girdi içerir:
`{study_uid, status, text, explanation}`; status `present`/`not_seen`/`not_covered`/`indeterminate`;
`present`/`not_seen` o zaman noktasında `refs` de gerektirir. Tarih bazlı kaynaklar bulguda gösterilir;
ara zaman noktası komşularından çıkarsanmaz. `patient_context` ve `patient_limitations` doldurulur.
İsteğe bağlı `glossary` `{term, explanation}` listesi, `recommendations` metin listesidir. Sayısal
değerler her iki belgede aynı kayıttan üretilir.

### Kanıt şeması (örnek, **gerçek hasta verisi değildir**)

```json
{
  "id": "L1",
  "text": "Sağ alt lob posterior bazal segmentte, subplevral yerleşimli, düzgün sınırlı 6 mm solid nodül; kalsifikasyon veya yağ içermiyor.",
  "confidence": "high",
  "priority": "important",
  "refs": [{"study_uid": "GERÇEK", "series_uid": "GERÇEK", "sop_uid": "GERÇEK", "row": 245, "col": 312}],
  "pages": ["İNCELENEN_PNG_MUTLAK_YOLU"],
  "patient": {
    "meaning": "Nodül, akciğerde görülen küçük yuvarlak bir lekedir; buradaki bir bezelye tanesi büyüklüğündedir.",
    "importance": "Bu boyuttaki solid nodüller çoğunlukla iyi huyludur; ancak kanser öyküsü olan bir kişide yeni bir nodül yakından izlenir.",
    "uncertainty": "Görüntü nodülün neden oluştuğunu söyleyemez; bunu ancak zaman içindeki değişim veya doku örneği gösterir.",
    "discuss_with_doctor": "Birkaç ay sonra kontrol BT mi yoksa PET/BT mi uygun olur, sorun."
  },
  "measurements": [{
    "value": 6, "unit": "mm",
    "method": "En büyük göründüğü 1 mm akciğer penceresi kesitinde uzun aks (7 mm) ve dik kısa aks (5 mm) ortalaması; native PixelSpacing Öklid mesafesi",
    "evidence_file": "LOG_MUTLAK_YOLU", "sha256": "GERÇEK_SHA256",
    "ref": {"study_uid": "GERÇEK", "series_uid": "GERÇEK", "sop_uid": "GERÇEK", "row": 245, "col": 312}
  }],
  "comparison": {"status": "new", "from_study_uid": "ÖNCEKİ_STUDY_UID", "to_study_uid": "GÜNCEL_STUDY_UID",
                 "reason": "Önceki ince kesitli seride aynı konumda izlenmedi."}
}
```

Birden fazla tetkikte `comparison` ve her tetkik için `timeline` girdisi zorunludur. İzinli durumlar:
`new`, `increased`, `decreased`, `stable`, `resolved`, `indeterminate`, `not_comparable`. Değişim
iddiaları `from_study_uid` ve `to_study_uid` bildirir ve iki uç noktanın kaynak kanıtını gerektirir
(`new` için eski konum, `resolved` için güncel konum dahil). Her ölçüm gerçek araç çıktısı
(`openrad measure --output`), yöntem, birim, kaynak tarihi ve koordinat gerektirir. HU kalibre BT,
SUVbw vücut ağırlığına göre PET, MR sinyali HU değildir. Araç ondalıkları ölçüm doğruluğunu
kanıtlamaz. 2D komşuluk ortalaması SUVpeak/SULpeak yerine geçmez.

Okunan BT/MR/PET serilerinin `required_passes` alanı tam olarak `SKILL.md`'deki gibi yazılır (örn.
`["lung:native", "lung:mip", "soft:native", "bone:native"]`, `["mr:native"]`, `["pet:native", "pet:fused"]`).
Render aracı kapsanan SOP'ları kaydeder; her zorunlu geçişteki tüm kaynak kesitler incelenmiş
sayfalarda olmalıdır. Yardımcı MPR/zoom sayfa girdileri geçerli study/series/purpose taşır; kapsam
asla uydurulmaz. `geometry_checked`, yükleyicinin o seride başarılı olduğunu gösterir.

## 3. Hasta ve aile rehberi (`__aciklama.md`)

Rehber aynı defterden üretilir ve şu koşulların tümünü sağlar:
1. **Önce doğruluk.** Her tarih, taraf, boyut ve olumsuzlama profesyonel raporla birebir örtüşür; yeni
   iddia yok, yumuşatılmış bulgu yok.
2. **Sakin ve somut dil.** Kısa cümleler, günlük sözcükler, her cümlede tek fikir. Milimetre değerinden
   sonra tanıdık nesnelerle karşılaştırma yapılır (pirinç tanesi ≈ 5 mm, bezelye ≈ 8 mm, üzüm ≈ 15 mm).
3. **Önce terim, sonra bulgu.** "Nodül … demektir. Bu çekimde 6 mm'lik bir nodül görüldü …"
4. **Kesin ile belirsiz ayrılır.** Görüntünün ne gösterdiği, neyi gösteremediği ve soruyu neyin çözeceği
   (kontrol aralığı, biyopsi, başka bir yöntem) yazılır.
5. **Söz vermeden rahatlatma.** "Bu boyuttaki nodüllerin çoğu zararsızdır" kabul edilir; "bu hiçbir şey
   değil" kabul edilmez. Prognoz veya tedavi belirtilmez.
6. **Hekime sorular.** Her bulgu, tek ve yanıtlanabilir bir soru katar; rehber ayrıca randevuya
   götürülecek birleşik bir soru listesi basar.
7. **Her bölge açıklanır**, normal olanlar dahil; sessizlik eksiklik sanılmasın.
8. **Kapanış cümlesi**: bir bulgunun görülmemesi hastalığı dışlamaz; nihai değerlendirme görüntüleri
   inceleyen hekime ve resmi rapora aittir.

## 4. Son kontrol

Üretilen raporu okuyun: çekim tarihleri/sırası, taraf, kaynak kimlikleri, birimler, güven düzeyi ve
negatifler doğru mu? Bir bölümde sıvı, diğerinde "efüzyon yok" gibi çelişkiler giderilir. Resmi rapora
atıf, yalnızca yetkili kör olmayan/denetim görevinde gerçekten okunduysa yapılır. Yalnız görüntüden
çıkarılmış tedavi takvimi veya patolojik evre yazılmaz. Yapısal doğrulama her anlamsal hatayı
yakalayamaz; emin değilseniz görüntülere yeniden bakın.

Denetim anlık görüntüleri ve bulgular çalışma önbelleğinde kalır; klinik altın standart veya aynı olgu
için yeni bir kör testin temiz girdisi değildir.
