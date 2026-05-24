import os
from sqlalchemy import create_engine
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, Form, Depends, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import create_engine, Column, Integer, String, Text, JSON, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from datetime import date, datetime, timedelta
import json

# ==========================================
# 1. VERİTABANI KURULUMU VE ŞEMALAR
# ==========================================
db_path = "/tmp/haberler.db" if os.getenv("RENDER") else "./haberler.db"
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{db_path}")

connect_args = {"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)

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


Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ==========================================
# 2. FASTAPI YENİ NESİL YAŞAM DÖNGÜSÜ (LIFESPAN)
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
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


app = FastAPI(title="haberPortaliAPI", version="2.4.0", lifespan=lifespan)


# ==========================================
# 3. ANDROID RETROFIT İLETİŞİM KAPILARI (GERÇEK SAYFALAMA)
# ==========================================
@app.get("/")
def ana_sayfa():
    return {"mesaj": "Sunucu ve Gelişmiş Veritabanı Aktif!"}


@app.get("/kategoriler")
def kategorileri_getir(db: Session = Depends(get_db)):
    kategoriler = db.query(KategoriDB).filter(KategoriDB.aktif_mi == True).all()
    kategori_isimleri = [k.isim for k in kategoriler]
    return {"kategoriler": kategori_isimleri}


@app.get("/haberler/son-dakika")
def son_dakika_haberleri(skip: int = Query(0, ge=0), limit: int = Query(6, ge=1), db: Session = Depends(get_db)):
    tum_haberler = db.query(HaberDB).order_by(HaberDB.id.desc()).all()
    # Vitrin sadece ilk yüklemede (skip=0) yollanır (10 adet).
    vitrin = sorted(tum_haberler, key=lambda x: x.dailyViewCount, reverse=True)[:10] if skip == 0 else []
    return {"vitrin": vitrin, "haberler": tum_haberler[skip : skip + limit]}


@app.get("/haberler/filtrele")
def coklu_kategori_getir(kategoriler: str = Query(""), skip: int = Query(0, ge=0), limit: int = Query(6, ge=1), db: Session = Depends(get_db)):
    tum_haberler = db.query(HaberDB).order_by(HaberDB.id.desc()).all()

    if not kategoriler:
        vitrin = sorted(tum_haberler, key=lambda x: x.dailyViewCount, reverse=True)[:10] if skip == 0 else []
        return {"vitrin": vitrin, "haberler": tum_haberler[skip : skip + limit]}

    istenen_kategoriler_lower = [k.strip().lower() for k in kategoriler.split(",")]
    filtrelenmis_haberler = [
        h for h in tum_haberler
        if any(kat.lower() in istenen_kategoriler_lower for kat in h.categories)
    ]
    vitrin = sorted(filtrelenmis_haberler, key=lambda x: x.dailyViewCount, reverse=True)[:10] if skip == 0 else []
    return {"vitrin": vitrin, "haberler": filtrelenmis_haberler[skip : skip + limit]}


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
    return {"hata": "Haber bulunamadı"}


@app.get("/haberler/{kategori_adi}")
def kategoriye_gore_haber_getir(kategori_adi: str, skip: int = Query(0, ge=0), limit: int = Query(6, ge=1), db: Session = Depends(get_db)):
    tum_haberler = db.query(HaberDB).order_by(HaberDB.id.desc()).all()
    kategori_adi_lower = kategori_adi.lower()

    filtrelenmis_haberler = [
        h for h in tum_haberler
        if any(kat.lower() == kategori_adi_lower for kat in h.categories)
    ]
    vitrin = sorted(filtrelenmis_haberler, key=lambda x: x.dailyViewCount, reverse=True)[:10] if skip == 0 else []
    return {"vitrin": vitrin, "haberler": filtrelenmis_haberler[skip : skip + limit]}


# ==========================================
# 4. GELİŞMİŞ WEB ADMİN PANELİ (PREMIUM UI DESIGN)
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
    """Ana Dashboard: Filtreleme Seçenekli Haber Listesi"""
    haberler = db.query(HaberDB).all()

    if sort == "yeniden-eskiye":
        haberler = sorted(haberler, key=lambda x: x.id, reverse=True)
    elif sort == "eskiden-yeniye":
        haberler = sorted(haberler, key=lambda x: x.id)
    elif sort == "en-cok-tiklanan":
        haberler = sorted(haberler, key=lambda x: x.viewCount, reverse=True)
    elif sort == "24-saatte-en-cok-tiklanan":
        haberler = sorted(haberler, key=lambda x: x.dailyViewCount, reverse=True)
    elif sort == "en-cok-kullanilan":
        kategori_frekanslari = {}
        for h in haberler:
            for kat in h.categories:
                kategori_frekanslari[kat.lower()] = kategori_frekanslari.get(kat.lower(), 0) + 1

        def haber_populerlik_skoru(h):
            if not h.categories: return 0
            return max(kategori_frekanslari.get(kat.lower(), 0) for cat in h.categories)

        haberler = sorted(haberler, key=haber_populerlik_skoru, reverse=True)

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
    tum_haberler = db.query(HaberDB).order_by(HaberDB.id.desc()).all()
    kategori_adi_lower = kategori_adi.lower()

    filtrelenmis_haberler = [
        h for h in tum_haberler
        if any(kat.lower() == kategori_adi_lower for kat in h.categories)
    ]

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
# 5. HABER EKLEME VE DÜZENLEME MODÜLLERİ
# ==========================================
@app.get("/admin/haber-ekleme", response_class=HTMLResponse)
def admin_haber_ekleme_sayfasi(db: Session = Depends(get_db)):
    kayitli_kategoriler = [k.isim for k in db.query(KategoriDB).all()]

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
        source: str = Form(...), content: str = Form(...), db: Session = Depends(get_db)
):
    kaynak_listesi = [source.strip()]
    kayitli_kategoriler = [k.isim for k in db.query(KategoriDB).all()]
    kategori_listesi = []

    for k in categories.split(","):
        temiz = k.strip()
        if not temiz: continue

        for orj_kat in kayitli_kategoriler:
            if orj_kat.lower() == temiz.lower():
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
        source: str = Form(...), content: str = Form(...), db: Session = Depends(get_db)
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
                if orj_kat.lower() == temiz.lower():
                    kategori_listesi.append(orj_kat)
                    break

        haber.categories = kategori_listesi
        haber.content = content.split('\n\n\n\n')[0]
        haber.date = date.today().isoformat()

        db.commit()

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
