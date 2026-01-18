import streamlit as st
import os
import pandas as pd
from datetime import datetime
from pypdf import PdfReader
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold

# --- 1. 系統設定 ---
st.set_page_config(page_title="創傷知情 AI 實作教練", layout="wide")

# 初始化 Session State
if "history" not in st.session_state: st.session_state.history = []
if "loaded_text" not in st.session_state: st.session_state.loaded_text = ""
if "user_nickname" not in st.session_state: st.session_state.user_nickname = ""

# --- 2. 登入區 ---
if not st.session_state.user_nickname:
    st.title("🧠 創傷知情 AI 實作教練")
    st.markdown("### 老師您好，我是您的 AI 導師。讓我們一起討論課後實踐中遇到的挑戰。")
    nickname_input = st.text_input("請輸入您的暱稱以開始：", placeholder="例如：兆祺心理師...")
    if st.button("🚀 開始諮詢"):
        if nickname_input.strip():
            st.session_state.user_nickname = nickname_input
            st.rerun()
        else:
            st.error("❌ 暱稱不能為空！")
    st.stop()

# --- 3. 側邊欄設定 ---
st.sidebar.title(f"👤 導師: {st.session_state.user_nickname}")
st.sidebar.markdown("---")

# API Key 設定
api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    api_key = st.sidebar.text_input("請輸入您的 Gemini API Key", type="password")

# 模型選擇
valid_model_name = None
if api_key:
    try:
        genai.configure(api_key=api_key)
        available_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        if available_models:
            valid_model_name = st.sidebar.selectbox("🤖 選擇 AI 模型", available_models)
    except: st.sidebar.error("API Key 驗證失敗")

lang = st.sidebar.selectbox("選擇對話語言", ["繁體中文", "粵語", "English"])

# --- 4. 自動讀取雙教材 (硬寫入檔名) ---
FILES = [
    "創傷知情文本Creating Trauma informed Strength based Classroom_compressed.pdf",
    "Assigning AI_Seven Apperoaches for Students with prompts.pdf"
]

if not st.session_state.loaded_text:
    combined_text = ""
    with st.spinner("📚 正在內化專業文本與 AI 引導策略..."):
        for filename in FILES:
            if os.path.exists(filename):
                try:
                    reader = PdfReader(filename)
                    for page in reader.pages:
                        text = page.extract_text()
                        if text: combined_text += text + "\n"
                except: st.error(f"讀取 {filename} 失敗")
        st.session_state.loaded_text = combined_text

# --- 5. 教練邏輯主畫面 ---
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

    # A. 初始引導
    if len(st.session_state.history) == 0:
        st.info("💡 **教練建議**：您可以描述一個最近在教室裡遇到的挑戰，例如學生的情緒爆發或拒絕參與。")
        
        # 設定系統角色（參考 Mollick 的 AI Coach 模式）
        sys_prompt = f"""
        Role: You are a "Trauma-Informed Implementation Coach" for teachers.
        Style: Empathetic, Socratic, Supportive, and Professional.
        Core Knowledge: {st.session_state.loaded_text[:25000]}
        
        Instructions based on Mollick & Mollick:
        1. Act as a coach, not an answer-machine. 
        2. When a teacher shares a problem, first validate their feeling.
        3. Use the knowledge base to guide them to identify the student's 4F response (Fight/Flight/Freeze/Fawn).
        4. Help them shift from 'Correction' to 'Connection' and 'Strength-based' perspectives.
        5. Ask one guiding question at a time to lead their reflection.
        6. Language: {lang}.
        """
        st.session_state.chat_session = model.start_chat(history=[
            {"role": "user", "parts": [sys_prompt]},
            {"role": "model", "parts": [f"你好 {st.session_state.user_nickname}，我是您的 AI 實作教練。今天在教室裡有什麼讓您感到掛心的情況嗎？"]}
        ])
        st.session_state.history.append({"role": "student", "content": f"你好 {st.session_state.user_nickname}，我是您的 AI 實作教練。今天在教室裡有什麼讓您感到掛心的情況嗎？"})

    # B. 顯示對話
    for msg in st.session_state.history:
        with st.chat_message("assistant" if msg["role"] == "student" else "user"):
            st.write(msg["content"])

    # C. 輸入框
    if user_in := st.chat_input("輸入您的實作困擾..."):
        st.session_state.history.append({"role": "teacher", "content": user_in})
        try:
            resp = st.session_state.chat_session.send_message(user_in)
            st.session_state.history.append({"role": "student", "content": resp.text})
            st.rerun()
        except Exception as e:
            st.error(f"連線中斷: {e}")

# --- 6. 紀錄下載功能 ---
st.sidebar.markdown("---")
if st.session_state.history:
    st.sidebar.subheader("💾 紀錄下載")
    df = pd.DataFrame(st.session_state.history)
    df['user'] = st.session_state.user_nickname
    csv = df.to_csv(index=False).encode('utf-8-sig')
    st.sidebar.download_button(
        label="📥 下載諮詢紀錄 (CSV)",
        data=csv,
        file_name=f"實作教練紀錄_{st.session_state.user_nickname}.csv",
        mime="text/csv"
    )
