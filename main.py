import os
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, Form, Depends, Query, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import create_engine, Column, Integer, String, Text, JSON, Boolean, or_, cast
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from datetime import date, datetime, timedelta
import json
from pydantic import BaseModel
from typing import Optional, List, Any
from notification_service import initialize_firebase, toplu_bildirim_gonder
# ==========================================
# 1. VERİTABANI KURULUMU VE ŞEMALAR
# ==========================================
db_path = "/tmp/haberler.db" if os.getenv("RENDER") else "./haberler.db"
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{db_path}")
connect_args = {"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
engine = create_engine(
    DATABASE_URL, 
    connect_args=connect_args,
    json_serializer=lambda obj: json.dumps(obj, ensure_ascii=False)
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
class HaberDB(Base):
    __tablename__ = "haberler"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, index=True)
    viewCount = Column(Integer, default=0)
    dailyViewCount = Column(Integer, default=0)
    hourlyClicks = Column(JSON, default={})
    pushSummary = Column(String)
    feedSummary = Column(String)
    content = Column(Text)
    headerImage = Column(String)
    contentImages = Column(JSON, default=[])
    trustScore = Column(Integer, default=100)
    categories = Column(JSON)
    sources = Column(JSON)
    date = Column(String)
class KategoriDB(Base):
    __tablename__ = "kategoriler"
    id = Column(Integer, primary_key=True, index=True)
    isim = Column(String, unique=True, index=True)
    aktif_mi = Column(Boolean, default=True)
class KaynakDB(Base):
    __tablename__ = "kaynaklar"
    id = Column(Integer, primary_key=True, index=True)
    isim = Column(String, unique=True)
    logo_url = Column(String, nullable=True)
class KullaniciDB(Base):
    __tablename__ = "kullanicilar"
    id = Column(Integer, primary_key=True, index=True)
    cihaz_id = Column(String, unique=True, index=True)
    kaydedilen_haberler = Column(JSON, default=[])
    ilgi_alanlari = Column(JSON, default=[])
    fcm_token = Column(String, nullable=True)
Base.metadata.create_all(bind=engine)
# ==========================================
# 2. PYDANTIC ŞEMALARI (Android Parse Hatalarını Engeller)
# ==========================================
class HaberSchema(BaseModel):
    id: int
    title: str
    viewCount: int = 0
    dailyViewCount: int = 0
    pushSummary: Optional[str] = None
    feedSummary: Optional[str] = None
    content: Optional[str] = None
    headerImage: Optional[str] = None
    contentImages: Optional[Any] = []
    trustScore: int = 100
    categories: Optional[Any] = []
    sources: Optional[Any] = []
    date: Optional[str] = None
    class Config:
        from_attributes = True
class HaberlerResponse(BaseModel):
    vitrin: List[HaberSchema]
    haberler: List[HaberSchema]
class KategoriResponse(BaseModel):
    kategoriler: List[str]
class TokenRequest(BaseModel):
    cihaz_id: str
    fcm_token: str
class IlgiAlanlariRequest(BaseModel):
    ilgi_alanlari: List[str]
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
def turkce_kucult(metin: str):
    return metin.replace("I", "ı").replace("İ", "i").lower()
def kategori_arama_filtresi(kategori_adi: str):
    """
    SQLite'da JSON kolonunda Türkçe karakter (ü, ı, ş, ç vb.) ararken 
    eski kayıtlardaki unicode (\u00fc vb.) sorunlarını çözer.
    """
    raw_kat = kategori_adi
    encoded_kat = json.dumps(kategori_adi)[1:-1]
    
    # Tırnaksız arama yaparak daha esnek (single/double quote) eşleşme sağlar
    cond1 = cast(HaberDB.categories, String).ilike(f'%{raw_kat}%')
    cond2 = cast(HaberDB.categories, String).ilike(f'%{encoded_kat}%')
    
    # SQLite LIKE non-ASCII case-sensitivity için manuel varyasyonlar
    lower_kat = turkce_kucult(raw_kat)
    upper_kat = raw_kat.replace("ı", "I").replace("i", "İ").upper()
    
    cond3 = cast(HaberDB.categories, String).ilike(f'%{lower_kat}%')
    cond4 = cast(HaberDB.categories, String).ilike(f'%{upper_kat}%')
    
    return or_(cond1, cond2, cond3, cond4)
def hedef_kitle_tokenlari(hedef_kategori: str, db: Session):
    kullanicilar = db.query(KullaniciDB).filter(KullaniciDB.fcm_token.isnot(None)).all()
    if hedef_kategori == "Tümü":
        return [k.fcm_token for k in kullanicilar]
    
    hedef_kucuk = turkce_kucult(hedef_kategori)
    token_list = []
    for k in kullanicilar:
        if k.ilgi_alanlari:
            alanlar_kucuk = [turkce_kucult(a) for a in k.ilgi_alanlari]
            if hedef_kucuk in alanlar_kucuk:
                token_list.append(k.fcm_token)
    return token_list
# ==========================================
# 3. FASTAPI YENİ NESİL YAŞAM DÖNGÜSÜ (LIFESPAN)
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_firebase()
    db = SessionLocal()
    try:
        if db.query(KategoriDB).count() == 0:
            varsayilanlar = [
                "Teknoloji", "Bilim", "Gündem", "Finans", "Tarih", "Siyaset",
                "Yapay Zeka", "Galatasaray", "Fenerbahçe", "Beşiktaş", "Trabzonspor",
                "Resmi Gazete", "Borsa", "Kripto", "Girişim", "Otomobil"
            ]
            for kat_isim in varsayilanlar:
                yeni_kat = KategoriDB(isim=kat_isim)
                db.add(yeni_kat)
            db.commit()
    finally:
        db.close()
    yield
    print("🛑 Sunucu kapatılıyor...")
app = FastAPI(title="haberPortaliAPI", version="2.5.0", lifespan=lifespan)
# ==========================================
# 4. ANDROID RETROFIT İLETİŞİM KAPILARI (GERÇEK SAYFALAMA)
# ==========================================
@app.get("/")
def ana_sayfa():
    return {"mesaj": "Sunucu ve Gelişmiş Veritabanı Aktif!"}
@app.get("/kategoriler", response_model=KategoriResponse)
def kategorileri_getir(db: Session = Depends(get_db)):
    kategoriler = db.query(KategoriDB).filter(KategoriDB.aktif_mi == True).all()
    kategori_isimleri = [k.isim for k in kategoriler]
    return {"kategoriler": kategori_isimleri}
# DİKKAT (HATA 2 DÜZELTİLDİ): /detay/ route'u, genel kategori route'undan önce gelmeli
@app.get("/haberler/detay/{haber_id}", response_model=HaberSchema)
def haber_detayi_getir(haber_id: int, db: Session = Depends(get_db)):
    haber = db.query(HaberDB).filter(HaberDB.id == haber_id).first()
    if haber:
        return haber
    raise HTTPException(status_code=404, detail="Haber bulunamadı")
@app.get("/haberler/filtrele", response_model=HaberlerResponse)
def coklu_kategori_getir(kategoriler: str = Query(""), skip: int = Query(0, ge=0), limit: int = Query(6, ge=1), db: Session = Depends(get_db)):
    if not kategoriler:
        haberler = db.query(HaberDB).order_by(HaberDB.id.desc()).offset(skip).limit(limit).all()
        vitrin = db.query(HaberDB).order_by(HaberDB.dailyViewCount.desc()).limit(10).all() if skip == 0 else []
        return {"vitrin": vitrin, "haberler": haberler}
    istenen_kategoriler = [k.strip() for k in kategoriler.split(",")]
    
    # HATA 6 (ÇÖKME) DÜZELTİLDİ: JSON sütunu filtrelerken Türkçe karakter desteği eklendi
    conditions = [kategori_arama_filtresi(kat) for kat in istenen_kategoriler]
    
    haberler = db.query(HaberDB).filter(or_(*conditions)).order_by(HaberDB.id.desc()).offset(skip).limit(limit).all()
    
    vitrin = []
    if skip == 0:
        vitrin = db.query(HaberDB).filter(or_(*conditions)).order_by(HaberDB.dailyViewCount.desc()).limit(10).all()
        
    return {"vitrin": vitrin, "haberler": haberler}
@app.post("/haberler/{haber_id}/tikla")
def habere_tikla(haber_id: int, db: Session = Depends(get_db)):
    haber = db.query(HaberDB).filter(HaberDB.id == haber_id).first()
    if haber:
        now = datetime.now()
        current_hour_str = now.strftime("%Y-%m-%dT%H")
        clicks = dict(haber.hourlyClicks) if haber.hourlyClicks else {}
        clicks[current_hour_str] = clicks.get(current_hour_str, 0) + 1
        twenty_four_hours_ago = now - timedelta(hours=24)
        cleaned_clicks = {}
        total_24h = 0
        for k, v in clicks.items():
            get_dt = datetime.strptime(k, "%Y-%m-%dT%H")
            if get_dt >= twenty_four_hours_ago:
                cleaned_clicks[k] = v
                total_24h += v
        blueprint_hourly = cleaned_clicks
        haber.hourlyClicks = blueprint_hourly
        haber.dailyViewCount = total_24h
        haber.viewCount += 1
        db.commit()
        return {"mesaj": "Tıklanma artırıldı", "toplam": haber.viewCount, "son_24_saat": haber.dailyViewCount}
    raise HTTPException(status_code=404, detail="Haber bulunamadı")
@app.get("/haberler/{kategori_adi}", response_model=HaberlerResponse)
def kategoriye_gore_haber_getir(kategori_adi: str, skip: int = Query(0, ge=0), limit: int = Query(6, ge=1), db: Session = Depends(get_db)):
    if kategori_adi.lower() == "tümü":
        haberler = db.query(HaberDB).order_by(HaberDB.id.desc()).offset(skip).limit(limit).all()
        vitrin = db.query(HaberDB).order_by(HaberDB.dailyViewCount.desc()).limit(10).all() if skip == 0 else []
        return {"vitrin": vitrin, "haberler": haberler}
        
    # HATA 6 (ÇÖKME) DÜZELTİLDİ: JSON formatındaki sütun doğrudan ilike aranamaz, Türkçe destekli filtremiz kullanıldı.
    filtre = kategori_arama_filtresi(kategori_adi)
    haberler = db.query(HaberDB).filter(filtre).order_by(HaberDB.id.desc()).offset(skip).limit(limit).all()
    
    vitrin = []
    if skip == 0:
        vitrin = db.query(HaberDB).filter(filtre).order_by(HaberDB.dailyViewCount.desc()).limit(10).all()
        
    return {"vitrin": vitrin, "haberler": haberler}
@app.post("/kullanicilar/token-kaydet")
def token_kaydet(request: TokenRequest, db: Session = Depends(get_db)):
    kullanici = db.query(KullaniciDB).filter(KullaniciDB.cihaz_id == request.cihaz_id).first()
    if kullanici:
        kullanici.fcm_token = request.fcm_token
    else:
        yeni_kullanici = KullaniciDB(
            cihaz_id=request.cihaz_id, 
            fcm_token=request.fcm_token,
            kaydedilen_haberler=[],
            ilgi_alanlari=[]
        )
        db.add(yeni_kullanici)
    db.commit()
    return {"mesaj": "FCM Token başarıyla kaydedildi."}
@app.post("/kullanicilar/{cihaz_id}/ilgi-alanlari-kaydet")
def ilgi_alanlari_kaydet(cihaz_id: str, request: IlgiAlanlariRequest, db: Session = Depends(get_db)):
    kullanici = db.query(KullaniciDB).filter(KullaniciDB.cihaz_id == cihaz_id).first()
    if kullanici:
        kullanici.ilgi_alanlari = request.ilgi_alanlari
        db.commit()
        return {"mesaj": "İlgi alanları başarıyla güncellendi."}
    
    # Kullanıcı yoksa oluşturup kaydedelim
    yeni_kullanici = KullaniciDB(
        cihaz_id=cihaz_id,
        fcm_token=None,
        kaydedilen_haberler=[],
        ilgi_alanlari=request.ilgi_alanlari
    )
    db.add(yeni_kullanici)
    db.commit()
    return {"mesaj": "Kullanıcı oluşturuldu ve ilgi alanları eklendi."}
# ==========================================
# 5. GELİŞMİŞ WEB ADMİN PANELİ (PREMIUM UI DESIGN)
# ==========================================
ADMIN_CSS = """
<style>
    body { background-color: #0E1013; color: #E2E8F0; font-family: 'Segoe UI', system-ui, -apple-system, sans-serif; padding: 30px 20px; margin: 0; }
    .container { max-width: 760px; margin: auto; background: #15181F; padding: 35px; border-radius: 16px; box-shadow: 0px 10px 30px rgba(0,0,0,0.6); border: 1px solid #22252E; }
    h2 { color: #64B5F6; text-align: center; margin-top: 0; margin-bottom: 25px; font-size: 26px; font-weight: 700; letter-spacing: -0.5px; }
    h3 { font-size: 18px; font-weight: 600; color: #F8FAFC; }
    label { font-weight: 600; font-size: 14px; color: #94A3B8; margin-top: 16px; display: block; }
    input, textarea, select { width: 100%; padding: 12px 16px; margin: 8px 0 18px 0; background: #1E222B; color: #F8FAFC; border: 1px solid #2D323F; border-radius: 10px; box-sizing: border-box; font-size: 14px; transition: all 0.25s ease; }
    input:focus, textarea:focus, select:focus { outline: none; border-color: #1976D2; box-shadow: 0 0 0 3px rgba(25, 118, 210, 0.2); background: #222732; }
    .btn { padding: 14px 20px; border: none; border-radius: 10px; cursor: pointer; font-weight: 700; font-size: 15px; transition: all 0.2s ease-in-out; text-align: center; text-decoration: none; display: inline-block; width: 100%; box-sizing: border-box; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.2); }
    .btn:hover { transform: translateY(-2px); box-shadow: 0 10px 15px -3px rgba(0,0,0,0.4); }
    .btn-blue { background: #1976D2; color: white; }
    .btn-blue:hover { background: #1565C0; }
    .btn-green { background: #10B981; color: white; }
    .btn-green:hover { background: #059669; }
    .btn-red { background: #EF4444; color: white; }
    .btn-red:hover { background: #DC2626; }
    .btn-gray { background: #334155; color: #F1F5F9; margin-top: 10px; }
    .btn-gray:hover { background: #475569; }
    .btn-small { padding: 8px 14px; font-size: 12px; width: auto; margin: 0; border-radius: 8px; }
    .category-item { display: flex; justify-content: space-between; align-items: center; background: #1E222B; padding: 14px 20px; border-radius: 10px; margin-bottom: 10px; border: 1px solid #2D323F; transition: border-color 0.2s; }
    .category-item:hover { border-color: #475569; }
    .row { display: flex; gap: 16px; }
    .row > div, .row > a, .row > button { flex: 1; }
    .hint { font-size: 12px; color: #64748B; margin-top: -14px; margin-bottom: 16px; display: block; font-style: italic; }
    .news-list { display: flex; flex-direction: column; gap: 12px; margin-top: 20px; }
    .news-item { display: flex; align-items: center; background: #1E222B; padding: 14px; border-radius: 12px; text-decoration: none; color: white; transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1); border: 1px solid #2D323F; }
    .news-item:hover { background: #252A36; border-color: #1976D2; transform: translateX(8px); }
    .news-item img { width: 90px; height: 68px; object-fit: cover; border-radius: 8px; margin-right: 16px; border: 1px solid #334155; }
    .news-item-content { flex: 1; }
    .news-title { font-size: 15px; font-weight: 600; margin-bottom: 6px; color: #F1F5F9; line-height: 1.4; }
    .news-id { font-size: 12px; color: #64B5F6; font-family: monospace; }
    .chip-container { display: flex; flex-wrap: wrap; gap: 8px; margin-top: -10px; margin-bottom: 20px; padding: 4px; }
    .chip-valid { background: rgba(25, 118, 210, 0.15); color: #64B5F6; border: 1px solid rgba(25, 118, 210, 0.4); padding: 6px 14px; border-radius: 20px; font-size: 13px; font-weight: 600; display: inline-block; }
    .chip-invalid { background: rgba(239, 68, 68, 0.1); color: #F87171; border: 1px solid rgba(239, 68, 68, 0.4); padding: 5px 13px; border-radius: 20px; font-size: 13px; font-weight: 600; display: inline-block; }
    .error-msg { color: #F87171; font-size: 13px; margin-top: -12px; margin-bottom: 20px; display: none; font-weight: 600; background: rgba(239, 68, 68, 0.05); padding: 10px; border-radius: 8px; border: 1px solid rgba(239, 68, 68, 0.2); }
    .format-guide-card { background: #1E222B; border: 1px dashed #1976D2; border-radius: 10px; padding: 14px; margin-top: 6px; margin-bottom: 12px; }
    .format-guide-title { font-size: 13px; font-weight: 700; color: #64B5F6; margin-bottom: 8px; display: flex; align-items: center; gap: 6px; }
    .format-guide-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; font-size: 11px; }
    .format-guide-item { background: #15181F; padding: 8px 12px; border-radius: 6px; border: 1px solid #2D323F; display: flex; flex-direction: column; justify-content: flex-start; }
    .format-code { font-family: monospace; color: #10B981; font-weight: 700; background: rgba(16, 185, 129, 0.1); padding: 2px 6px; border-radius: 4px; display: block; margin-bottom: 4px; width: fit-content; }
</style>
"""
FORMAT_GUIDE_HTML = """
<div class="format-guide-card">
    <div class="format-guide-title">📝 Mobil Akıllı Metin & Enjeksiyon Format Kılavuzu</div>
    <div class="format-guide-grid">
        <div class="format-guide-item">
            <span class="format-code">***metin*** veya **metin**</span>
            <span>Satır bazında <b>1.1 kat büyük başlık</b>; cümle içinde ise kelimeleri kurumsal seviyede <b>Kalın (Bold)</b> yapar.</span>
        </div>
        <div class="format-guide-item">
            <span class="format-code">pp2 metin pp2</span>
            <span>Satır bazında <b>3 kat büyük dev başlık</b>; cümle içinde ise kelimeleri <b>Mavi Renkli ve Kalın</b> olarak parlatır.</span>
        </div>
        <div class="format-guide-item">
            <span class="format-code">pp1 metin pp1 veya *metin*</span>
            <span>Satır bazında <i>İtalik başlık</i>; cümle içinde ise kelimeleri <b>Özel 550 Yarı-Kalın İtalik (Eğik)</b> kıvama getirir.</span>
        </div>
        <div class="format-guide-item">
            <span class="format-code">ccc metin ccc</span>
            <span>Seçilen kelime grubunun veya cümlenin altına, kelime kırılması yapmadan kesintisiz kurumsal bir <u>alt çizgi</u> ekler.</span>
        </div>
        <div class="format-guide-item">
            <span class="format-code">*reklam*</span>
            <span>Metin arasına üstten ve alttan dengeli boşluk bırakarak şık bir <b>Native Kare Reklam Kartı</b> yerleştirir (Makale içi Max 3 adet).</span>
        </div>
        <div class="format-guide-item">
            <span class="format-code">*önerileniçerik=id*</span>
            <span>Belirtilen ID'li haberi sistemden çekerek sol şerit mavi hatlı, parlayan etiketli şık bir <b>Önerilen İçerik Kartına</b> dönüştürür.</span>
        </div>
    </div>
</div>
"""
VALIDATION_SCRIPT = """
<script>
    const validCategories = DATABASE_CATEGORIES_PLACEHOLDER; 
    const input = document.getElementById('categoriesInput');
    const preview = document.getElementById('categoriesPreview');
    const errorDiv = document.getElementById('categoryError');
    const form = document.getElementById('newsForm');
    function checkCategories() {
        let value = input.value;
        let parts = value.split(',');
        let hasInvalid = false;
        let previewHTML = '';
        let invalidTags = [];
        parts.forEach((part) => {
            let trimmed = part.trim();
            if (trimmed === '') return;
            let match = validCategories.find(c => c.toLowerCase() === trimmed.toLowerCase());
            if (match) {
                previewHTML += `<span class="chip-valid">${match}</span>`;
            } else {
                hasInvalid = true;
                invalidTags.push(trimmed);
                previewHTML += `<span class="chip-invalid">⚠️ ${trimmed}</span>`;
            }
        });
        preview.innerHTML = previewHTML;
        if (hasInvalid) {
            errorDiv.style.display = 'block';
            errorDiv.innerHTML = "❌ Hata: Şunlar sistemde kayıtlı değil: " + invalidTags.join(', ') + ".";
            return false;
        } else {
            errorDiv.style.display = 'none';
            errorDiv.innerHTML = '';
            return true;
        }
    }
    input.addEventListener('input', checkCategories);
    input.addEventListener('blur', checkCategories);
    input.addEventListener('keydown', function(e) {
        if (e.key === 'Enter') {
            e.preventDefault();
            checkCategories();
        }
    });
    form.addEventListener('submit', function(e) {
        const isValid = checkCategories();
        if (!isValid) {
            e.preventDefault();
            alert("Hatalı veya geçersiz kategoriler var!");
        }
    });
    checkCategories();
</script>
"""
@app.get("/admin", response_class=HTMLResponse)
def admin_ana_sayfa(sort: str = "yeniden-eskiye", db: Session = Depends(get_db)):
    # HATA 5 (RAM Optimizasyonu) DÜZELTİLDİ: 
    # Milyonlarca haber olursa all() server'ı çökertir, sıralamalar SQL tarafına taşındı.
    if sort == "yeniden-eskiye":
        haberler = db.query(HaberDB).order_by(HaberDB.id.desc()).limit(200).all()
    elif sort == "eskiden-yeniye":
        haberler = db.query(HaberDB).order_by(HaberDB.id.asc()).limit(200).all()
    elif sort == "en-cok-tiklanan":
        haberler = db.query(HaberDB).order_by(HaberDB.viewCount.desc()).limit(200).all()
    elif sort == "24-saatte-en-cok-tiklanan":
        haberler = db.query(HaberDB).order_by(HaberDB.dailyViewCount.desc()).limit(200).all()
    elif sort == "en-cok-kullanilan":
        # Kategorilere göre yoğunluk (şimdilik RAM'de)
        tum_haberler = db.query(HaberDB).limit(200).all()
        kategori_frekanslari = {}
        for h in tum_haberler:
            if h.categories:
                for kat in h.categories:
                    kategori_frekanslari[kat.lower()] = kategori_frekanslari.get(kat.lower(), 0) + 1
        # HATA 1 (ÇÖKME) DÜZELTİLDİ: cat yerine kat yazıldı
        def haber_populerlik_skoru(h):
            if not h.categories: return 0
            return max(kategori_frekanslari.get(kat.lower(), 0) for kat in h.categories)
        haberler = sorted(tum_haberler, key=haber_populerlik_skoru, reverse=True)
    else:
        haberler = db.query(HaberDB).order_by(HaberDB.id.desc()).limit(200).all()
    liste_html = ""
    for h in haberler:
        fmt_id = f"{h.id:010d}"
        liste_html += f"""
        <a href="/admin/{fmt_id}" class="news-item">
            <img src="{h.headerImage}" alt="Görsel">
            <div class="news-item-content">
                <div class="news-title">{h.title}</div>
                <div class="news-id">ID: {fmt_id} &nbsp;|&nbsp; Toplam Okunma: {h.viewCount} &nbsp;|&nbsp; Son 24s: <span style="color:#10B981; font-weight:bold;">{h.dailyViewCount}</span></div>
            </div>
        </a>
        """
    html_content = f"""
    <!DOCTYPE html>
    <html lang="tr">
    <head>
        <meta charset="UTF-8">
        <title>Yönetim Paneli</title>
        {ADMIN_CSS}
    </head>
    <body>
        <div class="container">
            <h2>⚙️ Control Center Dashboard</h2>
            <div class="row">
                <a href="/admin/haber-ekleme" class="btn btn-green">➕ YENİ HABER YAYINLA</a>
                <a href="/admin/haber-duzenle-secim" class="btn btn-blue">✏️ HABER DÜZENLE / SİL</a>
                <a href="/admin/kategoriler" class="btn btn-gray" style="margin-top:0;">🏷️ KATEGORİ SİSTEMİ</a>
            </div>
            <div class="row" style="margin-top: 12px;">
                <a href="/admin/ozel-bildirim" class="btn btn-blue" style="background:#8B5CF6;">📢 ÖZEL BİLDİRİM GÖNDER</a>
            </div>
            <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 45px; border-bottom: 2px solid #22252E; padding-bottom: 12px;">
                <h3 style="margin: 0; color: #F1F5F9;">📰 Yayındaki İçerik Akışı</h3>
                <select id="sortSelect" onchange="location.href='/admin?sort='+this.value" style="width: auto; margin: 0; padding: 8px 16px; background: #1E222B; color: #F8FAFC; border: 1px solid #2D323F; border-radius: 8px; cursor: pointer; font-size: 13px; font-weight: 600;">
                    <option value="yeniden-eskiye" {"selected" if sort == "yeniden-eskiye" else ""}>Sıralama: Yeniden Eskiye</option>
                    <option value="eskiden-yeniye" {"selected" if sort == "eskiden-yeniye" else ""}>Sıralama: Eskiden Yeniye</option>
                    <option value="en-cok-kullanilan" {"selected" if sort == "en-cok-kullanilan" else ""}>Kategori Yoğunluğuna Göre</option>
                    <option value="en-cok-tiklanan" {"selected" if sort == "en-cok-tiklanan" else ""}>En Çok Okunanlar (Global)</option>
                    <option value="24-saatte-en-cok-tiklanan" {"selected" if sort == "24-saatte-en-cok-tiklanan" else ""}>En Çok Okunanlar (Son 24s)</option>
                </select>
            </div>
            <div class="news-list">
                {liste_html if liste_html else "<p style='color: #64748B; text-align:center; padding: 20px;'>Henüz sisteme haber kaydı girilmemiş.</p>"}
            </div>
        </div>
    </body>
    </html>
    """
    return html_content
@app.get("/admin/ozel-bildirim", response_class=HTMLResponse)
def admin_ozel_bildirim_sayfasi(db: Session = Depends(get_db)):
    kayitli_kategoriler = [k.isim for k in db.query(KategoriDB).all()]
    options_html = '<option value="Tümü">Tümü (Herkese)</option>'
    for k in kayitli_kategoriler:
        options_html += f'<option value="{k}">{k}</option>'
        
    html_content = f"""
    <!DOCTYPE html>
    <html lang="tr">
    <head>
        <meta charset="UTF-8">
        <title>Özel Bildirim Merkezi</title>
        {ADMIN_CSS}
    </head>
    <body>
        <div class="container">
            <h2>📢 Özel Push Bildirim Merkezi</h2>
            <form action="/admin/ozel-bildirim-gonder" method="post">
                <label>Bildirim Başlığı (Zorunlu):</label>
                <input type="text" name="baslik" required placeholder="Flaş gelişme...">
                
                <label>Açıklama / İçerik:</label>
                <input type="text" name="icerik" placeholder="Detaylı bilgi (opsiyonel)">
                
                <label>Görsel Linki (Sağdaki Küçük İkon):</label>
                <input type="url" name="image_url" placeholder="https://... (opsiyonel)">
                
                <label>Bağlı Olduğu Haber ID:</label>
                <input type="number" name="haber_id" placeholder="Tıklayınca habere gitsin istiyorsanız ID yazın (opsiyonel)">
                
                <label>Kime Gönderilsin? (Hedef Kitle):</label>
                <select name="hedef_kategori">
                    {options_html}
                </select>
                
                <div class="row" style="margin-top: 25px;">
                    <a href="/admin" class="btn btn-gray" style="line-height: normal; display: flex; align-items: center; justify-content: center;">İPTAL ET</a>
                    <button type="submit" class="btn btn-blue" style="background:#8B5CF6;">📢 BİLDİRİMİ ATEŞLE</button>
                </div>
            </form>
        </div>
    </body>
    </html>
    """
    return html_content
@app.post("/admin/ozel-bildirim-gonder")
def ozel_bildirim_gonder_islem(
    baslik: str = Form(...), 
    icerik: Optional[str] = Form(None), 
    image_url: Optional[str] = Form(None), 
    haber_id: Optional[str] = Form(None), 
    hedef_kategori: str = Form("Tümü"), 
    db: Session = Depends(get_db)
):
    # 1. Haber ID güvenli dönüşüm (Boşsa veya harf girilmişse -1 kalır)
    h_id = -1
    if haber_id and str(haber_id).strip().isdigit():
        h_id = int(str(haber_id).strip())
        
    # 2. Token (Hedef Cihaz) Listesini Al
    token_listesi = hedef_kitle_tokenlari(hedef_kategori, db)
    
    # 3. Eğer sistemde en az 1 cihaz kayıtlıysa bildirimi ateşle
    if token_listesi:
        # İhtiyat: İçerik boş bırakılırsa Firebase hata vermesin diye varsayılan atama
        safe_icerik = icerik.strip() if icerik and icerik.strip() else "Detaylar için tıklayın."
        safe_image = image_url.strip() if image_url and image_url.strip() else None
        
        toplu_bildirim_gonder(
            baslik=baslik,
            icerik=safe_icerik,
            cihaz_tokenlari=token_listesi,
            haber_id=h_id,
            image_url=safe_image,
            hedef_kategori=hedef_kategori
        )
        
    return RedirectResponse(url="/admin", status_code=303)
@app.get("/admin/kategoriler", response_class=HTMLResponse)
def admin_kategoriler_sayfasi(db: Session = Depends(get_db)):
    kategoriler = db.query(KategoriDB).all()
    kategori_html = ""
    for k in kategoriler:
        kategori_html += f"""
        <div class="category-item">
            <a href="/admin/kategoriler/{k.isim}" style="color: #F1F5F9; font-weight: 600; font-size: 15px; text-decoration: none; flex: 1; transition: color 0.15s;" onmouseover="this.style.color='#64B5F6'" onmouseout="this.style.color='#F1F5F9'"># {k.isim}</a>
            <form action="/admin/kategori-sil/{k.id}" method="post" style="margin: 0;" onsubmit="return confirm('Bu kategoriyi silmek istediğinize emin misiniz?');">
                <button type="submit" class="btn btn-red btn-small">🗑️ SİL</button>
            </form>
        </div>
        """
    html_content = f"""
    <!DOCTYPE html>
    <html lang="tr">
    <head>
        <meta charset="UTF-8">
        <title>Kategori Yönetimi</title>
        {ADMIN_CSS}
    </head>
    <body>
        <div class="container">
            <h2>🏷️ Kategori Yönetim Modülü</h2>
            <div style="background: #1E222B; padding: 22px; border-radius: 12px; border: 1px solid #2D323F; margin-bottom: 35px; box-shadow: inset 0 2px 4px rgba(0,0,0,0.2);">
                <h3 style="margin-top: 0; color: #10B981; font-size: 15px; text-transform: uppercase; letter-spacing: 0.5px;">➕ Yeni Kategori Enjeksiyonu</h3>
                <form action="/admin/kategori-ekle" method="post" style="display: flex; gap: 12px; margin-top: 10px;">
                    <input type="text" name="isim" required placeholder="Örn: Yapay Zeka" style="margin: 0; flex: 1;">
                    <button type="submit" class="btn btn-green btn-small" style="height: 45px; padding: 0 24px; font-weight: 700;">SİSTEME EKLE</button>
                </form>
            </div>
            <h3 style="color: #94A3B8; border-bottom: 2px solid #22252E; padding-bottom: 12px; margin-bottom: 15px;">Mevcut Aktif Kategoriler</h3>
            <p style="color: #64748B; font-size: 12px; margin-top: -10px; margin-bottom: 20px; font-style: italic;">Süzme işlemi yapmak ve haberleri listelemek için kategori etiketine tıklayın.</p>
            <div>
                {kategori_html if kategori_html else "<p style='color: #64748B; text-align:center;'>Sistemde kayıtlı kategori bulunamadı.</p>"}
            </div>
            <div style="margin-top: 35px;">
                <a href="/admin" class="btn btn-gray" style="display: block;">⬅️ DASHBOARD ANA PANELİNE DÖN</a>
            </div>
        </div>
    </body>
    </html>
    """
    return html_content
@app.get("/admin/kategoriler/{kategori_adi}", response_class=HTMLResponse)
def admin_kategori_haberleri_sayfasi(kategori_adi: str, db: Session = Depends(get_db)):
    # HATA DÜZELTME: Kategori sayfasında tüm verileri çekmek RAM israfıdır. 
    # ilike SQLite JSON kolonlarında patladığı için cast(HaberDB.categories, String) kullanılır.
    # SQLite JSON kolonlarında Türkçe karakter araması (ü, ı vb.) için filtre kullanıldı.
    filtre = kategori_arama_filtresi(kategori_adi)
    filtrelenmis_haberler = db.query(HaberDB).filter(filtre).order_by(HaberDB.id.desc()).limit(100).all()
    liste_html = ""
    for h in filtrelenmis_haberler:
        fmt_id = f"{h.id:010d}"
        liste_html += f"""
        <a href="/admin/{fmt_id}" class="news-item">
            <img src="{h.headerImage}" alt="Görsel">
            <div class="news-item-content">
                <div class="news-title">{h.title}</div>
                <div class="news-id">ID: {fmt_id} &nbsp;|&nbsp; Toplam Okunma: {h.viewCount} &nbsp;|&nbsp; Son 24s: <span style="color:#10B981; font-weight:bold;">{h.dailyViewCount}</span></div>
            </div>
        </a>
        """
    html_content = f"""
    <!DOCTYPE html>
    <html lang="tr">
    <head>
        <meta charset="UTF-8">
        <title>{kategori_adi} Haberleri</title>
        {ADMIN_CSS}
    </head>
    <body>
        <div class="container">
            <h2>🏷️ #{kategori_adi} Filtreli İçerik Havuzu</h2>
            <p style="color: #64748B; font-size: 13px; border-bottom: 2px solid #22252E; padding-bottom: 12px; margin-bottom: 20px; font-style: italic;">Düzenleme veya silme paneline uçmak için haber kartına tıklayın.</p>
            <div class="news-list">
                {liste_html if liste_html else f"<p style='color: #64748B; text-align:center; padding: 20px;'>Bu kategori etiketine ait bir haber kaydı bulunamadı.</p>"}
            </div>
            <div style="margin-top: 35px;">
                <a href="/admin/kategoriler" class="btn btn-gray" style="display: block;">⬅️ KATEGORİ LİSTESİNE GERİ DÖN</a>
            </div>
        </div>
    </body>
    </html>
    """
    return html_content
@app.post("/admin/kategori-ekle")
def admin_kategori_ekle(isim: str = Form(...), db: Session = Depends(get_db)):
    temiz_isim = isim.strip()
    mevcut_kategoriler = db.query(KategoriDB).all()
    zaten_var = any(k.isim.lower() == temiz_isim.lower() for k in mevcut_kategoriler)
    if not zaten_var and temiz_isim != "":
        yeni_kat = KategoriDB(isim=temiz_isim.title(), aktif_mi=True)
        db.add(yeni_kat)
        db.commit()
    return RedirectResponse(url="/admin/kategoriler", status_code=303)
@app.post("/admin/kategori-sil/{kat_id}")
def admin_kategori_sil(kat_id: int, db: Session = Depends(get_db)):
    kategori = db.query(KategoriDB).filter(KategoriDB.id == kat_id).first()
    if kategori:
        db.delete(kategori)
        db.commit()
    return RedirectResponse(url="/admin/kategoriler", status_code=303)
# ==========================================
# 6. HABER EKLEME VE DÜZENLEME MODÜLLERİ
# ==========================================
@app.get("/admin/haber-ekleme", response_class=HTMLResponse)
def admin_haber_ekleme_sayfasi(db: Session = Depends(get_db)):
    kayitli_kategoriler = [k.isim for k in db.query(KategoriDB).all()]
    options_html = '<option value="Tümü">Tümü (Herkese)</option>'
    for k in kayitli_kategoriler:
        options_html += f'<option value="{k}">{k}</option>'
    html_content = f"""
    <!DOCTYPE html>
    <html lang="tr">
    <head>
        <meta charset="UTF-8">
        <title>Haber Ekle</title>
        {ADMIN_CSS}
    </head>
    <body>
        <div class="container">
            <h2>➕ Yeni Haber Ekleme Konsolu</h2>
            <form id="newsForm" action="/admin/haber-ekle-islem" method="post">
                <label>Haber Başlığı:</label>
                <input type="text" name="title" required placeholder="Haber manşetini girin...">
                <label>Kısa Özet (Mobil Ana Akışta Görünecek Metin):</label>
                <input type="text" name="feedSummary" required placeholder="Akış kartındaki alt bilgi...">
                <label>Bildirim Özeti (Telefona Düşecek Push Metni):</label>
                <input type="text" name="pushSummary" required placeholder="Kullanıcıyı çekecek flaş özet...">
                <label>Ana Kapak Görseli Linki (URL):</label>
                <input type="url" name="headerImage" required placeholder="https://example.com/resim.jpg">
                <div class="row">
                    <div>
                        <label>Güven Skoru (0-100):</label>
                        <input type="number" name="trustScore" min="0" max="100" value="100" required>
                    </div>
                    <div>
                        <label>İçerik/Haber Kaynağı:</label>
                        <input type="text" name="source" required placeholder="Örn: Anadolu Ajansı">
                    </div>
                </div>
                <label>Sistem Kategorileri:</label>
                <span class="hint">Sadece sistemde kayıtlı olanları virgülle ayırarak yazın (Örn: Gündem, Teknoloji)</span>
                <input type="text" id="categoriesInput" name="categories" required placeholder="Yazıp virgüle veya Enter'a basın..." autocomplete="off">
                <div id="categoriesPreview" class="chip-container"></div>
                <div id="categoryError" class="error-msg"></div>
                <label>Haberin Tam İçeriği:</label>
                {FORMAT_GUIDE_HTML}
                <span class="hint">Not: Kaydedildiğinde haber ID numarası JSON içerisinde yerleşik olarak mobil uygulamaya paslanacaktır.</span>
                <textarea name="content" rows="10" required placeholder="Haber metnini buraya detaylıca yazın..."></textarea>
                <div style="background: #1E222B; padding: 15px; border-radius: 8px; margin-top: 15px; border: 1px dashed #64B5F6;">
                    <label style="margin-top:0; color:#64B5F6;">📢 Bu haber için Bildirim Gönderilsin mi?</label>
                    <div style="display:flex; align-items:center; margin-top:8px;">
                        <input type="checkbox" name="bildirim_gonder" value="evet" style="width:20px; height:20px; margin:0 10px 0 0;">
                        <span style="color:#E2E8F0; font-size:14px;">Evet, anında gönder (Uygulamayı uyandırır)</span>
                    </div>
                    <label style="margin-top:15px;">Hedef Kitle (Kime Gönderilsin?):</label>
                    <select name="bildirim_hedef_kategori">
                        {options_html}
                    </select>
                </div>
                <div class="row" style="margin-top: 25px;">
                    <a href="/admin" class="btn btn-gray" style="line-height: normal; display: flex; align-items: center; justify-content: center;">İPTAL ET</a>
                    <button type="submit" class="btn btn-green">HABERİ YAYINA AL</button>
                </div>
            </form>
        </div>
    </body>
    </html>
    """
    script_content = VALIDATION_SCRIPT.replace("DATABASE_CATEGORIES_PLACEHOLDER", json.dumps(kayitli_kategoriler))
    return html_content + script_content
@app.post("/admin/haber-ekle-islem")
def haber_ekle_islem(
        title: str = Form(...), feedSummary: str = Form(...), pushSummary: str = Form(...),
        headerImage: str = Form(...), trustScore: int = Form(...), categories: str = Form(...),
        source: str = Form(...), content: str = Form(...), 
        bildirim_gonder: str = Form(None), bildirim_hedef_kategori: str = Form("Tümü"),
        db: Session = Depends(get_db)
):
    kaynak_listesi = [source.strip()]
    kayitli_kategoriler = [k.isim for k in db.query(KategoriDB).all()]
    kategori_listesi = []
    for k in categories.split(","):
        temiz = k.strip()
        if not temiz: continue
        for orj_kat in kayitli_kategoriler:
            if turkce_kucult(orj_kat) == turkce_kucult(temiz):
                kategori_listesi.append(orj_kat)
                break
    yeni_haber = HaberDB(
        title=title, viewCount=0, dailyViewCount=0, hourlyClicks={},
        pushSummary=pushSummary, feedSummary=feedSummary, content=content,
        headerImage=headerImage, contentImages=[], trustScore=trustScore,
        categories=kategori_listesi, sources=kaynak_listesi, date=date.today().isoformat()
    )
    db.add(yeni_haber)
    db.commit()
    db.refresh(yeni_haber)
    if bildirim_gonder == "evet" and pushSummary.strip():
        token_listesi = hedef_kitle_tokenlari(bildirim_hedef_kategori, db)
        if token_listesi:
            toplu_bildirim_gonder(
                baslik=title,
                icerik=pushSummary,
                cihaz_tokenlari=token_listesi,
                haber_id=yeni_haber.id,
                image_url=headerImage,
                hedef_kategori=bildirim_hedef_kategori
            )
    return RedirectResponse(url="/admin", status_code=303)
@app.get("/admin/haber-duzenle-secim", response_class=HTMLResponse)
def admin_duzenle_secim():
    html_content = f"""
    <!DOCTYPE html>
    <html lang="tr">
    <head>
        <meta charset="UTF-8">
        <title>Haber Bulucu</title>
        {ADMIN_CSS}
    </head>
    <body>
        <div class="container" style="text-align: center;">
            <h2>✏️ İçerik Modifikasyon Merkezi</h2>
            <p style="color: #94A3B8; font-size: 14px;">İşlem yapmak istediğiniz haber kaydının 10 haneli benzersiz ID numarasını girin.</p>
            <input type="text" id="haber_id_input" placeholder="Örn: 0000000001" style="text-align: center; font-size: 18px; letter-spacing: 4px; max-width: 320px; margin: 20px auto; display: block; font-family: monospace;">
            <div class="row" style="max-width: 400px; margin: auto; margin-top: 25px;">
                <a href="/admin" class="btn btn-gray" style="line-height: normal; display: flex; align-items: center; justify-content: center;">GERİ DÖN</a>
                <button onclick="git()" class="btn btn-blue">KAYDI SORGULA</button>
            </div>
            <script>
                function git() {{
                    var id = document.getElementById('haber_id_input').value.trim();
                    if(id) {{
                        window.location.href = "/admin/" + id;
                    }} else {{
                        alert("Lütfen geçerli bir ID girin!");
                    }}
                }}
            </script>
        </div>
    </body>
    </html>
    """
    return html_content
@app.get("/admin/{haber_id}", response_class=HTMLResponse)
def admin_haber_duzenle_sayfasi(haber_id: str, db: Session = Depends(get_db)):
    try:
        gercek_id = int(haber_id)
    except:
        return "<h2 style='color:white; text-align:center;'>Geçersiz ID Formatı!</h2>"
    haber = db.query(HaberDB).filter(HaberDB.id == gercek_id).first()
    if not haber:
        return f"<div style='text-align:center; padding: 50px; color: white; background: #0E1013; font-family: sans-serif;'><h2>❌ Haber Bulunamadı!</h2><a href='/admin' style='color:#64B5F6; text-decoration:none; font-weight:bold;'>Dashboard'a Geri Dön</a></div>"
    temiz_icerik = haber.content if haber.content else ""
    kat_str = ", ".join(haber.categories) if haber.categories else ""
    kaynak_str = ", ".join(haber.sources) if haber.sources else ""
    kayitli_kategoriler = [k.isim for k in db.query(KategoriDB).all()]
    options_html = '<option value="Tümü">Tümü (Herkese)</option>'
    for k in kayitli_kategoriler:
        options_html += f'<option value="{k}">{k}</option>'
    html_content = f"""
    <!DOCTYPE html>
    <html lang="tr">
    <head>
        <meta charset="UTF-8">
        <title>Haberi Düzenle</title>
        {ADMIN_CSS}
    </head>
    <body>
        <div class="container">
            <h2>✏️ Kayıt Düzenleme Odası (ID: {haber_id})</h2>
            <div style="background: #1E222B; padding: 12px; border-radius: 10px; margin-bottom: 25px; text-align: center; border: 1px solid #2D323F;">
                <span style="color: #94A3B8; font-size: 13px;">Analitik Veriler &nbsp;|&nbsp; Toplam Okunma: <b style="color: white;">{haber.viewCount}</b> &nbsp;|&nbsp; Son 24 Saat: <b style="color: #10B981;">{haber.dailyViewCount}</b></span>
            </div>
            <form id="newsForm" action="/admin/guncelle/{haber_id}" method="post">
                <label>Haber Başlığı:</label>
                <input type="text" name="title" value="{haber.title}" required>
                <label>Kısa Özet:</label>
                <input type="text" name="feedSummary" value="{haber.feedSummary}" required>
                <label>Bildirim Özeti:</label>
                <input type="text" name="pushSummary" value="{haber.pushSummary}" required>
                <label>Ana Görsel Linki (URL):</label>
                <input type="url" name="headerImage" value="{haber.headerImage}" required>
                <div class="row">
                    <div>
                        <label>Güven Skoru (0-100):</label>
                        <input type="number" name="trustScore" min="0" max="100" value="{haber.trustScore}" required>
                    </div>
                    <div>
                        <label>Haber Kaynağı:</label>
                        <input type="text" name="source" value="{kaynak_str}" required>
                    </div>
                </div>
                <label>Sistem Kategorileri:</label>
                <span class="hint">Sadece sistemde kayıtlı olanları virgülle ayırarak yazın (Örn: Gündem, Teknoloji)</span>
                <input type="text" id="categoriesInput" name="categories" value="{kat_str}" required autocomplete="off">
                <div id="categoriesPreview" class="chip-container"></div>
                <div id="categoryError" class="error-msg"></div>
                <label>Haberin Tam İçeriği:</label>
                {FORMAT_GUIDE_HTML}
                <textarea name="content" rows="10" required>{temiz_icerik}</textarea>
                <div style="background: #1E222B; padding: 15px; border-radius: 8px; margin-top: 15px; border: 1px dashed #64B5F6;">
                    <label style="margin-top:0; color:#64B5F6;">📢 Güncelleme sonrası Bildirim Gönderilsin mi?</label>
                    <div style="display:flex; align-items:center; margin-top:8px;">
                        <input type="checkbox" name="bildirim_gonder" value="evet" style="width:20px; height:20px; margin:0 10px 0 0;">
                        <span style="color:#E2E8F0; font-size:14px;">Evet, bildirimi ateşle</span>
                    </div>
                    <label style="margin-top:15px;">Hedef Kitle (Kime Gönderilsin?):</label>
                    <select name="bildirim_hedef_kategori">
                        {options_html}
                    </select>
                </div>
                <div class="row" style="margin-top: 25px;">
                    <button type="submit" class="btn btn-blue">💾 DEĞİŞİKLİKLERİ VERİTABANINA KAYDET</button>
                </div>
            </form>
            <hr style="border: 0; border-top: 2px solid #22252E; margin: 30px 0;">
            <form action="/admin/sil/{haber_id}" method="post" onsubmit="return confirm('Bu haberi KALICI olarak silmek istediğinize emin misiniz? (Geri dönüşü yoktur!)');">
                <div class="row">
                    <a href="/admin" class="btn btn-gray" style="line-height: normal; display: flex; align-items: center; justify-content: center;">İPTAL ET</a>
                    <button type="submit" class="btn btn-red">🗑️ HABER KAYDINI TAMAMEN SİL</button>
                </div>
            </form>
        </div>
    </body>
    </html>
    """
    script_content = VALIDATION_SCRIPT.replace("DATABASE_CATEGORIES_PLACEHOLDER", json.dumps(kayitli_kategoriler))
    return html_content + script_content
@app.post("/admin/guncelle/{haber_id}")
def haber_guncelle(
        haber_id: str, title: str = Form(...), feedSummary: str = Form(...), pushSummary: str = Form(...),
        headerImage: str = Form(...), trustScore: int = Form(...), categories: str = Form(...),
        source: str = Form(...), content: str = Form(...), 
        bildirim_gonder: str = Form(None), bildirim_hedef_kategori: str = Form("Tümü"),
        db: Session = Depends(get_db)
):
    gercek_id = int(haber_id)
    haber = db.query(HaberDB).filter(HaberDB.id == gercek_id).first()
    if haber:
        haber.title = title
        haber.feedSummary = feedSummary
        haber.pushSummary = pushSummary
        haber.headerImage = headerImage
        haber.trustScore = trustScore
        haber.sources = [source.strip()]
        kayitli_kategoriler = [k.isim for k in db.query(KategoriDB).all()]
        kategori_listesi = []
        for k in categories.split(","):
            temiz = k.strip()
            if not temiz: continue
            for orj_kat in kayitli_kategoriler:
                if turkce_kucult(orj_kat) == turkce_kucult(temiz):
                    kategori_listesi.append(orj_kat)
                    break
        haber.categories = kategori_listesi
        haber.content = content.split('\n\n\n\n')[0]
        haber.date = date.today().isoformat()
        db.commit()
        if bildirim_gonder == "evet" and pushSummary.strip():
            token_listesi = hedef_kitle_tokenlari(bildirim_hedef_kategori, db)
            if token_listesi:
                toplu_bildirim_gonder(
                    baslik=title,
                    icerik=pushSummary,
                    cihaz_tokenlari=token_listesi,
                    haber_id=haber.id,
                    image_url=headerImage,
                    hedef_kategori=bildirim_hedef_kategori
                )
    return RedirectResponse(url="/admin", status_code=303)
@app.post("/admin/sil/{haber_id}")
def haber_sil(haber_id: str, db: Session = Depends(get_db)):
    gercek_id = int(haber_id)
    haber = db.query(HaberDB).filter(HaberDB.id == gercek_id).first()
    if haber:
        db.delete(haber)
        db.commit()
    return RedirectResponse(url="/admin", status_code=303)
if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
