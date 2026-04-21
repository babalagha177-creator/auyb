import requests
import streamlit as st
import pytz
from datetime import datetime
import pandas as pd
import os
import gspread
from oauth2client.service_account import ServiceAccountCredentials

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

def get_log_worksheet(spreadsheet_obj):
    """يفتح ورقة Log أولاً، ثم Daily_Log كخيار احتياطي."""
    for sheet_name in ["Log", "Daily_Log"]:
        try:
            return spreadsheet_obj.worksheet(sheet_name)
        except Exception:
            continue
    raise ValueError("تعذر العثور على ورقة Log أو Daily_Log")

# إعداد الربط مع جوجل شيت
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
try:
    creds = ServiceAccountCredentials.from_json_keyfile_name("key.json", scope)
    client = gspread.authorize(creds)
    
    # فتح الجدول بالرابط المباشر
    spreadsheet = client.open_by_url("https://docs.google.com/spreadsheets/d/1OmmOv6D0jcxMxcSlPlfyH9-RgQoijsLrr4g34aZDeDg/edit")
    
    # تعريف الورقة الأولى (للسلع) والورقة الثانية (للجرد)
    sheet = spreadsheet.get_worksheet(0) # الورقة الأولى
    daily_sheet = get_log_worksheet(spreadsheet) # ورقة السجل: Log أو Daily_Log
    
    st.sidebar.success("✅ تم الاتصال بكافة الأوراق")
except Exception as e:
    st.sidebar.error(f"❌ خطأ في الربط: {e}")
# 1. إعدادات الصفحة
st.set_page_config(page_title="نظام باب الآغا", layout="wide")

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

# 3. إدارة قاعدة البيانات
DB_FILE = "inventory_list.csv"
DAILY_LOG_FILE = "Daily_Log.csv"
DAILY_LOG_HEADERS = ['التاريخ', 'الوقت', 'اسم السلعة', 'المتوفر', 'المطلوب', 'الوحدة', 'الملاحظات', 'اسم المسؤول']
# ترتيب عرض جدول الخلفة (يمين -> يسار) مع الحفاظ على نفس أسماء الأعمدة الرسمية
KHALLFA_RESULTS_ORDER = ["اسم السلعة", "المتوفر", "المطلوب", "الوحدة", "الملاحظات", "التاريخ", "الوقت", "اسم المسؤول"]
daily_log_reset_message = None
st.cache_data.clear()

def get_section_by_item(item_name: str) -> str:
    """إرجاع القسم المرتبط بالسلعة من قاعدة السلع المحلية."""
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

def ensure_daily_log_file():
    global daily_log_reset_message
    if not os.path.exists(DAILY_LOG_FILE):
        pd.DataFrame(columns=DAILY_LOG_HEADERS).to_csv(DAILY_LOG_FILE, index=False, encoding="utf-8-sig")
        return
    try:
        existing_df = pd.read_csv(DAILY_LOG_FILE)
        if list(existing_df.columns) != DAILY_LOG_HEADERS:
            # إذا كان الملف القديم بعناوين خاطئة: ننشئ نسخة احتياطية ونبدأ ملفاً جديداً صحيحاً
            backup_name = f"Daily_Log_legacy_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            os.replace(DAILY_LOG_FILE, backup_name)
            pd.DataFrame(columns=DAILY_LOG_HEADERS).to_csv(DAILY_LOG_FILE, index=False, encoding="utf-8-sig")
            daily_log_reset_message = (
                f"تم إنشاء ملف سجل جديد بالعناوين الصحيحة. "
                f"تم حفظ الملف القديم كنسخة احتياطية: {backup_name}"
            )
    except Exception:
        pd.DataFrame(columns=DAILY_LOG_HEADERS).to_csv(DAILY_LOG_FILE, index=False, encoding="utf-8-sig")

def normalize_log_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    توحيد أعمدة السجل مع DAILY_LOG_HEADERS مهما كان مصدر الملف
    (أقدم/أحدث/عدد أعمدة مختلف) بدون رمي أخطاء.
    """
    normalized = df.copy()

    # توحيد أي مسميات قديمة/بديلة عند العرض فقط (لا يغيّر ملف CSV على القرص)
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
        if os.path.exists(DAILY_LOG_FILE):
            display_df = pd.read_csv(DAILY_LOG_FILE)
        else:
            all_rows = daily_sheet.get_all_records()
            display_df = pd.DataFrame(all_rows)

        if display_df.empty:
            st.info("لا توجد بيانات مسجلة حالياً.")
            return

        display_df = normalize_log_columns(display_df)
        # تثبيت أسماء الأعمدة بشكل صريح حتى لو المصدر قديم أو مشوّه
        display_df.columns = DAILY_LOG_HEADERS
        today_rows = display_df[display_df["التاريخ"].astype(str).str.strip() == today_value]

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
        st.warning(f"تعذر تحميل نتائج اليوم من Google Sheet: {e}")

def load_items():
    if os.path.exists(DB_FILE):
        try:
            df = pd.read_csv(DB_FILE)
            if df.empty:
                return pd.DataFrame(columns=["السلعة", "الحد_الأدنى", "الخلفة", "القسم", "الوحدة"])
            return df
        except Exception:
            return pd.DataFrame(columns=["السلعة", "الحد_الأدنى", "الخلفة", "القسم", "الوحدة"])
    return pd.DataFrame(columns=["السلعة", "الحد_الأدنى", "الخلفة", "القسم", "الوحدة"])

if 'inventory_db' not in st.session_state:
    st.session_state.inventory_db = load_items()
ensure_daily_log_file()
if daily_log_reset_message:
    st.sidebar.warning(daily_log_reset_message)

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
menu = st.sidebar.radio("انتقل إلى:", ["🏠 الرئيسية", "📦 الجرد والمخزون", "⚙️ إدارة السلع", "🖨️ الطباعة والواتساب"])

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
    filtered_df = df[df['السلعة'].str.contains(search)] if search else df

    for index, row in filtered_df.iterrows():
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
# --- زر تفريغ البيانات (تضعه هنا بالضبط) ---
st.sidebar.write("---") # خط فاصل للتنسيق
st.sidebar.subheader("🗑️ عمليات سريعة")
if st.sidebar.button("🧹 مسح جرد اليوم (تصفير)"):
    # هذا الأمر يفرغ كل الأرقام والملاحظات التي أدخلتها
    st.session_state.quantities = {}
    st.sidebar.success("تم تصفير الأرقام بنجاح!")
    st.rerun() # لإعادة إنعاش الصفحة فوراً
# --- القسم 3: إدارة السلع ---
if menu == "⚙️ إدارة السلع":
    st.header("⚙️ التحكم في قائمة السلع")
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
                        # 1. الحفظ المحلي في جهازك
                        clean_section = str(section_name or "").strip() or "عام"
                        new_data = pd.DataFrame([{"القسم": clean_section, "الخلفة": "", "الحد_الأدنى": limit, "السلعة": name, "الوحدة": unit}])
                        st.session_state.inventory_db = pd.concat([st.session_state.inventory_db, new_data], ignore_index=True)
                        st.session_state.inventory_db.to_csv(DB_FILE, index=False)
                        st.cache_data.clear()
                        
                        # 2. الحفظ السحابي مع توجيه القسم إلى العمود I (الخانة 9)
                        try:
                            # A..I مع وضع "القسم" في I فقط وعدم المساس بأعمدة A..H
                            manager_row = [iraq_now.strftime('%Y-%m-%d'), time_now, name, 0, limit, "إضافة جديدة", "", "", clean_section]
                            
                            # إدخال السطر في ورقة السلع (Sheet1)
                            sheet.insert_row(manager_row, 2)
                            
                            st.success(f"✅ تم إضافة السلعة ({name}) بنجاح إلى النظام وقاعدة البيانات السحابية")
                        except Exception as e:
                            st.error(f"❌ الاتصال نجح لكن الإرسال فشل: {e}")
                        
                        st.rerun()

        st.write("---")

        # 2. واجهة الحذف
        st.subheader("🗑️ حذف سلعة من النظام")
        # نضع قائمة منسدلة بأسماء كل السلع الموجودة حالياً
        list_of_items = st.session_state.inventory_db['السلعة'].tolist()
        
        item_to_delete = st.selectbox("اختر السلعة التي تريد حذفها نهائياً:", list_of_items)
        
        # زر الحذف مع تأكيد لونه أحمر
        if st.button(f"❌ حذف سلعة ({item_to_delete}) الآن"):
            # نقوم بفلترة البيانات ونأخذ كل شيء "عدا" السلعة المختارة
            st.session_state.inventory_db = st.session_state.inventory_db[st.session_state.inventory_db['السلعة'] != item_to_delete]
            
            # حفظ التغيير في الملف فوراً لكي لا تعود السلعة مرة أخرى
            st.session_state.inventory_db.to_csv(DB_FILE, index=False)
            st.cache_data.clear()
            
            st.error(f"⚠️ تم حذف {item_to_delete} من النظام بنجاح.")
            st.rerun() # لتحديث القائمة وإخفائها من الجرد فوراً

    st.write("---")
    st.subheader("🔐 إعدادات المدير")
    with st.expander("🧨 إعادة تهيئة سجل Daily_Log (للمدير فقط)"):
        reset_password = st.text_input("كلمة مرور المدير", type="password", key="manager_reset_password")
        if reset_password == "1948baba":
            st.warning("سيتم حذف ملف Daily_Log.csv المحلي فقط. لن يتم تعديل Google Sheets.")
            confirm_reset = st.checkbox("نعم، أنا متأكد من حذف سجل Daily_Log المحلي", key="confirm_daily_log_reset")
            if st.button("✅ تنفيذ إعادة التهيئة الآن", key="do_daily_log_reset"):
                if confirm_reset:
                    try:
                        if os.path.exists(DAILY_LOG_FILE):
                            os.remove(DAILY_LOG_FILE)
                    except Exception:
                        pass
                    # fallback مضمون: إنشاء ملف جديد بهيدر نظيف حتى لو فشل الحذف
                    pd.DataFrame(columns=DAILY_LOG_HEADERS).to_csv(
                        DAILY_LOG_FILE,
                        index=False,
                        encoding="utf-8-sig"
                    )
                    st.cache_data.clear()
                    st.session_state.clear()
                    st.success("تمت إعادة تهيئة ملف Daily_Log.csv المحلي بنجاح.")
                    st.rerun()
                else:
                    st.error("يرجى تفعيل خيار التأكيد قبل تنفيذ المسح.")
        elif reset_password:
            st.error("كلمة المرور غير صحيحة.")
            # --- القسم 4: الطباعة والواتساب (التحديث النهائي) ---
if menu == "🖨️ الطباعة والواتساب":
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

            # 5. تجهيز صف البيانات بنفس ترتيب هيدر Daily_Log.csv (بدون أي إزاحة)
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
        col_wa, col_gs = st.columns(2)
        with col_wa:
            # استخدام wa_text المحدث الذي يحتوي على الملاحظات
            encoded_msg = requests.utils.quote(wa_text)
            st.link_button("🟢 إرسال واتساب", f"https://wa.me/?text={encoded_msg}")
        with col_gs:
            if st.button("✅ إرسال الجرد وحفظه في السجل"):
                if not str(st.session_state.get("khallfa_name", "")).strip():
                    st.error("يرجى كتابة اسم المسؤول/الخلفة أولاً قبل الإرسال.")
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
                # حفظ محلي متوافق مع Daily_Log.csv
                save_df = pd.DataFrame(inventory_data, columns=DAILY_LOG_HEADERS)
                save_df.to_csv(
                    DAILY_LOG_FILE,
                    mode="a",
                    index=False,
                    header=not os.path.exists(DAILY_LOG_FILE) or os.path.getsize(DAILY_LOG_FILE) == 0,
                    encoding="utf-8-sig"
                )

                daily_sheet = get_log_worksheet(spreadsheet)
                # ترتيب Google Sheets المطلوب:
                # A التاريخ | B الوقت | C اسم السلعة | D المتوفر | E المطلوب | F اسم المسؤول | G الملاحظات | H الوحدة
                gs_rows = []
                for k, v in ordered.items():
                    item_info = st.session_state.inventory_db[st.session_state.inventory_db['السلعة'] == k]
                    unit_value = str(v.get("unit", "") or "")
                    required_qty = float(v.get("req", 0) or 0)
                    if not unit_value and not item_info.empty:
                        unit_value = str(item_info['الوحدة'].values[0])
                    if required_qty == 0 and not item_info.empty:
                        required_qty = float(item_info['الحد_الأدنى'].values[0])

                    available_qty = float(v.get("qty", 0) or 0)
                    item_name = k
                    current_date = iraq_now.strftime('%Y-%m-%d')
                    current_time = time_now
                    user_note = str(v.get("note", st.session_state.get(f"n_{k}", "")) or "").strip()
                    full_manager_name = f"{current_shift} - {str(st.session_state.get('khallfa_name', '')).strip()}"
                    section_name = str(item_info['القسم'].values[0]).strip() if (not item_info.empty and 'القسم' in item_info.columns) else "عام"
                    gs_rows.append([
                        current_date,
                        current_time,
                        item_name,
                        available_qty,
                        required_qty,
                        full_manager_name,
                        user_note,
                        unit_value,
                        section_name
                    ])
                daily_sheet.append_rows(gs_rows, value_input_option="USER_ENTERED")
                st.success("✅ تم الإرسال بنجاح!")
                reset_inventory_inputs()
                st.rerun()
            
            import urllib.parse
            encoded_msg = urllib.parse.quote(wa_text)

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
                <p>التاريخ: {datetime.now().strftime('%Y-%m-%d')} | الوقت: {time_now}</p>
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
                            <td><b>التاريخ:</b> {datetime.now().strftime('%Y-%m-%d')}</td>
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
