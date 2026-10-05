# Colab_Muzik: alternatif pop atolyesi

Gunde bir uretim denemesi, Turkce alternatif pop, canli grup hissi.
**Butce: 0 USD. Ucretli Lyria 3.5 ve diger ucretli modellere gecis yok.**

## Gercek sinirlar

Lyria RealTime yalnizca enstrumantal muzik uretir. Turkce sozler ayri bir
soz dosyasidir; sesin icinde soylenmez. Bu kurulum Suno benzeri tamamlanmis
vokalli sarki sistemi degildir. Ucretsiz kota, modelin deneysel erisimi ve
kalite elemesi nedeniyle her gun kabul edilen bir kayit garanti edilmez.
Lyria 3.5 API icin ucretsiz katman yoktur; bu projede o model cagrilmaz.

## Gunluk uretim

GitHub Actions her gun Istanbul saatiyle **09:17**'de calisir. GitHub zamanlama
gecikmeleri olabilir. Colab'in veya bilgisayarin acik olmasi gerekmez.
Uretimi elle baslatmak icin Actions > Daily alternative pop studio > Run workflow.
Ayni Istanbul takvim gununde ikinci deneme otomatik olarak engellenir.

1. Iki fikirden secilen ozgun hikaye, guclu nakarat, dogal Turkce soz ve bagimsiz soz editoru.
2. 4/4, 96-112 BPM, sabit tonalite, 68 olculuk yaklasik 2:26-2:50 duzenleme.
3. Akustik gitar, piyano, elektrik bas, gercek davul kiti ve temiz elektrik gitar;
   ayni muzikal yon korunarak yumusak bolum gecisleri. Bunlar modele verilen
   yonlendirmelerdir, kesin nota/stem kontrolu degildir.
4. Iki enstrumantal aday; kirpilma, sessizlik ve sure kontrolu.
5. Iki gecisli loudness mastering, MP3 sonrasinda loudness/true-peak kontrolu.
6. Gemini ile tum sesin elestirel dinleme puani: groove, canli enstruman hissi,
   hook, form ve miks. Ortalama en az 78, her kategori en az 65.
7. En iyi kabul edilen MP3, sozler, plan, istemler ve kalite raporu GitHub'a commit edilir.

`catalog/YYYY-MM-DD/` gunluk arsivdir. Basarisiz/kalitesiz uretimde de rapor
kaydedilir; MP3 adaylar 7 gunluk Actions artifact icinde tutulur. Ham WAV
dosyalari ve onceki gunlerin arsivi tekrar artifact'e yuklenmez; depolama
ucretsiz kotada kalsin diye gunluk sikistirilmis dosyalarla sinirlidir.
`ready_for_human_review` ticari yayin onayi degildir. Otomatik puanlar tahmindir.
`generation_failed` teknik ses uretimi hatasidir; bir muzik kalite puani degildir.
`quality.json` asamayi ve anahtarlar/URL'ler temizlenmis hata ayrintisini kaydeder.
Mastering kotu bir besteyi veya yapay bir performansi duzeltemez.

## Asama Sinirlari

Soz yazimi, editor, ses motoru, teknik kontrol, mastering, dinleme ve arsiv
ayri asamalardir. Editor sunucu hatasi taslagi kaybettirmez. Dinleme servisi
calismazsa teknik olarak dogrulanmis MP3 `preview.mp3` olarak korunur ve
`awaiting_audio_review` isaretlenir; kalite onayi verilmis gibi davranilmaz.
Bu durum tamamlanmis teknik uretimdir, otomatik dinleme onayi degildir; workflow
basarili biter ve MP3 arsivlenir. Gercek dusuk kalite puani yine reddedilir.
`steering_N.json` her bolumun istemini, ayarlari ve alinan ses suresini kaydeder.
Istek sayaci API cagrisindan once kalici yazilir. Arsiv push'undan once rebase,
es zamanli kod guncellemesi yuzunden muzik kaydinin kaybolmasini onler.

Actions > Music engine diagnostic elle calistirilan, gunluk zamanlamadan
bagimsiz teknik testtir. `probe` 15 saniyelik ses baglantisini sinar;
`arranged` mevcut gunluk taslagi tam uzunlukta render eder. `review`, source
alanindaki sayisal test run ID'sinin mevcut MP3'unu degerlendirir; yeni muzik
uretmez. Ayni test kaydi sadece bir kez puanlanabilir; gunluk alti metin/ses
analiz istegi siniri korunur. Kabul edilen kurtarma gunluk katalogda, eski
hata raporlari silinmeden saklanir. `archive_preview`, puanlama servisi
kullanilamiyorsa dogrulanmis tam uzunluktaki kaydi kalite onayi iddia etmeden
gunluk katalogda `preview.mp3` olarak saklar. API istegi yapmaz.
Bunlar otomatik retry dongusu degildir.

## Guvenli Kurulum

Anahtar **faturalandirma baglanmamis ucretsiz bir Google AI Studio projesine**
ait olmalidir. SDK/API anahtardan proje faturalandirmasini dogrulayamaz;
`config.json` kullanicinin bu kosulu dogrulamasini kaydeder, harcama kesici degildir.
Sonradan faturalandirma acilirsa calisma durdurulmalidir.

GitHub Settings > Secrets and variables > Actions bolumunde `GEMINI_API_KEY`
repository secret eklenir. Anahtar kodda, notebook ciktisinda veya repoda tutulmaz.
Workflow fork pull request'lerinden secret kullanmaz. En fazla iki ses adayi,
normalde dort beste/editor/dinleme istegi; kurtarma dahil gunluk ust sinir alti.
Metin/editor/dinleme Gemini 3.5 Flash ucretsiz katmaninda dogrudan SDK
`generate_content` ile calisir. Yalnizca sunucu hatasinda diger izinli ucretsiz
Flash modeline bir kez gecilir; gercek model kullanimi
`api_requests.json` icinde kaydedilir.
Kota/kimlik hatasinda retry, ucretli fallback veya ucretli muzik istegi yoktur.
Repo public oldugu icin kabul edilen muzik ve sozler de public olur.

[GitHub secret ayarlari](https://github.com/MuhammedAkkus/Colab_Muzik/settings/secrets/actions)

## Colab

[Yeni atolye defterini ac](https://colab.research.google.com/github/MuhammedAkkus/Colab_Muzik/blob/main/Studio.ipynb)

Colab ayni motoru kullanir; `GEMINI_API_KEY` Colab Secrets'ten okunur.
Yerel denemeler ZIP olarak indirilir. GitHub'a otomatik kayit buluttaki Actions
tarafindan yapilir; Colab'a ek bir GitHub token veya hesap sifresi gerekmez.
[Ilk deneme defteri](https://colab.research.google.com/github/MuhammedAkkus/Colab_Muzik/blob/main/Colab_Muzik.ipynb)
korunmustur ancak ucretli model hucrelerini sifir butceyle calistirmayin.

## Ozgunluk Ve Haklar

Sanatci taklidi, mevcut sarki sozleri, melodiler ve disaridan sample istemleri
kullanilmaz. Kendi katalogundaki ayni baslik, ortak bes kelimelik soz kalibi
ve tamamen ayni ses hash'i reddedilir. Bunlar dunya repertuvarinda benzerlik
aramasi veya telif temizleme islemi degildir. Baska bir esere benzememe,
munhasir hak sahipligi ve ticari kullanim uygunlugu garanti edilmez.
Yayindan once insan dinlemesi ve hak/benzerlik incelemesi gerekir.
Koda lisans eklemek, uretilen sesin haklarini otomatik belirlemez.

## Gelistirme

Python 3.11+, FFmpeg:

```sh
python -m pip install -r requirements.txt
python studio.py validate
python -m unittest discover -s tests -v
python studio.py run
```

Son komut anahtari ortam degiskeninden okur. `--catalog` ile farkli yerel arsiv
secilebilir. `--date` yalnizca YYYY-MM-DD kabul eder. Gunluk bulut rezervasyonu
API isteginden once commit edilir; runner kesilse de kor tekrar uretim yapilmaz.
Kota/erisim hatasini duzeltmeden rezervasyonu elle silmeyin. Beste henuz
olusmadan sunucu hatasi alinirsa Run workflow > repair_failed ile sadece
bir kurtarma denemesi yapilabilir; onceki istek sayaci korunur.
Taslak olustuktan sonra editor sunucu hatasi alirsa taslak korunur ve
`draft_only_human_editing_required` olarak isaretlenir. Eski basarisiz
calismalarda saklanmis taslagi ses asamasina devam ettirmek icin `finish_draft`
secenegi bir kez kullanilabilir; yeniden soz uretmez ve alti istek sinirini korur.

## Resmi Kaynaklar

- [Lyria RealTime ve enstrumantal siniri](https://ai.google.dev/gemini-api/docs/realtime-music-generation)
- [Guncel model fiyatlandirmasi](https://ai.google.dev/gemini-api/docs/pricing)
- [Yapilandirilmis beste/editor ciktilari](https://ai.google.dev/gemini-api/docs/structured-output)
- [Ses anlama ve dinleme analizi](https://ai.google.dev/gemini-api/docs/audio)
- [Dogrudan Python SDK ve yapilandirilmis yanitlar](https://googleapis.github.io/python-genai/)
- [Gemini kullanim kosullari](https://ai.google.dev/gemini-api/terms)

Model/kota/fiyat bilgileri 2026-10-05'te kontrol edilmistir. Deneysel servis
degisirse sessizce model veya ucretli hizmet degistirilmez.
