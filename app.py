import streamlit as st
from datetime import datetime
import pandas as pd
import os

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

def load_items():
    if os.path.exists(DB_FILE):
        return pd.read_csv(DB_FILE)
    return pd.DataFrame([{"السلعة": "توست أبيض", "الوحدة": "قطعة", "الحد الأدنى": 10.0}])

if 'inventory_db' not in st.session_state:
    st.session_state.inventory_db = load_items()

# ميزة حفظ الكميات في الذاكرة لكي لا تضيع عند التنقل
if 'quantities' not in st.session_state:
    st.session_state.quantities = {}

# 4. محرك الوقت والشفت
current_hour = datetime.now().hour
time_now = datetime.now().strftime("%I:%M %p").replace("AM", "صباحاً").replace("PM", "مساءً")

if 6 <= current_hour < 15:
    current_shift, shift_manager = "الصباحي", "أيوب هاني"
elif 15 <= current_hour < 23:
    current_shift, shift_manager = "المسائي", "مصطفى عمار"
else:
    current_shift, shift_manager = "الليلي", "طلحة"
    # تحديد اسم الكابتن بناءً على الشفت (وضعه هنا ليراه النظام في كل الصفحات)
if current_shift == "الصباحي":
    captain_name = "علي كريم"
elif current_shift == "المسائي":
    captain_name = "عبد الله عامر"
else:
    captain_name = "أحمد مجبل"

# 5. القائمة الجانبية
st.sidebar.title("🏢 إدارة باب الآغا")
st.sidebar.info(f"المسؤول: {shift_manager}\n\nالشفت: {current_shift}")
menu = st.sidebar.radio("انتقل إلى:", ["🏠 الرئيسية", "📦 الجرد والمخزون", "⚙️ إدارة السلع", "🖨️ الطباعة والواتساب"])

# --- القسم 1: الرئيسية ---
if menu == "🏠 الرئيسية":
    st.markdown(f"<h1 style='color: #D4AF37;'>مرحباً بك يا {shift_manager}</h1>", unsafe_allow_html=True)
    st.write(f"اليوم هو {datetime.now().strftime('%Y-%m-%d')} والوقت الآن {time_now}")
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
        min_val = row['الحد الأدنى']
        
        # إنشاء مفاتيح فريدة للذاكرة
        if item_name not in st.session_state.quantities:
            st.session_state.quantities[item_name] = {"qty": 0.0, "req": 0.0, "note": ""}

        with st.expander(f"🔹 {item_name} ({unit})"):
            c1, c2, c3 = st.columns([1, 1, 2])
            with c1:
                st.session_state.quantities[item_name]['qty'] = st.number_input(f"المتوفر", value=st.session_state.quantities[item_name]['qty'], key=f"q_{item_name}")
            with c2:
                st.session_state.quantities[item_name]['req'] = st.number_input(f"المطلوب", value=st.session_state.quantities[item_name]['req'], key=f"r_{item_name}")
            with c3:
                st.session_state.quantities[item_name]['note'] = st.text_input(f"ملاحظة", value=st.session_state.quantities[item_name]['note'], key=f"n_{item_name}")
            
            # نظام الألوان التلقائي
            if st.session_state.quantities[item_name]['qty'] <= min_val:
                st.error(f"❌ الكمية غير كافية (الحد الأدنى {min_val})")
            else:
                st.success("✅ الكمية كافية")
# --- زر تفريغ البيانات (تضعه هنا بالضبط) ---
st.sidebar.write("---") # خط فاصل للتنسيق
st.sidebar.subheader("🗑️ عمليات سريعة")
if st.sidebar.button("🧹 مسح جرد اليوم (تصفير)"):
    # هذا الأمر يفرغ كل الأرقام والملاحظات التي أدخلتها
    st.session_state.quantities = {}
    st.sidebar.success("تم تصفير الأرقام بنجاح!")
    st.rerun() # لإعادة إنعاش الصفحة فوراً
# --- القسم 3: إدارة السلع ---
elif menu == "⚙️ إدارة السلع":
    st.header("⚙️ التحكم في قائمة السلع")
    
    # 1. واجهة الإضافة (التي جربتها ونجحت)
    with st.expander("➕ إضافة سلعة جديدة"):
        with st.form("add_form"):
            name = st.text_input("اسم السلعة الجديدة")
            unit = st.selectbox("الوحدة", ["قطعة", "عربانة", "طاوولي", "صندوق", "عجنة", "نص عجنة", "كيس", "كغم"])
            limit = st.number_input("الحد الأدنى", value=5.0)
            if st.form_submit_button("حفظ السلعة"):
                if name:
                    new_data = pd.DataFrame([{"السلعة": name, "الوحدة": unit, "الحد الأدنى": limit}])
                    st.session_state.inventory_db = pd.concat([st.session_state.inventory_db, new_data], ignore_index=True)
                    st.session_state.inventory_db.to_csv(DB_FILE, index=False)
                    st.success(f"✅ تم إضافة {name} بنجاح!")
                    st.rerun() # لإعادة تحديث القائمة فوراً

    st.write("---")

    # 2. واجهة الحذف (طلبك الجديد)
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
        
        st.error(f"⚠️ تم حذف {item_to_delete} من النظام بنجاح.")
        st.rerun() # لتحديث القائمة وإخفائها من الجرد فوراً
            # --- القسم 4: الطباعة والواتساب (التحديث النهائي) ---
elif menu == "🖨️ الطباعة والواتساب":
    st.header("🖨️ التقرير والواتساب")

    # 1. جلب البيانات المطلوبة
    ordered = {k: v for k, v in st.session_state.quantities.items() if v.get('req', 0) > 0}

    if ordered:
        col1, col2 = st.columns(2)
        with col1:
            if st.button("📄 تشغيل الطباعة"):
                st.markdown('<script>window.print();</script>', unsafe_allow_html=True)
        
        with col2:
            # 1. تجهيز نص الرسالة الشامل
            wa_text = f"📋 *تقرير جرد مخابز باب الآغا*\n"
            wa_text += f"📅 التاريخ: {datetime.now().strftime('%Y-%m-%d')}\n"
            wa_text += f"👤 المسؤول: {shift_manager}\n"
            wa_text += f"👮 الكابتن: {captain_name}\n"
            wa_text += f"⏰ الوقت: {time_now}\n"
            wa_text += "--------------------------\n"
            
            for k, v in ordered.items():
                wa_text += f"🔹 {k}: (م:{v['qty']} | ط:{v['req']})\n"
                # إضافة الملاحظة إذا وجدت
                if v.get('note') and v['note'].strip() != "":
                    wa_text += f"   📝 ملاحظة: {v['note']}\n"
            
            import urllib.parse
            encoded_msg = urllib.parse.quote(wa_text)

            st.write("📲 **إرسال التقرير عبر الواتساب:**")

            # --- زر المدير (ثابت في كل الأوقات) ---
            url_manager = f"https://wa.me/9647824950417?text={encoded_msg}"
            st.markdown(f'<a href="{url_manager}" target="_blank"><button style="width:100%; margin-bottom:10px; height:45px; background-color:#D4AF37; color:black; border:none; border-radius:8px; font-weight:bold; cursor:pointer;">👑 المدير: علي رشيد</button></a>', unsafe_allow_html=True)

            # --- أزرار الشفت الصباحي ---
            if current_shift == "الصباحي":
                st.info("☀️ فريق الشفت الصباحي")
                # كابتن صباحي
                st.markdown(f'<a href="https://wa.me/9647828117429?text={encoded_msg}" target="_blank"><button style="width:100%; margin-bottom:5px; height:35px; background-color:#25D366; color:white; border:none; border-radius:8px; cursor:pointer;">👮 كابتن: علي كريم</button></a>', unsafe_allow_html=True)
                # خلفات صباحي
                st.markdown(f'<a href="https://wa.me/9647700537052?text={encoded_msg}" target="_blank"><button style="width:100%; margin-bottom:5px; height:35px; background-color:#128C7E; color:white; border:none; border-radius:8px; cursor:pointer;">👷 خلفة: حيدر السويدي</button></a>', unsafe_allow_html=True)
                st.markdown(f'<a href="https://wa.me/9647700798530?text={encoded_msg}" target="_blank"><button style="width:100%; margin-bottom:5px; height:35px; background-color:#128C7E; color:white; border:none; border-radius:8px; cursor:pointer;">👷 خلفة: مؤمن</button></a>', unsafe_allow_html=True)
                st.markdown(f'<a href="https://wa.me/9647762238233?text={encoded_msg}" target="_blank"><button style="width:100%; margin-bottom:5px; height:35px; background-color:#128C7E; color:white; border:none; border-radius:8px; cursor:pointer;">👷 خلفة: سيف لوز</button></a>', unsafe_allow_html=True)
                st.markdown(f'<a href="https://wa.me/9647804609224?text={encoded_msg}" target="_blank"><button style="width:100%; margin-bottom:5px; height:35px; background-color:#128C7E; color:white; border:none; border-radius:8px; cursor:pointer;">🍞 مسؤول توست: عباس</button></a>', unsafe_allow_html=True)
                st.markdown(f'<a href="https://wa.me/9647705413482?text={encoded_msg}" target="_blank"><button style="width:100%; margin-bottom:5px; height:35px; background-color:#128C7E; color:white; border:none; border-radius:8px; cursor:pointer;">👷 خلفة: مينا</button></a>', unsafe_allow_html=True)

            # --- أزرار الشفت المسائي ---
            elif current_shift == "المسائي":
                st.warning("🌙 فريق الشفت المسائي")
                # كابتن مسائي
                st.markdown(f'<a href="https://wa.me/9647718116535?text={encoded_msg}" target="_blank"><button style="width:100%; margin-bottom:5px; height:35px; background-color:#25D366; color:white; border:none; border-radius:8px; cursor:pointer;">👮 كابتن: عبدالله عامر</button></a>', unsafe_allow_html=True)
                # خلفة مسائي (خالد - أضفت لك رقمه إذا توفر لاحقاً)
                st.button("👷 خلفة: خالد (لا يوجد رقم حالياً)")
        # 2. بناء الجدول (تأكد من استخدام المتغيرات التي عرفتها في بداية الملف)
        table_html = ""
        for k, v in ordered.items():
            table_html += f"<tr><td>{k}</td><td>{v['qty']}</td><td>{v['req']}</td><td>{v.get('note', '')}</td></tr>"

        full_report = f"""
        <div class="print-container" style="direction:rtl; text-align:right; font-family:Arial; color:black; background:white; padding:20px;">
            <center>
                <h2 style="margin:0;">مخابز باب الآغا</h2>
                <p>التاريخ: {datetime.now().strftime('%Y-%m-%d')} | الوقت: {time_now}</p>
                <p><b>المسؤول الحالي: {shift_manager}</b></p>
                <p><b>الكابتن: {captain_name}</b></p>
            </center>
            <table border="1" style="width:100%; border-collapse:collapse; text-align:center; color:black;">
                <tr style="background:#eee;">
                    <th>السلعة</th><th>المتوفر</th><th>المطلوب</th><th>ملاحظات</th>
                </tr>
                {table_html}
            </table>
            <div style="margin-top:30px; display:flex; justify-content:space-between;">
                <div>توقيع المسؤول ({shift_manager}): __________</div>
                <div>توقيع الكابتن ({captain_name}): __________</div>
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
                            <td style="text-align:left;"><b>المسؤول:</b> {shift_manager}</td>
                        </tr>
                        <tr>
                            <td><b>الوقت:</b> {time_now}</td>
                            <td style="text-align:left;"><b>الكابتن:</b> {captain_name}</td>
                        </tr>
                    </table>
                    <table border="1" style="width:100%; border-collapse:collapse; text-align:center; color:black; border:1px solid black;">
                        <tr style="background-color:#eeeeee;">
                            <th style="padding:10px; border:1px solid black;">السلعة</th>
                            <th style="border:1px solid black;">المتوفر</th>
                            <th style="border:1px solid black;">المطلوب</th>
                            <th style="border:1px solid black;">الملاحظات</th>
                        </tr>
                        {table_rows}
                    </table>
                    <div style="display:flex; justify-content:space-around; text-align:center; margin-top:30px; color:black;">
                        <div style="width:40%;"><b>توقيع المسؤول</b><br>({shift_manager})<br><br>__________</div>
                        <div style="width:40%;"><b>توقيع الكابتن</b><br>({captain_name})<br><br>__________</div>
                    </div>
                </div>
                """
                st.markdown(report_html, unsafe_allow_html=True)
            else:
                st.info("💡 قائمة المعاينة فارغة.. السلع التي تطلبها ستظهر هنا فوراً.")
        else:
            st.warning("⚠️ يرجى البدء بإدخال الجرد من قسم الجرد والمخزون أولاً.")

# نهاية الملف