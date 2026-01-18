import streamlit as st
import os
import glob
import pandas as pd
from datetime import datetime
from pypdf import PdfReader
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold

# --- 1. 系統設定 ---
st.set_page_config(page_title="創傷知情 AI 實作教練 (專業版)", layout="wide")

# 初始化 Session State，確保對話紀錄與文本只讀取一次
if "history" not in st.session_state: st.session_state.history = []
if "loaded_text" not in st.session_state: st.session_state.loaded_text = ""
if "user_nickname" not in st.session_state: st.session_state.user_nickname = ""

# --- 2. 登入區 ---
if not st.session_state.user_nickname:
    st.title("🧠 創傷知情 AI 實作教練")
    st.markdown("### 老師您好，我是您的 AI 導師。請輸入您的暱稱開始諮詢。")
    nickname_input = st.text_input("請輸入您的暱稱：", placeholder="例如：兆祺心理師...")
    if st.button("🚀 進入教練室"):
        if nickname_input.strip():
            st.session_state.user_nickname = nickname_input
            st.rerun()
        else:
            st.error("❌ 暱稱不能為空！")
    st.stop()

# --- 3. 側邊欄設定 ---
st.sidebar.title(f"👤 導師: {st.session_state.user_nickname}")
st.sidebar.markdown("---")

# API Key 設定（優先從系統 Secrets 讀取）
api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    api_key = st.sidebar.text_input("🔑 請輸入您的 Gemini API Key", type="password")

valid_model_name = None
if api_key:
    try:
        genai.configure(api_key=api_key)
        available_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        if available_models:
            # 讓老師可以選擇模型，預設會出現如 gemini-1.5-flash 等
            valid_model_name = st.sidebar.selectbox("🤖 選擇 AI 模型", available_models)
    except:
        st.sidebar.error("❌ API Key 無效或網路連線失敗")

# 年級與語言選項
student_grade = st.sidebar.selectbox("🎯 諮詢對象年級", ["國小", "國中", "高中"])
lang = st.sidebar.selectbox("🌐 選擇對話語言", ["繁體中文", "粵語", "English"])

# --- 4. 自動讀取倉庫內所有 PDF 檔案 ---
if not st.session_state.loaded_text:
    combined_text = ""
    # 搜尋當前目錄下所有的 PDF
    pdf_files = glob.glob("*.pdf")
    
    if pdf_files:
        with st.spinner(f"📚 正在內化 {len(pdf_files)} 份專業教材..."):
            for filename in pdf_files:
                try:
                    reader = PdfReader(filename)
                    for page in reader.pages:
                        text = page.extract_text()
                        if text: combined_text += text + "\n"
                except Exception as e:
                    st.error(f"讀取 {filename} 失敗: {e}")
            st.session_state.loaded_text = combined_text
    else:
        st.warning("⚠️ 倉庫中找不到任何 PDF 檔案，請確認檔案已上傳。")

# --- 5. 教練對話邏輯 ---
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

    # 初始歡迎訊息與系統 Prompt 設定 
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
        
        st.session_state.chat_session = model.start_chat(history=[
            {"role": "user", "parts": [sys_prompt]},
            {"role": "model", "parts": [f"你好 {st.session_state.user_nickname} 老師，很高興能擔任您的 AI 實作教練。目前針對 {student_grade} 班級的教學現場，有沒有什麼讓你感到挫折或困難的具體個案，我們一起來討論看看？"]}
        ])
        st.session_state.history.append({"role": "assistant", "content": f"你好 {st.session_state.user_nickname} 老師，很高興能擔任您的 AI 實作教練。目前針對 {student_grade} 班級的教學現場，有沒有什麼讓你感到挫折或困難的具體個案，我們一起來討論看看？"})

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
            st.error(f"❌ 發生錯誤（可能是 API 流量限制）: {e}")

# --- 6. 紀錄下載功能 ---
st.sidebar.markdown("---")
if st.session_state.history:
    st.sidebar.subheader("💾 紀錄保存")
    # 排除第一筆系統設定用的背景資訊，只下載對話
    df = pd.DataFrame(st.session_state.history)
    df['nickname'] = st.session_state.user_nickname
    df['grade'] = student_grade
    csv = df.to_csv(index=False).encode('utf-8-sig')
    
    st.sidebar.download_button(
        label="📥 下載諮詢紀錄 (CSV)",
        data=csv,
        file_name=f"實作諮詢_{student_grade}_{st.session_state.user_nickname}.csv",
        mime="text/csv"
    )
    st.sidebar.caption("💡 離開前請記得下載紀錄以供日後分析。")
