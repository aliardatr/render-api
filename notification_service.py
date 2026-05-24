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
def toplu_bildirim_gonder(baslik: str, icerik: str, cihaz_tokenlari: list, haber_id: int = -1, image_url: str = None):
    if not firebase_admin._apps:
        print("Firebase pasif olduğu için bildirim atlandı.")
        return
        
    if not cihaz_tokenlari:
        print("Kayıtlı cihaz token'ı bulunamadı.")
        return
        
    # Notification nesnesine opsiyonel olarak image URL verilebilir
    notif_obj = messaging.Notification(
        title=baslik,
        body=icerik,
        image=image_url if image_url else None
    )
    message = messaging.MulticastMessage(
        notification=notif_obj,
        data={
            "haber_id": str(haber_id),
            "click_action": "FLUTTER_NOTIFICATION_CLICK"
        },
        tokens=cihaz_tokenlari
    )
    
    try:
        response = messaging.send_multicast(message)
        print(f"📡 Bildirim sonucu: {response.success_count} başarılı, {response.failure_count} başarısız.")
    except Exception as e:
        print(f"❌ Bildirim gönderme hatası: {e}")
