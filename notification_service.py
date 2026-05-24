import os
import json
import firebase_admin
from firebase_admin import credentials, messaging
# Uygulama başlatıldığında Firebase'i yapılandır
def initialize_firebase():
    # Render'da Environment Variable (Çevre Değişkeni) olarak ayarladığımız JSON verisini okuruz.
    # Böylece kodları GitHub'a atsanız bile kimse özel anahtarınızı göremez.
    creds_json = os.getenv("FIREBASE_CREDENTIALS_JSON")
    
    if creds_json:
        try:
            creds_dict = json.loads(creds_json)
            cred = credentials.Certificate(creds_dict)
            
            # Zaten başlatılmışsa tekrar başlatmayı önlemek için kontrol
            if not firebase_admin._apps:
                firebase_admin.initialize_app(cred)
                print("✅ Firebase Admin SDK başarıyla başlatıldı (Environment Variable üzerinden).")
        except Exception as e:
            print(f"❌ Firebase başlatılamadı: {e}")
    else:
        print("⚠️ Uyarı: FIREBASE_CREDENTIALS_JSON environment variable bulunamadı. Bildirim motoru pasif.")
# Yeni haber eklendiğinde bunu çağıracağız
def toplu_bildirim_gonder(
    baslik: str, 
    icerik: str, 
    cihaz_tokenlari: list, 
    haber_id: int = -1, 
    image_url: str = None, 
    hedef_kategori: str = "Tümü", 
    bildirim_id: int = -1,
    kucuk_resim: str = None,
    buyuk_resim: str = None,
    genis_metin: str = None,
    genisletme_tipi: str = "resim"
):
    try:
        if not firebase_admin._apps:
            print("Firebase pasif olduğu için bildirim atlandı.")
            return 0, 0
            
        if not cihaz_tokenlari:
            print("Kayıtlı cihaz token'ı bulunamadı.")
            return 0, 0
            
        # Notification nesnesine opsiyonel olarak image URL verilebilir
        notif_obj = messaging.Notification(
            title=baslik,
            body=icerik,
            image=kucuk_resim.strip() if kucuk_resim and kucuk_resim.strip() else (image_url if image_url else None)
        )
        success_count = 0
        failure_count = 0
        # Google, Haziran 2024 itibarıyla eski toplu /batch endpoint'ini (Multicast API) tamamen kapattığı için
        # artık modern HTTP v1 uyumlu bireysel send() metodunu döngüyle çağırıyoruz.
        for token in cihaz_tokenlari:
            if not token or not token.strip():
                continue
            try:
                message = messaging.Message(
                    notification=notif_obj,
                    data={
                        "haber_id": str(haber_id),
                        "hedef_kategori": hedef_kategori,
                        "bildirim_id": str(bildirim_id),
                        "click_action": "FLUTTER_NOTIFICATION_CLICK",
                        "kucuk_resim": kucuk_resim.strip() if kucuk_resim and kucuk_resim.strip() else "",
                        "buyuk_resim": buyuk_resim.strip() if buyuk_resim and buyuk_resim.strip() else "",
                        "genis_metin": genis_metin.strip() if genis_metin and genis_metin.strip() else "",
                        "genisletme_tipi": genisletme_tipi if genisletme_tipi else "resim"
                    },
                    token=token.strip()
                )
                messaging.send(message)
                success_count += 1
            except Exception as send_err:
                print(f"❌ Token ({token[:15]}...) için bildirim gönderilemedi: {send_err}")
                failure_count += 1
        
        print(f"📡 Bildirim sonucu: {success_count} başarılı, {failure_count} başarısız.")
        return success_count, failure_count
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ Bildirim gönderme genel hatası: {e}")
        return 0, len(cihaz_tokenlari)
