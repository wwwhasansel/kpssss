from flask import Flask, render_template, request, send_file, send_from_directory
import pdfplumber
import pandas as pd
import re
import os
import tempfile
import json

app = Flask(__name__)

# determine base directory so data paths are absolute
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FOLDER = os.path.join(BASE_DIR, "data")  # PDF klasörlerinin bulunduğu ana klasör

CACHE_FILE = os.path.join(BASE_DIR, "pdf_cache.json")
NITELIK_DESC_FILE = os.path.join(BASE_DIR, "nitelik_descriptions.json")

# Cache for PDF tables to avoid re-reading on every request
pdf_cache = {}

def _cache_key(pdf_path):
    """Normalize PDF path to a relative key so the cache works across platforms."""
    try:
        return os.path.relpath(pdf_path, BASE_DIR)
    except ValueError:
        return pdf_path

def load_cache():
    global pdf_cache
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                raw = json.load(f)
            # Only keep entries with relative (non-absolute) keys to stay portable
            pdf_cache = {k: v for k, v in raw.items() if not os.path.isabs(k)}
        except Exception as e:
            print(f"Cache yüklenirken hata: {e}")
            pdf_cache = {}

def save_cache():
    try:
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(pdf_cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Cache kaydedilirken hata: {e}")

def get_pdf_tables(pdf_path):
    key = _cache_key(pdf_path)
    if key not in pdf_cache:
        tables = []
        try:
            with pdfplumber.open(pdf_path) as pdf:
                for page in pdf.pages:
                    table = page.extract_table()
                    if table:
                        tables.extend(table)
        except Exception as e:
            print(f"Error reading {pdf_path}: {e}")
            tables = []
        pdf_cache[key] = tables
        save_cache()  # Save after loading new PDF
    return pdf_cache[key]

def get_all_program_names():
    """Extract all program names from minmax PDFs for SEO keywords."""
    program_names = set()
    for root, dirs, files in os.walk(DATA_FOLDER):
        for file in files:
            if file == 'minmax.pdf':
                pdf_path = os.path.join(root, file)
                tables = get_pdf_tables(pdf_path)
                for row in tables:
                    if len(row) > 1:
                        program_name = str(row[1]).strip()  # Assume program name is in column 1
                        if program_name and len(program_name) > 3:  # Filter out short or empty names
                            program_names.add(program_name.lower())
    return list(program_names)


def normalize_for_search(s):
    """Türkçe büyük/küçük harf ve ASCII folding normalize et."""
    return (s
            .replace('İ', 'i').replace('I', 'i').replace('ı', 'i')
            .replace('Ğ', 'g').replace('ğ', 'g')
            .replace('Ş', 's').replace('ş', 's')
            .replace('Ü', 'u').replace('ü', 'u')
            .replace('Ö', 'o').replace('ö', 'o')
            .replace('Ç', 'c').replace('ç', 'c')
            .lower())


def shorten_description(desc):
    """PDF'deki uzun nitelik açıklamalarını kısalt."""
    desc = desc.replace('\n', ' ').strip()
    # "X lisans/önlisans programından ... mezun olmak." sonekini kaldır
    desc = re.sub(
        r'\s+(?:lisans|önlisans|ön lisans)\s+program.+?mezun\s+olmak\.?',
        '', desc, flags=re.IGNORECASE
    ).strip()
    # Kalan "... mezun olmak." sonekini kaldır
    desc = re.sub(r'\s+mezun\s+olmak\.?$', '', desc, flags=re.IGNORECASE).strip()
    # Sondaki noktalama işaretlerini temizle
    desc = desc.rstrip('.,;/ ')
    # Hâlâ uzunsa 75 karakterde kes
    if len(desc) > 75:
        desc = desc[:72] + '...'
    return desc

@app.get("/healthz")
def healthz():
    return "ok", 200
    
def build_nitelik_descriptions():
    """Lisans ve önlisans nitelik PDF'lerini okuyup {kod: açıklama} sözlüğü döndür."""
    descriptions = {}
    # BASE_DIR içindeki *nitelik*.pdf dosyalarını bul
    pdf_files = [
        f for f in os.listdir(BASE_DIR)
        if 'nitelik' in f.lower() and f.lower().endswith('.pdf')
    ]
    for fname in pdf_files:
        pdf_path = os.path.join(BASE_DIR, fname)
        try:
            with pdfplumber.open(pdf_path) as pdf:
                for page in pdf.pages:
                    table = page.extract_table()
                    if not table:
                        continue
                    for row in table:
                        if not row or len(row) < 2:
                            continue
                        kod = str(row[0]).strip() if row[0] else ''
                        if len(kod) == 4 and kod.isdigit():
                            desc = str(row[1]).strip() if row[1] else ''
                            skip_headers = {'öğrenim koşulu', 'ogretim kosulu'}
                            if desc and normalize_for_search(desc) not in skip_headers:
                                descriptions[kod] = desc
        except Exception as e:
            print(f"Nitelik PDF okunurken hata ({fname}): {e}")
    return descriptions


# Load PDF cache on startup
load_cache()

# Extract and save codes on startup
nitelik_codes_file = os.path.join(BASE_DIR, 'nitelik_codes.json')
if os.path.exists(nitelik_codes_file):
    with open(nitelik_codes_file, 'r', encoding='utf-8') as f:
        nitelik_codes = json.load(f)
else:
    nitelik_codes = {}
CODES_FILE = os.path.join(BASE_DIR, "nitelik_codes.json")
with open(CODES_FILE, 'w', encoding='utf-8') as f:
    json.dump(nitelik_codes, f, ensure_ascii=False, indent=2)

# Load or build nitelik descriptions for autocomplete
if os.path.exists(NITELIK_DESC_FILE):
    try:
        with open(NITELIK_DESC_FILE, 'r', encoding='utf-8') as f:
            nitelik_descriptions = json.load(f)
    except Exception:
        nitelik_descriptions = {}
else:
    nitelik_descriptions = {}

if not nitelik_descriptions:
    print("Nitelik açıklamaları PDF'lerden okunuyor...")
    nitelik_descriptions = build_nitelik_descriptions()
    try:
        with open(NITELIK_DESC_FILE, 'w', encoding='utf-8') as f:
            json.dump(nitelik_descriptions, f, ensure_ascii=False, indent=2)
        print(f"{len(nitelik_descriptions)} nitelik açıklaması kaydedildi.")
    except Exception as e:
        print(f"Nitelik açıklamaları kaydedilirken hata: {e}")

# Autocomplete için kısa etiket sözlüğü: {kod: "4630 - Makine Mühendisliği"}
nitelik_labels = {
    kod: f"{kod} - {shorten_description(desc)}"
    for kod, desc in nitelik_descriptions.items()
}

# Cache program keywords at startup to avoid re-scanning PDFs on every request
_program_keywords_cache = None

def get_cached_program_keywords():
    global _program_keywords_cache
    if _program_keywords_cache is None:
        _program_keywords_cache = get_all_program_names()
    return _program_keywords_cache

def analiz_et(yil, donem, egitim_turu, aranan_kod):
    # normalize input
    aranan_kod = (aranan_kod or "").strip()
    messages = []

    # Determine which periods to search
    donemler = []

    if donem == "all":
        # Scan data folder for available periods with format yil_donem
        try:
            items = os.listdir(DATA_FOLDER)
            for item in items:
                item_path = os.path.join(DATA_FOLDER, item)
                if os.path.isdir(item_path) and item.startswith(f"{yil}_"):
                    # Extract donem number from folder name (e.g., "2025_1" -> "1")
                    parts = item.split("_")
                    if len(parts) == 2 and parts[1].isdigit():
                        donemler.append(parts[1])
            donemler.sort(key=int)  # Sort numerically
        except Exception as e:
            messages.append(f"Veri klasörü taranırken hata oluştu: {e}")
            return [], messages
    else:
        donemler = [donem]
    
    final_list = []

    if not aranan_kod:
        messages.append("Lütfen aramak istediğiniz kodu girin.")
        return final_list, messages
    
    for donem_val in donemler:
        # determine base folder for this period
        klasor = os.path.join(DATA_FOLDER, f"{yil}_{donem_val}")
        if not os.path.isdir(klasor):
            messages.append(f"{yil}_{donem_val} klasörü bulunamadı.")
            continue

        # support both direct PDF placement (e.g. 2025_1/) and nested by education type (e.g. 2025_2/lisans/)
        candidates = [os.path.join(klasor, egitim_turu)] if egitim_turu else []
        candidates.append(klasor)

        tablo2_yolu = None
        minmax_yolu = None
        used_folder = None
        for cand in candidates:
            cand_tablo2 = os.path.join(cand, "tablo2.pdf")
            cand_minmax = os.path.join(cand, "minmax.pdf")
            if os.path.exists(cand_tablo2) and os.path.exists(cand_minmax):
                tablo2_yolu = cand_tablo2
                minmax_yolu = cand_minmax
                used_folder = cand
                break

        if not tablo2_yolu or not minmax_yolu:
            messages.append(f"{yil}_{donem_val} için '{egitim_turu}' veya ana klasörde tablo2/minmax PDF bulunamadı.")
            continue

        # 1️⃣ Extract scores from Min-Max PDF
        puan_verileri = {}
        try:
            minmax_tables = get_pdf_tables(minmax_yolu)
            for row in minmax_tables:
                clean_row = [str(c).strip() if c else "" for c in row]
                if clean_row and len(clean_row[0]) == 9 and clean_row[0].isdigit():
                    kod = clean_row[0]
                    puan_verileri[kod] = {
                        "Kontenjan": clean_row[3],
                        "Min_Puan": clean_row[-2],
                        "Max_Puan": clean_row[-1]
                    }
        except Exception as e:
            messages.append(f"{minmax_yolu} okunurken hata oluştu: {e}")
            continue
        # 2️⃣ Find code in Tablo2
        try:
            tablo2_tables = get_pdf_tables(tablo2_yolu)
            for row in tablo2_tables:
                row_text = " ".join([str(c) for c in row if c]).replace('\n', ' ')
                if aranan_kod in row_text:
                    kod_match = re.search(r'(\d{9})', row_text)
                    if kod_match:
                        kod = kod_match.group(1)
                        if kod in puan_verileri:
                            il = str(row[3]).split('\n')[0] if len(row) > 3 else ""
                            ilce = str(row[4]).split('\n')[0] if len(row) > 4 else ""
                            if "SÖZLEŞMELİ" in il or "PERSONEL" in il:
                                il = str(row[4]).split('\n')[0] if len(row) > 4 else il
                                ilce = str(row[5]).split('\n')[0] if len(row) > 5 else "MERKEZ"

                            final_list.append({
                                "ÖSYM Kodu": kod,
                                "Kayıt no": str(row[1]).split('\n')[0],
                                "Kurum": str(row[2]).replace('\n', ' '),
                                "Pozisyon": il.strip(),
                                "İl": ilce.strip(),
                                "Kontenjan": puan_verileri[kod]["Kontenjan"],
                                "Min Puan": puan_verileri[kod]["Min_Puan"],
                                "Max Puan": puan_verileri[kod]["Max_Puan"]
                            })
        except Exception as e:
            messages.append(f"{tablo2_yolu} okunurken hata oluştu: {e}")
            continue
    
    if not final_list:
        messages.append("Aranan kod için herhangi bir sonuç bulunamadı.")
    return final_list, messages

# Flask route
@app.route("/", methods=["GET", "POST"])
def index():
    tablo = []
    excel_path = None
    sort_by = None
    sort_order = None
    messages = []
    
    # Dynamic SEO keywords from program names
    base_keywords = ["ösym taban puanı", "bölüm puanları", "lisans taban", "önlisans puan", "ösym arama", "2025 puanlar", "ösym kod arama", "min max puan", "kontenjan"]
    program_keywords = get_cached_program_keywords()
    all_keywords = base_keywords + program_keywords
    keywords_str = ", ".join(all_keywords[:150])  # Limit for meta tag
    if request.method == "POST":
        yil = request.form.get("yil")
        donem = request.form.get("donem")
        egitim_turu = request.form.get("egitim")
        kod = request.form.get("kod")

        sort_by = request.form.get("sort_by") or None
        sort_order = request.form.get("sort_order") or "asc"

        print(f"POST received: yil={yil}, donem={donem}, egitim={egitim_turu}, kod={kod}, sort_by={sort_by}, sort_order={sort_order}")
        tablo, messages = analiz_et(yil, donem, egitim_turu, kod)

        # apply sorting if requested and results exist
        if tablo and sort_by:
            reverse = (sort_order == "desc")
            try:
                tablo = sorted(tablo, key=lambda r: float(r.get(sort_by, 0)) if r.get(sort_by) not in (None, "") else 0, reverse=reverse)
            except ValueError:
                # fallback to string sort
                tablo = sorted(tablo, key=lambda r: r.get(sort_by, ""), reverse=reverse)

        if tablo:
            # Geçici Excel oluştur
            df = pd.DataFrame(tablo)
            temp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx")
            df.to_excel(temp.name, index=False)
            excel_path = temp.name
        # temizleme: eski geçici dosyaların birikmesini engelle
        # (basit bir yaklaşım, prod ortamında daha sağlam strateji gerekir)
        if excel_path:
            base = os.path.basename(excel_path)
            for fn in os.listdir(tempfile.gettempdir()):
                if fn.endswith('.xlsx') and fn != base:
                    try:
                        os.remove(os.path.join(tempfile.gettempdir(), fn))
                    except Exception:
                        pass

        # AJAX isteği için sadece sonuç parçasını döndür
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return render_template("_results.html", tablo=tablo, excel_path=excel_path, messages=messages)

    return render_template("index.html", tablo=tablo, excel_path=excel_path,
                           sort_by=sort_by, sort_order=sort_order, messages=messages, keywords=keywords_str)

@app.route("/api/suggestions")
def suggestions():
    q = request.args.get('q', '').strip()
    if len(q) < 2:
        return {"suggestions": []}

    q_norm = normalize_for_search(q)

    if q.isdigit():
        # Sayısal sorgu → yalnızca kod öneki eşleşmesi
        matches = [
            label for kod, label in nitelik_labels.items()
            if kod.startswith(q)
        ][:10]
    else:
        # Metin sorgusu → açıklama başı öncelikli, sonra içerme
        prefix_m, substr_m = [], []
        for kod, label in nitelik_labels.items():
            label_norm = normalize_for_search(label)
            # Açıklama kısmı "KOD - " den sonra başlar
            desc_norm = label_norm.split(' - ', 1)[1] if ' - ' in label_norm else label_norm
            if desc_norm.startswith(q_norm):
                prefix_m.append(label)
            elif q_norm in label_norm:
                substr_m.append(label)
            if len(prefix_m) >= 10:
                break
        matches = (prefix_m + substr_m)[:10]

    return {"suggestions": matches}

@app.route("/download/<path:excel_path>")
def download(excel_path):
    return send_file(excel_path, as_attachment=True)


@app.route("/manifest.webmanifest")
def manifest():
    return send_from_directory(app.static_folder, "manifest.webmanifest", mimetype="application/manifest+json")


@app.route("/service-worker.js")
def service_worker():
    return send_from_directory(app.static_folder, "service-worker.js", mimetype="application/javascript")


if __name__ == "__main__":
    app.run(debug=True)
