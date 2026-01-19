import streamlit as st
import os
import glob
import pandas as pd
from datetime import datetime, timedelta
from pypdf import PdfReader
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import time

# --- 1. 系統設定 ---
st.set_page_config(page_title="創傷知情 AI 實作教練 (研究版)", layout="wide")

# --- 0. 檢查是否剛登出 (放在最前面攔截) ---
if st.session_state.get("logout_triggered"):
    st.markdown("## ✅ 已成功登出")
    st.success("您的諮詢紀錄已安全上傳至雲端。感謝您的參與！")
    st.write("如果您需要再次諮詢，請點擊下方按鈕。")
    
    if st.button("🔄 重新登入"):
        st.session_state.logout_triggered = False
        st.rerun()
    st.stop()

# --- Google Sheets 上傳函式 (Coach 專用版) ---
def save_to_google_sheets(user_id, chat_history, grade, lang):
    try:
        # 1. 連線與設定
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds_dict = st.secrets["gcp_service_account"]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        
        sheet = client.open("2025創傷知情研習數據")
        # ⚠️ 注意：資料會寫入 'Coach' 分頁，請確認試算表已有此分頁
        worksheet = sheet.worksheet("Coach")
        
        # 2. 時間計算 (校正為台灣時間 UTC+8)
        tw_fix = timedelta(hours=8)
        
        # A. 取得登入時間
        start_t = st.session_state.get('start_time', datetime.now())
        login_str = (start_t + tw_fix).strftime("%Y-%m-%d %H:%M:%S")
        
        # B. 取得登出時間
        end_t = datetime.now()
        logout_str = (end_t + tw_fix).strftime("%Y-%m-%d %H:%M:%S")
        
        # C. 計算使用分鐘數
        duration_mins = round((end_t - start_t).total_seconds() / 60, 2)
        
        # D. 計算累積次數 (讀取 C 欄「學員編號」)
        try:
            all_ids = worksheet.col_values(3) 
            login_count = all_ids.count(user_id) + 1
        except:
            login_count = 1

        # 3. 整理對話內容
        # 記錄「諮詢年級」與「語言」作為情境參數
        context_info = f"諮詢對象年級: {grade} / 使用語言: {lang}"
        
        full_conversation = f"【設定參數】：{context_info}\n\n"
        for msg in chat_history:
            role = msg.get("role", "Unknown")
            content = ""
            if "parts" in msg:
                content = msg["parts"][0] if isinstance(msg["parts"], list) else str(msg["parts"])
            elif "content" in msg:
                content = msg["content"]
            full_conversation += f"[{role}]: {content}\n"

        # 4. 寫入六大欄位
        worksheet.append_row([
            login_str, 
            logout_str, 
            user_id, 
            duration_mins, 
            login_count, 
            full_conversation
        ])
        return True
    except Exception as e:
        st.error(f"上傳失敗: {e}")
        return False

# 初始化 Session State
if "history" not in st.session_state: st.session_state.history = []
if "loaded_text" not in st.session_state: st.session_state.loaded_text = ""
if "user_nickname" not in st.session_state: st.session_state.user_nickname = ""
if "start_time" not in st.session_state: st.session_state.start_time = datetime.now()

# --- 2. 登入區 (改為編號制) ---
if not st.session_state.user_nickname:
    st.title("🧠 創傷知情 AI 實作教練")
    st.info("請輸入您的研究編號 (ID) 以開始諮詢。")
    
    # 1. 建立輸入框
    nickname_input = st.text_input("請輸入您的編號：", placeholder="例如：001, 002...") 
    
    # 2. 建立登入按鈕
    if st.button("🚀 進入教練室"):
        if nickname_input.strip():
            st.session_state.user_nickname = nickname_input
            st.session_state.start_time = datetime.now() # 記錄開始時間
            st.rerun()
        else:
            st.error("❌ 編號不能為空！")
    st.stop()

# --- 3. 側邊欄設定 ---
st.sidebar.title(f"👤 學員: {st.session_state.user_nickname}")
st.sidebar.markdown("---")
st.sidebar.markdown("### 📤 結束諮詢")

# 上傳並登出按鈕
if st.sidebar.button("上傳紀錄並登出"):
    if not st.session_state.history:
        st.sidebar.warning("還沒有對話紀錄喔！")
    else:
        with st.spinner("正在上傳數據至雲端..."):
            # 讀取當前設定的年級與語言
            current_grade = st.session_state.get("current_grade", "未設定")
            current_lang = st.session_state.get("current_lang", "未設定")
            
            if save_to_google_sheets(st.session_state.user_nickname, st.session_state.history, current_grade, current_lang):
                st.sidebar.success("✅ 上傳成功！")
                time.sleep(1) 

                # 清除資料 (保留必要參數，清除個資)
                keys_to_clear = ["user_nickname", "history", "start_time", "chat_session"]
                for key in keys_to_clear:
                    if key in st.session_state:
                        del st.session_state[key]
                
                # 設定登出記號
                st.session_state.logout_triggered = True
                st.rerun()

# API Key 與設定
st.sidebar.markdown("---")
st.sidebar.warning("🔑 請輸入您自己的 Gemini API Key")
api_key = st.sidebar.text_input("在此貼上您的 API Key", type="password")

if not api_key:
    st.info("💡 提示：請先在側邊欄輸入 API Key，否則系統無法運作。")
    st.stop() 

# 自動偵測模型
valid_model_name = None
if api_key:
    try:
        genai.configure(api_key=api_key)
        available_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        if available_models:
            valid_model_name = st.sidebar.selectbox("🤖 AI 模型", available_models)
    except: 
        st.sidebar.error("❌ API Key 無效")

# 選項設定 (保留 Coach 的核心功能)
student_grade = st.sidebar.selectbox("🎯 諮詢對象年級", ["國小", "國中", "高中"])
lang = st.sidebar.selectbox("🌐 選擇對話語言", ["繁體中文", "粵語", "English"])

# 將當前設定存入 session 方便上傳時讀取
st.session_state.current_grade = student_grade
st.session_state.current_lang = lang

# --- 4. 自動讀取教材 ---
if not st.session_state.loaded_text:
    combined_text = ""
    pdf_files = glob.glob("*.pdf")
    
    if pdf_files:
        with st.spinner(f"📚 正在內化 {len(pdf_files)} 份專業教材..."):
            try:
                for filename in pdf_files:
                    reader = PdfReader(filename)
                    for page in reader.pages:
                        text = page.extract_text()
                        if text: combined_text += text + "\n"
                st.session_state.loaded_text = combined_text
            except Exception as e:
                st.error(f"教材讀取失敗: {e}")
    else:
        st.warning("⚠️ 倉庫中找不到 PDF 檔案。")

# --- 5. 教練對話邏輯 (Coach Brain) ---
st.title("💬 實作策略諮詢區")

if st.session_state.loaded_text and api_key and valid_model_name:
    model = genai.GenerativeModel(
        model_name=valid_model_name,
        safety_settings={
            HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
        }
    )

    # 初始歡迎訊息 (Coach 特有的開場)
    if len(st.session_state.history) == 0:
        sys_prompt = f"""
        Role: You are a "Trauma-Informed Implementation Coach" for teachers.
        Current Context: Working with {student_grade} students.
        Language: {lang}.
        
        Knowledge Base: {st.session_state.loaded_text[:30000]} 
        
        Guidelines:
        1. Empathize with the teacher first.
        2. Use Socratic questioning to help the teacher identify the student's behavior as a trauma response (4F: Fight, Flight, Freeze, Fawn).
        3. Differentiate advice by grade:
           - For 國小: Focus on sensory regulation and safety routines.
           - For 國中/高中: Focus on autonomy, respect, and collaborative problem-solving.
        4. Refer to 'Strength-Based' and 'Connect before Correct' principles from the texts.
        """
        
        # 開場白
        welcome_msg = f"你好 {st.session_state.user_nickname} 老師，很高興能擔任您的 AI 實作教練。目前針對 {student_grade} 班級的教學現場，有沒有什麼讓你感到挫折或困難的具體個案，我們一起來討論看看？"
        
        st.session_state.chat_session = model.start_chat(history=[
            {"role": "user", "parts": [sys_prompt]},
            {"role": "model", "parts": [welcome_msg]}
        ])
        st.session_state.history.append({"role": "assistant", "content": welcome_msg})

    # 顯示對話紀錄
    for msg in st.session_state.history:
        role = "assistant" if msg["role"] == "assistant" else "user"
        with st.chat_message(role):
            st.write(msg["content"])

    # 使用者輸入區
    if user_in := st.chat_input("描述您的挑戰（例如：學生突然大叫、拒絕合作...）"):
        st.session_state.history.append({"role": "user", "content": user_in})
        try:
            resp = st.session_state.chat_session.send_message(user_in)
            st.session_state.history.append({"role": "assistant", "content": resp.text})
            st.rerun()
        except Exception as e:
            st.error(f"❌ 發生錯誤: {e}")
