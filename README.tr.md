# asc-release-kit (Türkçe özet)

App Store Connect'teki sürüm ve mağaza metni işlerini komut satırından yapan,
küçük ve bağımlılıksız bir araç. Ruby ya da fastlane gerekmez; Python 3.10 ve
üstü yeter.

Her komut önce **plan** gösterir. `--apply` eklemeden App Store Connect'e hiçbir
şey yazılmaz; yazılan her şey bir günlüğe kaydedilir ve App Store Connect'ten
**geri okunarak** doğrulanır.

## Neler yapar?

- **`doctor`**: Kurulumu baştan sona, yalnızca okuyarak denetler: ayarlar, anahtar
  dosyasının izinleri, Anahtar Zinciri'nde yarım kalmış anahtar, jeton, Apple'ın
  yanıtı, saat farkı, uygulama, satıcı numarası ve analiz isteği. Sorun çıkan her
  maddenin altına ne yapmanız gerektiğini yazar.
- **`release`**: Sürüm kaydını açar ya da var olanı kullanır, işlenmiş (VALID)
  derlemeyi bağlar, her dilde "Yenilikler" metnini yazar, inceleme bilgilerini ve
  Game Center bağlantısını yayındaki sürümden kopyalar, hepsini okuyup doğrular.
  `--submit` ile incelemeye gönderir; ama göndermeden önce sürümün gönderimin
  içinde gerçekten bulunduğunu kontrol eder. Bütün dillere aynı metin yetiyorsa
  `--whats-new-text` yeterli.
- **`aso pull | validate | apply | storefronts`**: Uygulamanın her dildeki adını,
  alt başlığını, anahtar kelimelerini, tanıtım metnini, açıklamasını ve
  bağlantılarını tek bir JSON dosyasında tutar. `aso pull` bu dosyayı App Store
  Connect'teki güncel metinlerden çıkarır; `validate` Apple'ın sınırlarını
  (anahtar kelimede 100 bayt dahil), yinelenen kelimeleri ve marka/rakip yasak
  listesini (Yönerge 2.3.7) denetler; `apply` yazar ve geri okur.
- **`analytics`, `sales`, `reviews`**: Analiz raporlarını, satış raporlarını ve
  kullanıcı yorumlarını indirir. Satış raporu türleri Apple'ın izin verdiği
  birleşimlere göre önceden denetlenir.
- **`upload-build`**: Derlemeyi API anahtarıyla `xcodebuild` ya da `altool` ile
  yükler; macOS uygulamaları için `notarytool` ile noterlik onayı alır.

## Güvenlik

- Özel anahtar yalnızca tek bir kaynaktan okunur: ortam değişkeni, yalnız sahibinin
  okuyabildiği (0600) bir dosya, macOS Anahtar Zinciri ya da AWS SSM.
- Anahtar ve jeton komut satırına, URL'ye ya da günlüğe yazılmaz. openssl ve Xcode
  araçları anahtarı dosya olarak ister; bunun için yalnızca sizin okuyabileceğiniz
  (0600) geçici bir kopya açılır ve araç işini bitirince silinir. İş iptal edilse
  de (Ctrl-C, SIGTERM) bu kopya temizlenir.
- Jeton yalnızca Apple'ın sunucularına gider. Projedeki ayar dosyası bu adresi,
  imzalayıcıyı ya da openssl yolunu değiştiremez; bunlar yalnızca ortam
  değişkenlerinden okunur.
- İnceleme için verilen iletişim bilgileri günlükte gizlenir. Günlük, raporlar ve
  dışa aktarılan yorumlar yalnızca sizin okuyabileceğiniz dosyalara yazılır.

## Kurulum

Her tıklamayı tek tek anlatan rehber: [docs/setup.md](docs/setup.md) (İngilizce).
Özetle:

1. **Aracı kurun.**

   ```sh
   pipx install "git+https://github.com/deegitech/asc-release-kit@main"
   ```

   `@main` en güncel kodu kurar. Belirli bir sürüme sabitlemek için onun yerine
   [Releases](https://github.com/deegitech/asc-release-kit/releases) sayfasındaki bir
   etiketi yazın (ör. `@v0.1.0`).

2. **API anahtarı oluşturun.** App Store Connect'te **Users and Access >
   Integrations > App Store Connect API** sayfasını açın. Ekip API'yi ilk kez
   kullanacaksa önce Hesap Sahibi (Account Holder) bu sayfada bir kez **Request
   Access**'e tıklamalı. Sonra **Team Keys** sekmesinde **+** düğmesine basın;
   anahtarı yalnızca Admin (ya da Hesap Sahibi) oluşturabilir. Sürüm ve mağaza metni
   için **App Manager** rolü yeter. Satış raporu için **Finance** ya da **Sales**
   rolünde bir ekip anahtarı (team key) gerekir; analiz raporu isteği için **Admin**.
   Menü adları zamanla değişebilir.

3. **`.p8` dosyasını indirin.** Apple bu dosyayı yalnızca **bir kez** indirmenize
   izin verir, kopyasını da saklamaz. Aynı sayfadan iki değeri not edin: satırdaki
   **Key ID** (ör. `ABC123DEFG`) ve tablonun üstündeki **Issuer ID** (ör.
   `00000000-0000-0000-0000-000000000000`).

4. **Anahtarı güvenli bir yere koyun** (yalnızca birini seçin):

   - **Dosya** (en kolayı):

     ```sh
     mkdir -p ~/.config/asc-release-kit && chmod 700 ~/.config/asc-release-kit
     mv ~/Downloads/AuthKey_ABC123DEFG.p8 ~/.config/asc-release-kit/
     chmod 600 ~/.config/asc-release-kit/AuthKey_ABC123DEFG.p8
     export ASC_PRIVATE_KEY_PATH=~/.config/asc-release-kit/AuthKey_ABC123DEFG.p8
     ```

   - **macOS Anahtar Zinciri**: değeri komutun içinde verin.

     ```sh
     cd ~/Downloads
     security add-generic-password -U -s asc-release-kit -a ABC123DEFG \
       -w "$(base64 < AuthKey_ABC123DEFG.p8)"
     security find-generic-password -s asc-release-kit -a ABC123DEFG -w | wc -c   # 300-350 arası olmalı
     export ASC_KEYCHAIN_SERVICE=asc-release-kit ASC_KEYCHAIN_ACCOUNT=ABC123DEFG
     ```

     `-w` seçeneğini boş bırakmayın: macOS bu durumda parolayı sorar ama yalnızca ilk
     128 karakteri saklar, anahtar yarım kalır (Ekim 2026'da gözlemlendi). Uzunluk
     129 ya da daha azsa anahtarı yukarıdaki komutla yeniden kaydedin.
     `find-generic-password` komutunu `| wc -c` olmadan çalıştırmayın; anahtarı ekrana
     basar.

   - **CI**: `.p8` dosyasının tamamını `ASC_PRIVATE_KEY` adlı gizli değişkene koyun:
     `gh secret set ASC_PRIVATE_KEY < AuthKey_ABC123DEFG.p8`

   - **Sunucu**: AWS SSM'de `SecureString` olarak saklayın ve adını
     `ASC_SSM_PARAMETER` ile verin.

5. **Key ID ve Issuer ID'yi tanımlayın.**

   ```sh
   export ASC_KEY_ID=ABC123DEFG
   export ASC_ISSUER_ID=00000000-0000-0000-0000-000000000000
   ```

6. **Kontrol edin.** `asc-release-kit doctor` her adımı sırayla ve yalnızca okuyarak
   denetler; sorun çıkan her maddenin (✗) altına ne yapmanız gerektiğini yazar.
   Hiçbir şeyi değiştirmez, hiçbir gizli bilgiyi ekrana basmaz.

7. **İlk deneme.** Uygulamanın Apple ID'sini bulun, ardından bir sürüm planı
   çıkarın. `--apply` eklemediğiniz sürece App Store Connect'e hiçbir şey yazılmaz.

   ```sh
   asc-release-kit apps list          # ilk sütun: Apple ID
   export ASC_APP_ID=1234567890
   asc-release-kit release --version 1.2.0 --build 42 --whats-new-text "Hata düzeltmeleri."
   ```

8. **İsteğe bağlı:**
   - Satış raporları için satıcı numarası **Payments and Financial Reports**
     sayfasının sol üstünde yazar ("Vendor #"): `export ASC_VENDOR_NUMBER=87654321`
   - Analiz raporları için uygulama başına bir kez, Admin rolündeki bir anahtarla
     `asc-release-kit analytics request --apply` çalıştırmak yeter. Veri ertesi gün
     başlar, ilk dosyalar 24-48 saat sonra gelir. Geçmişe dönük veri gelmez; geçmiş
     için `--access-type ONE_TIME_SNAPSHOT` kullanın.

API anahtarlarının süresi dolmaz, iptal edilene kadar geçerlidir. Araç her
çalıştığında 15 dakikalık yeni bir jeton üretir; sizin yenilemeniz gereken bir şey
yoktur.

Bir hata mı aldınız? Önce `asc-release-kit doctor` çalıştırın. Hata mesajları ve App
Store Connect hata kodları, çözümleriyle birlikte
[docs/troubleshooting.md](docs/troubleshooting.md) dosyasında (İngilizce).

Ayrıntılı kullanım, yapılandırma ve güvenlik modeli için İngilizce
[README](README.md) dosyasına bakın.

## Lisans

MIT © 2026 DEEGITECH Teknoloji ve Yazılım Ltd. Şti.
