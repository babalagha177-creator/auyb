import os
from io import BytesIO

import requests
import streamlit as st
import pytz
from datetime import datetime
import pandas as pd
from supabase import create_client

# 1. إعدادات الصفحة (يجب أن يكون أول استدعاء لـ Streamlit)
st.set_page_config(page_title="نظام باب الآغا", layout="wide")

# تطبيع النص العربي للبحث (همزات/ياء/ألف مقصورة)
def normalize_arabic_text(text):
    normalized = str(text or "").strip().lower()
    return (
        normalized
        .replace("أ", "ا")
        .replace("إ", "ا")
        .replace("آ", "ا")
        .replace("ى", "ي")
        .replace("ؤ", "و")
        .replace("ئ", "ي")
    )

def _normalize_secret_value(val) -> str:
    """إزالة مسافات، BOM، أو علامات تنصيص لاصقة عند لصق المفتاح من لوحة Supabase."""
    s = str(val or "").strip()
    if s.startswith("\ufeff"):
        s = s.lstrip("\ufeff").strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1].strip()
    return s


def _secrets_pick(*keys: str):
    for k in keys:
        try:
            v = st.secrets[k]
        except Exception:
            continue
        if v is None:
            continue
        s = _normalize_secret_value(v)
        if s:
            return s
    return ""


def resolve_supabase_credentials():
    """
    يدعم المفاتيح في الجذر أو داخل قسم [supabase] في TOML.
    يقبل SUPABASE_KEY أو SUPABASE_ANON_KEY أو SUPABASE_SERVICE_ROLE_KEY.
    """
    url = ""
    key = ""
    nested = None
    try:
        nested = st.secrets["supabase"]
    except Exception:
        nested = None
    if nested is not None and not isinstance(nested, str):
        getter = getattr(nested, "get", None)
        if callable(getter):
            url = _normalize_secret_value(
                getter("SUPABASE_URL") or getter("url") or ""
            )
            key = _normalize_secret_value(
                getter("SUPABASE_KEY")
                or getter("key")
                or getter("SUPABASE_ANON_KEY")
                or getter("SUPABASE_SERVICE_ROLE_KEY")
                or ""
            )
    if not url:
        url = _secrets_pick("SUPABASE_URL")
    if not key:
        key = _secrets_pick(
            "SUPABASE_KEY",
            "SUPABASE_SERVICE_ROLE_KEY",
            "SUPABASE_ANON_KEY",
        )
    return url, key


@st.cache_resource
def _cached_supabase(url: str, key: str):
    return create_client(url, key)


def get_supabase_client():
    url, key = resolve_supabase_credentials()
    if not url or not key:
        raise ValueError(
            "SUPABASE_URL أو مفتاح Supabase فارغان داخل st.secrets "
            "(توقع: SUPABASE_URL و SUPABASE_KEY أو SUPABASE_SERVICE_ROLE_KEY / SUPABASE_ANON_KEY)."
        )
    return _cached_supabase(url, key)


supabase_client = None
supabase_connection_error = ""


def init_supabase():
    global supabase_client, supabase_connection_error
    try:
        supabase_client = get_supabase_client()
        supabase_connection_error = ""
        st.sidebar.success("✅ تم الاتصال بقاعدة بيانات Supabase")
    except Exception as e:
        supabase_client = None
        supabase_connection_error = str(e)
        st.sidebar.error("❌ تعذر الاتصال بـ Supabase")
        hint = ""
        err_low = str(e).lower()
        if "invalid api key" in err_low:
            hint = (
                "\n\n**تفسير شائع:** إن كان المفتاح يبدأ بـ `sb_secret_` أو `sb_publishable_` فإن إصدارات "
                "`supabase` القديمة (مثل 2.15) ترفضها محلياً برسالة *Invalid API key* قبل أي اتصال. "
                "حدّث الحزم (`pip install -r requirements.txt`) أو استخدم من لوحة Supabase قسماً **Legacy API keys** "
                "وانسخ مفتاح **service_role** أو **anon** (سلسلة طويلة تبدأ بـ `eyJ`). "
                "تأكد أن `SUPABASE_URL` لمشروعك يطابق نفس المشروع الذي نُسخ منه المفتاح."
            )
        st.error(
            "فشل تهيئة Supabase. تحقق من `SUPABASE_URL` والمفتاح في `.streamlit/secrets.toml` "
            "(أو أسرار التطبيق على Streamlit Cloud)، وأن الجداول `inventory_items` و `daily_audit_log` قد أُنشئت "
            "(ملف `supabase_schema.sql`).\n\n"
            f"التفاصيل: {supabase_connection_error}"
            f"{hint}"
        )


st.markdown("""
    <style>
    /* إجبار الواجهة والطباعة على اليمين */
    html, body, [data-testid="stAppViewContainer"], .main { 
        direction: rtl !important; 
        text-align: right !important; 
    }
    
    @media print {
        header, footer, [data-testid="stSidebar"], .stButton, [data-testid="stHeader"] {
            display: none !important;
        }
        .print-container {
            display: block !important;
            visibility: visible !important;
            direction: rtl !important;
            width: 100% !important;
        }
        table { width: 100% !important; border-collapse: collapse; }
        th, td { border: 1px solid black !important; padding: 8px; text-align: center !important; }
    }
    </style>
""", unsafe_allow_html=True)
# 2. نظام الحماية
if 'authenticated' not in st.session_state:
    st.session_state['authenticated'] = False

if not st.session_state['authenticated']:
    st.markdown("<h1 style='text-align: center; color: #D4AF37;'>🍞 مخابز باب الآغا</h1>", unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        password = st.text_input("أدخل كلمة المرور", type="password")
        if st.button("تأكيد الدخول"):
            if password == "0000":
                st.session_state['authenticated'] = True
                st.rerun()
    st.stop()

# 3. إدارة قاعدة البيانات (Supabase — سحابي بالكامل)
DAILY_LOG_HEADERS = [
    "التاريخ",
    "الوقت",
    "اسم السلعة",
    "المتوفر",
    "المطلوب",
    "الوحدة",
    "الملاحظات",
    "اسم المسؤول",
]
KHALLFA_RESULTS_ORDER = [
    "اسم السلعة",
    "المتوفر",
    "المطلوب",
    "الوحدة",
    "الملاحظات",
    "التاريخ",
    "الوقت",
    "اسم المسؤول",
]


def _float_safe_cell(val):
    try:
        return float(str(val).replace(",", "").strip())
    except Exception:
        return 0.0


def _audit_rows_as_dicts(df: pd.DataFrame):
    df = normalize_log_columns(df)
    rows = []
    for _, r in df.iterrows():
        rows.append(
            {
                "log_date": str(r["التاريخ"]).strip(),
                "log_time": str(r["الوقت"]).strip(),
                "item_name": str(r["اسم السلعة"]).strip(),
                "available": _float_safe_cell(r["المتوفر"]),
                "required": _float_safe_cell(r["المطلوب"]),
                "unit_val": str(r["الوحدة"] or "").strip(),
                "notes": str(r["الملاحظات"] or "").strip(),
                "manager_label": str(r["اسم المسؤول"] or "").strip(),
            }
        )
    return rows


def append_log_rows_to_supabase(df: pd.DataFrame):
    if df is None or df.empty or supabase_client is None:
        return
    rows = _audit_rows_as_dicts(df)
    if not rows:
        return
    supabase_client.table("daily_audit_log").insert(rows).execute()


def fetch_audit_log_for_admin(limit: int = 25000):
    cols = "log_date, log_time, item_name, available, required, unit_val, notes, manager_label"
    if supabase_client is None:
        return pd.DataFrame(
            columns=[
                "log_date",
                "log_time",
                "item_name",
                "available",
                "required",
                "unit_val",
                "notes",
                "manager_label",
            ]
        )
    res = (
        supabase_client.table("daily_audit_log")
        .select(cols)
        .order("id", desc=True)
        .limit(limit)
        .execute()
    )
    return pd.DataFrame(res.data or [])


def fetch_today_audit_rows(today_value: str, max_rows: int = 12000):
    cols = "log_date, log_time, item_name, available, required, unit_val, notes, manager_label"
    if supabase_client is None:
        return pd.DataFrame(columns=DAILY_LOG_HEADERS)
    res = (
        supabase_client.table("daily_audit_log")
        .select(cols)
        .eq("log_date", today_value)
        .order("id", desc=True)
        .limit(max_rows)
        .execute()
    )
    if not res.data:
        return pd.DataFrame(columns=DAILY_LOG_HEADERS)
    dfp = pd.DataFrame(res.data)
    dfp = dfp.rename(
        columns={
            "log_date": "التاريخ",
            "log_time": "الوقت",
            "item_name": "اسم السلعة",
            "available": "المتوفر",
            "required": "المطلوب",
            "unit_val": "الوحدة",
            "notes": "الملاحظات",
            "manager_label": "اسم المسؤول",
        }
    )
    return dfp[DAILY_LOG_HEADERS]


def clear_supabase_audit_log():
    if supabase_client is None:
        raise RuntimeError("Supabase غير متصل.")
    # حذف جميع الصفوف: شرط دائم صحيح على مفتاح تسلسلي
    supabase_client.table("daily_audit_log").delete().neq("id", 0).execute()


def format_admin_audit_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """أعمدة عربية للعرض: التاريخ، الوقت، المادة، اسم الخلفة، المسؤول، المتوفر، المطلوب، الوحدة، الملاحظات."""
    if df is None or df.empty:
        return pd.DataFrame(
            columns=[
                "التاريخ",
                "الوقت",
                "المادة",
                "اسم الخلفة",
                "المسؤول",
                "المتوفر",
                "المطلوب",
                "الوحدة",
                "الملاحظات",
            ]
        )

    def split_khallfa(label: str):
        s = str(label or "").strip()
        if " - " in s:
            shift_part, name_part = s.split(" - ", 1)
            return name_part.strip(), s
        return s, s

    kh_list, mgr_list = [], []
    for lbl in df["manager_label"].tolist():
        k, m = split_khallfa(lbl)
        kh_list.append(k)
        mgr_list.append(m)

    out = pd.DataFrame(
        {
            "التاريخ": df["log_date"].astype(str),
            "الوقت": df["log_time"].astype(str),
            "المادة": df["item_name"].astype(str),
            "اسم الخلفة": kh_list,
            "المسؤول": mgr_list,
            "المتوفر": df["available"],
            "المطلوب": df["required"],
            "الوحدة": df["unit_val"].astype(str).fillna(""),
            "الملاحظات": df["notes"].astype(str).fillna(""),
        }
    )
    return out


def build_audit_pdf_bytes(display_df: pd.DataFrame) -> bytes:
    """تقرير PDF بخط يدعم العربية (يتم تنزيل خط Noto عند أول استخدام)."""
    import arabic_reshaper
    from bidi.algorithm import get_display
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    font_dir = os.path.join(os.path.expanduser("~"), ".cache", "babalagha_inventory_fonts")
    os.makedirs(font_dir, exist_ok=True)
    font_path = os.path.join(font_dir, "NotoNaskhArabic-Regular.ttf")
    if not os.path.isfile(font_path) or os.path.getsize(font_path) < 5000:
        font_url = (
            "https://raw.githubusercontent.com/googlefonts/noto-naskh-arabic/main/"
            "fonts/ttf/hinted/ttf/NotoNaskhArabic-Regular.ttf"
        )
        r = requests.get(font_url, timeout=90)
        r.raise_for_status()
        with open(font_path, "wb") as f:
            f.write(r.content)

    pdfmetrics.registerFont(TTFont("NotoNaskh", font_path))

    buf = BytesIO()
    w, h = A4
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle("سجل الجرد")
    c.setFont("NotoNaskh", 13)
    title = get_display(arabic_reshaper.reshape("مخابز باب الآغا — سجل الجرد"))
    c.drawRightString(w - 36, h - 40, title)

    y = h - 62
    c.setFont("NotoNaskh", 8)
    if display_df is None or display_df.empty:
        line = get_display(arabic_reshaper.reshape("لا توجد سجلات."))
        c.drawRightString(w - 36, y, line)
        c.save()
        return buf.getvalue()

    for _, row in display_df.iterrows():
        parts = [str(row[col]) for col in display_df.columns]
        raw_line = "  |  ".join(parts)
        shaped = get_display(arabic_reshaper.reshape(raw_line))
        if len(shaped) > 95:
            shaped = shaped[:92] + "..."
        c.drawRightString(w - 36, y, shaped)
        y -= 11
        if y < 48:
            c.showPage()
            c.setFont("NotoNaskh", 8)
            y = h - 36
    c.save()
    return buf.getvalue()


def get_section_by_item(item_name: str) -> str:
    """إرجاع القسم المرتبط بالسلعة من جدول السلع في Supabase (المحفوظ في الجلسة)."""
    try:
        items_df = st.session_state.get("inventory_db", pd.DataFrame())
        if items_df.empty or "السلعة" not in items_df.columns or "القسم" not in items_df.columns:
            return "عام"
        match = items_df[items_df["السلعة"].astype(str) == str(item_name)]
        if match.empty:
            return "عام"
        return str(match.iloc[0].get("القسم", "عام") or "عام").strip() or "عام"
    except Exception:
        return "عام"

def normalize_log_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    توحيد أعمدة السجل مع DAILY_LOG_HEADERS مهما كان مصدر الملف
    (أقدم/أحدث/عدد أعمدة مختلف) بدون رمي أخطاء.
    """
    normalized = df.copy()

    # توحيد أي مسميات قديمة/بديلة عند العرض فقط
    display_aliases = {
        "اسم الخلفة": "الوحدة",
        "الخلفة": "الوحدة",
        "اسم المدير": "اسم المسؤول",
        "المسؤول": "اسم المسؤول",
    }
    normalized = normalized.rename(columns=display_aliases)

    # إذا كانت الأعمدة موجودة بالاسم الصحيح، نعتمدها بالاسم وليس بالموقع لتجنب الإزاحة
    if set(DAILY_LOG_HEADERS).issubset(set(normalized.columns)):
        return normalized[DAILY_LOG_HEADERS].fillna("")

    # fallback للملفات القديمة جداً: إعادة تعيين بالأماكن ثم إكمال النواقص
    if normalized.shape[1] > len(DAILY_LOG_HEADERS):
        normalized = normalized.iloc[:, :len(DAILY_LOG_HEADERS)]
    normalized.columns = DAILY_LOG_HEADERS[:normalized.shape[1]]
    for missing_col in DAILY_LOG_HEADERS[normalized.shape[1]:]:
        normalized[missing_col] = ""
    return normalized[DAILY_LOG_HEADERS].fillna("")

def validate_log_row_alignment(row):
    """
    فحص تحذيري لاكتشاف تبدّل الأعمدة قبل الحفظ.
    يرجع: (is_valid, message)
    """
    if len(row) != len(DAILY_LOG_HEADERS):
        return False, "عدد أعمدة الصف غير مطابق للهيدر المعتمد."

    unit_value = str(row[5] or "").strip()
    note_value = str(row[6] or "").strip()
    manager_value = str(row[7] or "").strip()

    # إن ظهرت وحدة قياس داخل اسم المسؤول، غالباً هناك إزاحة أعمدة
    known_units = {"قطعة", "عربانة", "كيس", "كيلو", "طاولي", "عجنه", "نص عجنه", "صندوق", "كارتون", "علبة"}
    if manager_value in known_units:
        return False, "تحذير: يبدو أن قيمة الوحدة انتقلت إلى عمود اسم المسؤول."

    # إذا كانت الملاحظة طويلة جداً واسم المسؤول فارغ، هذا مؤشر قوي على تبدّل موضع الاسم/الملاحظة
    if len(note_value) > 20 and not manager_value:
        return False, "تحذير: يوجد نص ملاحظات بدون اسم مسؤول؛ تحقق من ترتيب الأعمدة."

    # الوحدة يجب ألا تكون فارغة في السجل النهائي
    if not unit_value:
        return False, "تحذير: عمود الوحدة فارغ؛ يرجى اختيار الوحدة قبل الإرسال."

    return True, ""

def reset_inventory_inputs():
    """تصفير جميع مدخلات الجرد بعد الإرسال."""
    st.session_state.quantities = {}
    for key in list(st.session_state.keys()):
        if key.startswith(("q_", "r_", "u_", "n_")):
            del st.session_state[key]

def render_khallfa_results(today_value: str):
    st.write("---")
    st.subheader("📊 عرض نتائج اليوم حسب الخلفة")
    default_name = st.session_state.get("kh_input", "")
    if "khallfa_results_filter" not in st.session_state:
        st.session_state.khallfa_results_filter = default_name
    filter_col, refresh_col = st.columns([5, 1])
    with filter_col:
        khallfa_filter = st.text_input(
            "🔎 بحث ذكي (اسم الخلفة / اسم السلعة / القسم)",
            key="khallfa_results_filter"
        ).strip()
    with refresh_col:
        st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
        if st.button("🔄 تحديث", key="refresh_khallfa_results"):
            st.rerun()

    try:
        display_df = fetch_today_audit_rows(today_value)

        if display_df.empty:
            st.info("لا توجد بيانات مسجلة حالياً.")
            return

        display_df = normalize_log_columns(display_df)
        display_df.columns = DAILY_LOG_HEADERS
        today_rows = display_df[display_df["التاريخ"].astype(str).str.strip() == today_value].copy()

        today_rows["القسم"] = today_rows["اسم السلعة"].astype(str).apply(get_section_by_item)

        if khallfa_filter:
            khallfa_filter_norm = normalize_arabic_text(khallfa_filter)
            today_rows = today_rows[
                today_rows.apply(
                    lambda r: (
                        khallfa_filter_norm in normalize_arabic_text(r.get("اسم المسؤول", ""))
                        or khallfa_filter_norm in normalize_arabic_text(r.get("اسم السلعة", ""))
                        or khallfa_filter_norm in normalize_arabic_text(r.get("القسم", ""))
                    ),
                    axis=1
                )
            ]

        if today_rows.empty:
            st.info("لا توجد بيانات مطابقة لفلتر الخلفة لهذا اليوم.")
            return

        ordered_cols = KHALLFA_RESULTS_ORDER + ["القسم"]
        display_ordered = today_rows[ordered_cols].fillna("")

        def to_float_safe(value):
            try:
                return float(str(value).replace(",", "").strip())
            except Exception:
                return None

        html_rows = ""
        for _, row in display_ordered.iterrows():
            available_val = to_float_safe(row["المتوفر"])
            required_val = to_float_safe(row["المطلوب"])
            is_shortage = (
                available_val is not None
                and required_val is not None
                and available_val < required_val
            )
            row_style = "background-color:#ffe7e7;" if is_shortage else ""
            html_rows += (
                f"<tr style='{row_style}'>"
                + "".join([f"<td>{row[col]}</td>" for col in ordered_cols])
                + "</tr>"
            )

        results_table_html = f"""
        <div style="direction:rtl; text-align:right;">
            <table style="width:100%; border-collapse:collapse;">
                <thead>
                    <tr style="background:#f2f2f2;">
                        <th style="border:1px solid #ddd; padding:8px;">اسم السلعة</th>
                        <th style="border:1px solid #ddd; padding:8px;">المتوفر</th>
                        <th style="border:1px solid #ddd; padding:8px;">المطلوب</th>
                        <th style="border:1px solid #ddd; padding:8px;">الوحدة</th>
                        <th style="border:1px solid #ddd; padding:8px;">الملاحظات</th>
                        <th style="border:1px solid #ddd; padding:8px;">التاريخ</th>
                        <th style="border:1px solid #ddd; padding:8px;">الوقت</th>
                        <th style="border:1px solid #ddd; padding:8px;">اسم المسؤول</th>
                        <th style="border:1px solid #ddd; padding:8px;">القسم</th>
                    </tr>
                </thead>
                <tbody>
                    {html_rows}
                </tbody>
            </table>
            <p style="margin-top:8px; font-size:13px; color:#a94442;">الصف الأحمر يعني أن المتوفر أقل من المطلوب.</p>
        </div>
        """
        st.markdown(results_table_html, unsafe_allow_html=True)
    except Exception as e:
        st.warning(f"تعذر تحميل نتائج اليوم من Supabase: {e}")


def load_items():
    empty = pd.DataFrame(columns=["السلعة", "الحد_الأدنى", "الخلفة", "القسم", "الوحدة"])
    if supabase_client is None:
        return empty
    try:
        res = (
            supabase_client.table("inventory_items")
            .select("item_name, min_qty, unit_val, section_name, khallfa")
            .order("item_name")
            .execute()
        )
        if not res.data:
            return empty
        raw = pd.DataFrame(res.data)
        return raw.rename(
            columns={
                "item_name": "السلعة",
                "min_qty": "الحد_الأدنى",
                "unit_val": "الوحدة",
                "section_name": "القسم",
                "khallfa": "الخلفة",
            }
        )
    except Exception:
        return empty


init_supabase()

if "inventory_db" not in st.session_state:
    st.session_state.inventory_db = load_items()

# ميزة حفظ الكميات في الذاكرة لكي لا تضيع عند التنقل
if 'quantities' not in st.session_state:
    st.session_state.quantities = {}

# 4. محرك الوقت والشفت (مضبوط على توقيت بغداد عبر pytz)
baghdad_tz = pytz.timezone("Asia/Baghdad")
iraq_now = datetime.now(baghdad_tz)
current_hour = iraq_now.hour
time_now = iraq_now.strftime("%I:%M %p").replace("AM", "صباحاً").replace("PM", "مساءً")

if 6 <= current_hour < 15:
    current_shift = "الصباحي"
elif 15 <= current_hour < 23:
    current_shift = "المسائي"
else:
    current_shift = "الليلي"

# اسم المسؤول/الخلفة اليدوي (بدون أي قوائم أو أسماء ثابتة)
khallfa_on_duty = st.sidebar.text_input("👷 اسم المسؤول/الخلفة", key="kh_input").strip()
if khallfa_on_duty:
    st.session_state["khallfa_name"] = khallfa_on_duty

# 5. القائمة الجانبية
st.sidebar.title("🏢 إدارة باب الآغا")
st.sidebar.info(f"الشفت الحالي: {current_shift}")
menu = st.sidebar.radio(
    "انتقل إلى:",
    [
        "🏠 الرئيسية",
        "📦 الجرد والمخزون",
        "⚙️ إدارة السلع",
        "🔐 بوابة الإدارة (للمدير)",
        "🖨️ الطباعة والواتساب",
    ],
)

st.sidebar.write("---")
st.sidebar.subheader("🗑️ عمليات سريعة")
if st.sidebar.button("🧹 مسح جرد اليوم (تصفير)", key="sidebar_clear_today_qty"):
    st.session_state.quantities = {}
    st.sidebar.success("تم تصفير الأرقام بنجاح!")
    st.rerun()

# --- القسم 1: الرئيسية ---
if menu == "🏠 الرئيسية":
    # ابحث عن هذا السطر في قسم الرئيسية وعدله:
    st.write(f"اليوم هو {iraq_now.strftime('%Y-%m-%d')} والوقت الآن {time_now}")
    st.success(f"لديك حالياً {len(st.session_state.inventory_db)} سلعة مسجلة في النظام.")
    st.info("انتقل لقسم 'الجرد والمخزون' لبدء تسجيل أرقام اليوم.")

# --- القسم 2: الجرد والمخزون (هذا هو طلبك) ---
elif menu == "📦 الجرد والمخزون":
    st.header("📋 جدول الجرد اليومي")
    search = st.text_input("🔍 ابحث عن سلعة (صمون، توست...)...")
    
    # عرض السلع من قاعدة البيانات
    df = st.session_state.inventory_db
    if search:
        filtered_df = df[df["السلعة"].astype(str).str.contains(search, regex=False, na=False)]
    else:
        filtered_df = df

    inv_total = len(filtered_df)
    page_size = 80
    total_pages = max(1, (inv_total + page_size - 1) // page_size)
    if inv_total > page_size:
        st.caption(
            f"إجمالي {inv_total} سلعة — عرض {page_size} لكل صفحة. استخدم البحث لتقليل القائمة عند الحاجة."
        )
        page_num = st.number_input(
            "رقم الصفحة",
            min_value=1,
            max_value=total_pages,
            value=1,
            step=1,
            key="inventory_page",
        )
        start_idx = (int(page_num) - 1) * page_size
        page_df = filtered_df.iloc[start_idx : start_idx + page_size]
    else:
        page_df = filtered_df

    for index, row in page_df.iterrows():
        item_name = row['السلعة']
        unit = row['الوحدة']
        min_val = row['الحد_الأدنى']
        
        # إنشاء مفاتيح فريدة للذاكرة
        if item_name not in st.session_state.quantities:
            st.session_state.quantities[item_name] = {"qty": 0.0, "req": float(min_val), "note": "", "unit": unit}

        with st.expander(f"🔹 {item_name} ({unit})"):
            c1, c2, c3 = st.columns([1, 1, 2])
            with c1:
                # المتوفر
                st.session_state.quantities[item_name]['qty'] = st.number_input(f"المتوفر", value=st.session_state.quantities[item_name]['qty'], key=f"q_{item_name}")
            with c2:
                unit_options = ["قطعة", "عربانة", "كيس", "كيلو", "طاولي", "عجنه", "نص عجنه", "صندوق", "كارتون", "علبة"]
                current_unit = str(st.session_state.quantities[item_name].get("unit", unit) or unit)
                if current_unit not in unit_options:
                    unit_options = [current_unit] + unit_options
                selected_unit = st.selectbox(
                    "الوحدة",
                    unit_options,
                    index=unit_options.index(current_unit),
                    key=f"u_{item_name}"
                )
                st.session_state.quantities[item_name]["unit"] = selected_unit
                st.session_state.quantities[item_name]["req"] = st.number_input(
                    "المطلوب (الحد الأدنى)",
                    value=float(st.session_state.quantities[item_name].get("req", min_val) or min_val),
                    key=f"r_{item_name}"
                )
            with c3:
                # الملاحظة
                st.session_state.quantities[item_name]['note'] = st.text_input(f"ملاحظة", value=st.session_state.quantities[item_name]['note'], key=f"n_{item_name}")
            
            # نظام الألوان التلقائي
            if st.session_state.quantities[item_name]['qty'] <= min_val:
                st.error(f"❌ الكمية غير كافية (الحد الأدنى {min_val})")
            else:
                st.success("✅ الكمية كافية")
    # تثبيت جدول نتائج الخلفة لليوم أسفل صفحة الجرد
    render_khallfa_results(iraq_now.strftime('%Y-%m-%d'))

# --- القسم 3: إدارة السلع ---
elif menu == "⚙️ إدارة السلع":
    st.header("⚙️ التحكم في قائمة السلع")
    st.caption(
        "مسح سجل الجرد السحابي بالكامل يتم من قسم «بوابة الإدارة» مع التحقق المزدوج بكلمة المرور."
    )
    admin_items_password_ok = st.session_state.get("admin_items_password_ok", False)
    if not admin_items_password_ok:
        items_password = st.text_input("🔒 كلمة مرور إدارة السلع", type="password", key="items_admin_password")
        if st.button("تفعيل صلاحية إدارة السلع", key="unlock_items_admin"):
            if items_password == "57575656baba":
                st.session_state.admin_items_password_ok = True
                st.success("✅ تم تفعيل صلاحية إدارة السلع.")
                st.rerun()
            else:
                st.error("❌ كلمة المرور غير صحيحة.")
        st.info("أقسام إضافة/حذف السلع مخفية حتى إدخال كلمة المرور الصحيحة.")
    else:
        if st.button("🔒 إلغاء صلاحية الإدارة", key="lock_items_admin"):
            st.session_state.admin_items_password_ok = False
            st.success("تم إلغاء صلاحية الإدارة وإعادة القفل.")
            st.rerun()

        # 1. واجهة الإضافة (التي جربتها ونجحت)
        with st.expander("➕ إضافة سلعة جديدة"):
            with st.form("add_form"):
                name = st.text_input("اسم السلعة الجديدة")
                # إضافة قائمة منسدلة للوحدات بجانب كل مادة
                unit = st.selectbox("الوحدة", ["قطعة", "عربانة", "كيس", "كيلو", "طاولي", "عجنه", "نص عجنه", "صندوق", "كارتون", "علبة"], key="new_unit_selection")
                limit = st.number_input("الحد الأدنى", value=5.0)
                section_name = st.text_input("القسم", value="عام")
                if st.form_submit_button("حفظ السلعة"):
                    if name:
                        clean_section = str(section_name or "").strip() or "عام"
                        if supabase_client is None:
                            st.error("❌ Supabase غير متصل. تحقق من الأسرار والجداول.")
                        else:
                            try:
                                supabase_client.table("inventory_items").insert(
                                    {
                                        "item_name": str(name).strip(),
                                        "min_qty": float(limit),
                                        "unit_val": unit,
                                        "section_name": clean_section,
                                        "khallfa": "",
                                    }
                                ).execute()
                                st.session_state.inventory_db = load_items()
                                st.cache_data.clear()
                                st.success(f"✅ تم إضافة السلعة ({name}) بنجاح في Supabase")
                            except Exception as e:
                                st.error(f"❌ فشل حفظ السلعة في السحابة: {e}")
                        st.rerun()

        st.write("---")

        # 2. واجهة الحذف
        st.subheader("🗑️ حذف سلعة من النظام")
        del_search = st.text_input(
            "🔍 تصفية السلع قبل الاختيار (مهم عند وجود آلاف الأصناف)",
            key="delete_item_filter",
        )
        all_names = st.session_state.inventory_db["السلعة"].astype(str).tolist()
        if del_search.strip():
            q = del_search.strip()
            list_of_items = [n for n in all_names if q in n]
            if len(list_of_items) > 500:
                list_of_items = list_of_items[:500]
                st.caption("عرض أول 500 تطابق — اضيق عبارة البحث لرؤية المزيد بدقة.")
        else:
            list_of_items = all_names[:400]
            if len(all_names) > 400:
                st.caption(
                    f"عرض أول 400 سلعة من أصل {len(all_names)}. اكتب في خانة التصفية للعثور على سلعة محددة."
                )
        if not list_of_items:
            st.warning("لا توجد سلع مطابقة للتصفية الحالية.")
            item_to_delete = None
        else:
            item_to_delete = st.selectbox(
                "اختر السلعة التي تريد حذفها نهائياً:",
                list_of_items,
                key="delete_item_pick",
            )
        
        # زر الحذف مع تأكيد لونه أحمر
        if item_to_delete and st.button(f"❌ حذف سلعة ({item_to_delete}) الآن"):
            if supabase_client is None:
                st.error("❌ Supabase غير متصل.")
            else:
                try:
                    supabase_client.table("inventory_items").delete().eq("item_name", item_to_delete).execute()
                    st.session_state.inventory_db = load_items()
                    st.cache_data.clear()
                    st.error(f"⚠️ تم حذف {item_to_delete} من النظام بنجاح.")
                except Exception as e:
                    st.error(f"❌ فشل الحذف من السحابة: {e}")
            st.rerun()

elif menu == "🔐 بوابة الإدارة (للمدير)":
    st.header("🔐 بوابة الإدارة (للمدير)")
    if not st.session_state.get("admin_portal_unlocked"):
        apw = st.text_input("كلمة مرور بوابة الإدارة", type="password", key="admin_portal_gate_pw")
        if st.button("تأكيد الدخول", key="admin_portal_gate_btn"):
            if apw == "1948baba":
                st.session_state.admin_portal_unlocked = True
                st.success("تم الدخول.")
                st.rerun()
            else:
                st.error("كلمة المرور غير صحيحة.")
        st.info("هذا القسم للاطلاع على سجل الجرد المحفوظ في Supabase وتحميله أو مسحه بحرص شديد.")
    else:
        if st.button("🔒 خروج من بوابة الإدارة", key="admin_portal_gate_leave"):
            st.session_state.admin_portal_unlocked = False
            st.rerun()
        try:
            raw_audit = fetch_audit_log_for_admin()
            display_audit = format_admin_audit_dataframe(raw_audit)
            st.caption(
                f"عدد السجلات المعروضة: {len(display_audit)} — الوقت والتاريخ كما خُزّنا عند الإرسال (Asia/Baghdad)."
            )
            st.dataframe(display_audit, use_container_width=True, height=520)
            csv_payload = display_audit.to_csv(index=False).encode("utf-8-sig")
            st.download_button(
                label="📥 تحميل كملف Excel (CSV)",
                data=csv_payload,
                file_name="سجل_الجرد_المدير.csv",
                mime="text/csv",
                key="admin_audit_download_csv",
            )
            try:
                pdf_bytes = build_audit_pdf_bytes(display_audit)
                st.download_button(
                    label="📄 تحميل كملف PDF",
                    data=pdf_bytes,
                    file_name="سجل_الجرد_المدير.pdf",
                    mime="application/pdf",
                    key="admin_audit_download_pdf",
                )
            except Exception as pdf_err:
                st.warning(f"تعذر تجهيز ملف PDF: {pdf_err}")

            st.write("---")
            with st.expander("⚙️ خيارات متقدمة للمدير", expanded=False):
                st.caption("مسح السجل السحابي يتطلب إعادة إدخال كلمة المرور ثم تأكيداً صريحاً.")
                adv_pw = st.text_input(
                    "أعد إدخال كلمة مرور المدير لتفعيل زر المسح",
                    type="password",
                    key="admin_wipe_reauth_pw",
                )
                if adv_pw == "1948baba":
                    st.warning("سيُحذف كل سجل الجرد من جدول Supabase `daily_audit_log` نهائياً.")
                    wipe_ok = st.checkbox(
                        "أؤكد رغبتي في مسح كل السجلات السحابية نهائياً",
                        key="admin_wipe_confirm_chk",
                    )
                    st.markdown(
                        "<p style='color:#b91c1c;font-weight:700;margin:0.5rem 0;'>"
                        "🗑️ زر المسح النهائي (لا يمكن التراجع)</p>",
                        unsafe_allow_html=True,
                    )
                    if st.button(
                        "🗑️ مسح جميع السجلات في السحابة الآن",
                        type="secondary",
                        key="admin_wipe_all_btn",
                    ):
                        if wipe_ok:
                            try:
                                clear_supabase_audit_log()
                                st.cache_data.clear()
                                st.success("تم مسح سجل الجرد في Supabase.")
                                st.rerun()
                            except Exception as wipe_err:
                                st.error(f"تعذر إكمال المسح: {wipe_err}")
                        else:
                            st.error("فعّل خانة التأكيد أولاً.")
                elif adv_pw:
                    st.error("كلمة المرور غير صحيحة.")
        except Exception as admin_err:
            st.error(f"تعذر قراءة سجل الجرد من Supabase: {admin_err}")

elif menu == "🖨️ الطباعة والواتساب":
    st.header("🖨️ التقرير والواتساب")
    khallfa_on_duty = str(st.session_state.get("khallfa_name", "")).strip() or "لم يحدد"

    # 1. جلب البيانات المطلوبة
    ordered = {
        k: v for k, v in st.session_state.quantities.items()
        if float(v.get('qty', 0) or 0) > 0 or str(v.get('note', '')).strip()
    }

    render_khallfa_results(iraq_now.strftime('%Y-%m-%d'))

    if ordered:
        # 1. تعريف المتغيرات وتصفير القائمة
        inventory_data = []
        # جلب اسم الخلفة اليدوي المحفوظ في session_state فقط
        khallfa_on_duty = str(st.session_state.get("khallfa_name", "")).strip() or "غير محدد"
        
        # تجهيز رأس الرسالة
        wa_text = f"📋 *تقرير جرد وتجهيز - باب الآغا*\n"
        wa_text += f"👷 المسؤول/الخلفة: {khallfa_on_duty}\n"
        wa_text += f"🕒 الشفت: {current_shift}\n"
        wa_text += f"📅 {iraq_now.strftime('%Y-%m-%d')} | ⏰ {time_now}\n"
        wa_text += "--------------------------\n"

        for k, v in ordered.items():
            # أخذ الوحدة والمطلوب من اختيار الخلفة (مع fallback من قاعدة السلع عند الحاجة)
            item_info = st.session_state.inventory_db[st.session_state.inventory_db['السلعة'] == k]
            chosen_unit = str(v.get("unit", "") or "")
            required_qty = float(v.get("req", 0) or 0)
            if not chosen_unit and not item_info.empty:
                chosen_unit = str(item_info['الوحدة'].values[0])
            if required_qty == 0 and not item_info.empty:
                required_qty = float(item_info['الحد_الأدنى'].values[0])

            user_note = str(v.get("note", st.session_state.get(f"n_{k}", "")) or "").strip()

            # 3. بناء نص الواتساب (للتأكد من ظهوره هناك أيضاً)
            wa_text += f"🔹 {k}: {v['qty']} {chosen_unit} | ط: {required_qty}"
            if user_note:
                wa_text += f" [📝 {user_note}]"
            wa_text += "\n"

            # 4. جلب القسم
            section_name = item_info['القسم'].values[0] if not item_info.empty else "عام"

            # 5. تجهيز صف البيانات بنفس ترتيب أعمدة السجل المعتمدة (بدون إزاحة)
            row_date = iraq_now.strftime('%Y-%m-%d')
            row_time = time_now
            row_item = k
            row_available = float(v['qty'])
            row_required = required_qty
            row_unit = chosen_unit
            row_note = user_note
            full_manager_name = f"{current_shift} - {str(st.session_state.get('khallfa_name', '')).strip()}"
            inventory_data.append([
                row_date,          # 1 التاريخ
                row_time,          # 2 الوقت
                row_item,          # 3 اسم السلعة
                row_available,     # 4 المتوفر
                row_required,      # 5 المطلوب
                row_unit,          # 6 الوحدة
                row_note,          # 7 الملاحظات
                full_manager_name  # 8 اسم المسؤول
            ])

        # عرض معاينة نص التقرير
        st.text_area("📝 معاينة التقرير النهائي:", value=wa_text, height=200, disabled=True)
        # عرض معاينة الجدول قبل الإرسال النهائي لزيادة وضوح ترتيب الأعمدة
        preview_df = pd.DataFrame(inventory_data, columns=DAILY_LOG_HEADERS)
        preview_df["القسم"] = preview_df["اسم السلعة"].astype(str).apply(get_section_by_item)
        st.write("### 👀 معاينة الجدول قبل الإرسال")
        st.dataframe(
            preview_df[DAILY_LOG_HEADERS + ["القسم"]],
            use_container_width=True
        )

        # أزرار الإرسال
        col_wa, col_save = st.columns(2)
        with col_wa:
            encoded_msg = requests.utils.quote(wa_text)
            st.link_button("🟢 إرسال واتساب", f"https://wa.me/?text={encoded_msg}")
        with col_save:
            if st.button("✅ إرسال الجرد وحفظه في السجل"):
                if not str(st.session_state.get("khallfa_name", "")).strip():
                    st.error("يرجى كتابة اسم المسؤول/الخلفة أولاً قبل الإرسال.")
                    st.stop()
                if supabase_client is None:
                    st.error("❌ Supabase غير متصل. لا يمكن حفظ السجل السحابي.")
                    st.stop()
                invalid_messages = []
                for row in inventory_data:
                    is_valid, msg = validate_log_row_alignment(row)
                    if not is_valid:
                        invalid_messages.append(msg)
                if invalid_messages:
                    st.error("⚠️ تم اكتشاف خلل محتمل في ترتيب الأعمدة. الرجاء المراجعة قبل الإرسال.")
                    for warning_msg in sorted(set(invalid_messages)):
                        st.warning(warning_msg)
                    st.stop()
                save_df = pd.DataFrame(inventory_data, columns=DAILY_LOG_HEADERS)
                try:
                    append_log_rows_to_supabase(save_df)
                    st.success("✅ تم حفظ الجرد في Supabase بنجاح.")
                except Exception as e:
                    st.error(f"❌ فشل حفظ السجل في Supabase: {e}")
                    st.stop()
                reset_inventory_inputs()
                st.rerun()

            st.write("📲 **إرسال التقرير عبر الواتساب:**")
            st.caption("تمت إزالة قوائم الأسماء الثابتة من الكود.")

        # 2. بناء الجدول (تأكد من استخدام المتغيرات التي عرفتها في بداية الملف)
        table_html = ""
        for k, v in ordered.items():
            table_html += f"<tr><td>{k}</td><td>{v['qty']}</td><td>{v['req']}</td><td>{v.get('note', '')}</td></tr>"

        full_report = f"""
        <div class="print-container" style="direction:rtl; text-align:right; font-family:Arial; color:black; background:white; padding:20px;">
            <center>
                <h2 style="margin:0;">مخابز باب الآغا</h2>
                <p>التاريخ: {iraq_now.strftime('%Y-%m-%d')} | الوقت: {time_now}</p>
                <p><b>المسؤول/الخلفة: {khallfa_on_duty}</b></p>
                <p><b>الشفت: {current_shift}</b></p>
            </center>
            <table border="1" style="width:100%; border-collapse:collapse; text-align:center; color:black;">
                <tr style="background:#eee;">
                    <th>السلعة</th><th>المتوفر</th><th>المطلوب</th><th>ملاحظات</th>
                </tr>
                {table_html}
            </table>
            <div style="margin-top:30px; display:flex; justify-content:space-between;">
                <div>توقيع المسؤول/الخلفة ({khallfa_on_duty}): __________</div>
                <div>توقيع الشفت ({current_shift}): __________</div>
            </div>
        </div>
        """
        st.markdown(full_report, unsafe_allow_html=True)
    else:
        st.info("لا توجد بيانات للجرد حالياً.")

        st.write("---")
        st.subheader("🖼️ معاينة ورقة الطلبيات فقط")

        # التأكد من وجود بيانات وفلترة السلع المطلوبة فقط
        if 'quantities' in st.session_state and st.session_state.quantities:
            ordered_items = {item: data for item, data in st.session_state.quantities.items() if data.get('req', 0) > 0}

            if ordered_items:
                table_rows = "".join([
                    f"<tr><td style='text-align:right; padding:8px; border:1px solid black;'>{item}</td>"
                    f"<td style='border:1px solid black;'>{data.get('qty', 0)}</td>"
                    f"<td style='border:1px solid black;'>{data.get('req', 0)}</td>"
                    f"<td style='border:1px solid black;'>{get_section_by_item(item)}</td>"
                    f"<td style='border:1px solid black;'>{data.get('note', '')}</td></tr>" 
                    for item, data in ordered_items.items()
                ])
                
                report_html = f"""
                <div class="print-container" style="background:white; padding:30px; border:2px solid black; color:black; direction:rtl; font-family:'Cairo';">
                    <div style="text-align:center; border-bottom:2px solid black; margin-bottom:10px;">
                        <h2 style="margin:0;">مخابز باب الآغا</h2>
                        <h4 style="margin:5px;">قائمة الطلبيات المطلوبة فقط</h4>
                    </div>
                    <table style="width:100%; color:black; margin-bottom:10px; font-size:14px;">
                        <tr>
                            <td><b>التاريخ:</b> {iraq_now.strftime('%Y-%m-%d')}</td>
                            <td style="text-align:left;"><b>المسؤول/الخلفة:</b> {khallfa_on_duty}</td>
                        </tr>
                        <tr>
                            <td><b>الوقت:</b> {time_now}</td>
                            <td style="text-align:left;"><b>الشفت:</b> {current_shift}</td>
                        </tr>
                    </table>
                    <table border="1" style="width:100%; border-collapse:collapse; text-align:center; color:black; border:1px solid black;">
                        <tr style="background-color:#eeeeee;">
                            <th style="padding:10px; border:1px solid black;">السلعة</th>
                            <th style="border:1px solid black;">المتوفر</th>
                            <th style="border:1px solid black;">المطلوب</th>
                            <th style="border:1px solid black;">القسم</th>
                            <th style="border:1px solid black;">الملاحظات</th>
                        </tr>
                        {table_rows}
                    </table>
                    <div style="display:flex; justify-content:space-around; text-align:center; margin-top:30px; color:black;">
                        <div style="width:40%;"><b>توقيع المسؤول/الخلفة</b><br>({khallfa_on_duty})<br><br>__________</div>
                        <div style="width:40%;"><b>توقيع الشفت</b><br>({current_shift})<br><br>__________</div>
                    </div>
                </div>
                """
                st.markdown(report_html, unsafe_allow_html=True)
            else:
                st.info("💡 قائمة المعاينة فارغة.. السلع التي تطلبها ستظهر هنا فوراً.")
        else:
            st.warning("⚠️ يرجى البدء بإدخال الجرد من قسم الجرد والمخزون أولاً.")

# نهاية الملف
