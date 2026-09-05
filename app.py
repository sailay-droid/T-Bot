import os
import io
import time
import re
import requests
import telebot
import asyncio
import edge_tts
from flask import Flask, request

# ============================================
# ၁။ Environment Variables များကို ဖတ်ယူခြင်း
# ============================================
BOT_TOKEN = os.environ.get('BOT_TOKEN')
GROQ_API_KEY = os.environ.get('GROQ_API_KEY')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
RENDER_URL = os.environ.get('RENDER_EXTERNAL_URL', '')

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable not set!")
if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY environment variable not set!")
if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY environment variable not set!")

# ============================================
# ၂။ Flask & Bot Setup
# ============================================
bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

TEMP_FOLDER = "/tmp/"
if not os.path.exists(TEMP_FOLDER):
    os.makedirs(TEMP_FOLDER)

# ============================================
# ၃။ Helper Functions
# ============================================

# ၃.၁ စာသားကို အပိုင်းလိုက်ခွဲပေးမယ့် Function
def split_text_into_chunks(text, max_chars=3000):
    if len(text) <= max_chars:
        return [text]
    chunks = []
    sentences = re.split(r'(?<=[.!?])\s+', text)
    current_chunk = []
    current_len = 0
    for sentence in sentences:
        sentence_len = len(sentence) + 1
        if current_len + sentence_len > max_chars and current_chunk:
            chunks.append(' '.join(current_chunk))
            current_chunk = [sentence]
            current_len = sentence_len
        else:
            current_chunk.append(sentence)
            current_len += sentence_len
    if current_chunk:
        chunks.append(' '.join(current_chunk))
    return chunks

# ၃.၂ Transcription (SRT) - Groq Whisper API
def transcribe_with_groq(file_path):
    url = "https://api.groq.com/openai/v1/audio/transcriptions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
    filename = os.path.basename(file_path)
    if filename.endswith('.mp4') or filename.endswith('.avi') or filename.endswith('.mov'):
        mime_type = 'video/mp4'
    else:
        mime_type = 'audio/mp3'
    with open(file_path, 'rb') as f:
        files = {'file': (filename, f, mime_type)}
        data = {'model': 'whisper-large-v3', 'response_format': 'srt'}
        response = requests.post(url, headers=headers, files=files, data=data, timeout=120)
    if response.status_code == 200:
        return response.text
    else:
        raise Exception(f"Groq API Error: {response.status_code} - {response.text}")

# ၃.၃ Translation - Gemini (Primary) + LibreTranslate (Fallback)
def translate_with_gemini(text):
    import google.generativeai as genai
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(
        model_name="gemini-1.5-flash",
        system_instruction="You are a professional translator. Translate the following English text into natural, fluent Burmese (Myanmar). Use everyday language and maintain the original tone and meaning. Only return the translated text."
    )
    response = model.generate_content(text)
    return response.text

def translate_with_libretranslate(text):
    url = "https://libretranslate.com/translate"
    payload = {"q": text, "source": "en", "target": "my", "format": "text"}
    response = requests.post(url, json=payload, timeout=30)
    if response.status_code == 200:
        return response.json()["translatedText"]
    else:
        raise Exception(f"LibreTranslate Error: {response.status_code}")

def translate_text_with_fallback(text):
    try:
        return translate_with_gemini(text)
    except Exception as e:
        print(f"⚠️ Gemini failed: {e}. Falling back to LibreTranslate...")
        return translate_with_libretranslate(text)

# ၃.၄ Text-to-Speech - Edge TTS (မြန်မာအသံ ၂ မျိုး)
async def tts_with_edge(text, voice="my-MM-NilarNeural"):
    """Edge TTS ကိုသုံးပြီး မြန်မာအသံဖိုင် ထုတ်ပေးမယ်"""
    communicate = edge_tts.Communicate(text, voice)
    audio_data = b""
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_data += chunk["data"]
    return audio_data

def tts_with_gtts_fallback(text):
    """gTTS ကို Fallback အနေနဲ့ သုံးမယ်"""
    from gtts import gTTS
    tts = gTTS(text[:1000], lang='en', slow=False)
    audio_bytes = io.BytesIO()
    tts.write_to_fp(audio_bytes)
    audio_bytes.seek(0)
    return audio_bytes.read()

def generate_voiceover_full(text, voice_name="my-MM-NilarNeural"):
    """စာသားရှည်ကြီးကို အပိုင်းလိုက်ခွဲပြီး Edge TTS ခေါ်ကာ အားလုံးပေါင်းပေးမယ်"""
    chunks = split_text_into_chunks(text, max_chars=3000)
    total = len(chunks)
    combined_audio = b''

    for i, chunk in enumerate(chunks):
        print(f"🔊 Edge TTS ({voice_name}): {i+1}/{total}")
        try:
            audio_bytes = asyncio.run(tts_with_edge(chunk, voice_name))
            combined_audio += audio_bytes
        except Exception as e:
            print(f"⚠️ Edge TTS failed: {e}. Falling back to gTTS...")
            audio_bytes = tts_with_gtts_fallback(chunk)
            combined_audio += audio_bytes
        time.sleep(0.5)

    return combined_audio

# ၃.၅ SRT ဖိုင် တစ်ခုလုံးကို ဘာသာပြန်ပေးမယ့် Function
def translate_srt_full(srt_content):
    lines = srt_content.split('\n')
    text_parts = []
    text_indices = []
    for i, line in enumerate(lines):
        if line.strip() and not line.strip().isdigit() and '-->' not in line:
            text_parts.append(line.strip())
            text_indices.append(i)
    full_text = '\n'.join(text_parts)
    if len(full_text) > 4000:
        chunks = split_text_into_chunks(full_text, max_chars=4000)
        translated_parts = []
        for chunk in chunks:
            translated = translate_text_with_fallback(chunk)
            translated_parts.append(translated)
            time.sleep(0.5)
        translated_text = ' '.join(translated_parts)
    else:
        translated_text = translate_text_with_fallback(full_text)
    translated_lines = translated_text.split('\n')
    result_lines = lines.copy()
    for idx, pos in enumerate(text_indices):
        if idx < len(translated_lines):
            result_lines[pos] = translated_lines[idx]
    return '\n'.join(result_lines)

# ============================================
# ၄။ Flask Routes (Render အတွက်)
# ============================================
@app.route('/')
@app.route('/health')
def health_check():
    return "✅ Bot is running!", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return 'OK', 200
    return 'Unsupported content type', 400

# ============================================
# ၅။ Telegram Bot Handlers
# ============================================

# /start
@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message,
        "🎬 **MyRCBot - Movie Recap Assistant**\n\n"
        "📌 **Command များ:**\n"
        "1️⃣ `/transcribe` - Video/Audio ကနေ SRT Transcript ထုတ်ပေးမယ်\n"
        "2️⃣ `/translate` - SRT ဖိုင် ဘာသာပြန်ပေးမယ်\n"
        "3️⃣ `/tts` - စာသားကို Voiceover (Nilar) ပြောင်းပေးမယ်\n"
        "4️⃣ `/tts_nilar` - Nilar (အမျိုးသမီး) အသံနဲ့ Voiceover\n"
        "5️⃣ `/tts_thiha` - Thiha (အမျိုးသား) အသံနဲ့ Voiceover\n"
        "6️⃣ `/recap` - Video ကနေ ဇာတ်လမ်းအနှစ်ချုပ် ထုတ်ပေးမယ်\n\n"
        "💡 သုံးနည်း: Command ကိုနှိပ်ပြီး ဖိုင်/စာသား ပို့ပါ။",
        parse_mode='Markdown')

# /transcribe
@bot.message_handler(commands=['transcribe'])
def transcribe_command(message):
    bot.reply_to(message, "🎤 Video/Audio ဖိုင် (MP4, MP3) ကို ပို့ပါ။")
    bot.register_next_step_handler(message, process_transcribe)

def process_transcribe(message):
    try:
        if not message.audio and not message.video and not message.document:
            bot.reply_to(message, "❌ Audio/Video ဖိုင် တစ်ခုခု ပို့ပါ။")
            return
        if message.audio:
            file_info = message.audio
            ext = "mp3"
        elif message.video:
            file_info = message.video
            ext = "mp4"
        else:
            file_info = message.document
            ext = message.document.file_name.split('.')[-1] if message.document.file_name else "bin"
        file_path = bot.get_file(file_info.file_id)
        downloaded_file = bot.download_file(file_path.file_path)
        temp_file = os.path.join(TEMP_FOLDER, f"audio_{message.from_user.id}_{int(time.time())}.{ext}")
        with open(temp_file, 'wb') as f:
            f.write(downloaded_file)
        status = bot.reply_to(message, "⏳ Transcription လုပ်နေပါပြီ...")
        srt_content = transcribe_with_groq(temp_file)
        os.remove(temp_file)
        srt_file = io.BytesIO(srt_content.encode('utf-8'))
        srt_file.name = f"transcript_{message.from_user.id}.srt"
        bot.send_document(message.chat.id, srt_file, caption="✅ ဒီမှာ သင့် SRT Transcript ပါ။")
        bot.delete_message(message.chat.id, status.message_id)
    except Exception as e:
        bot.reply_to(message, f"❌ အမှားဖြစ်သွားတယ်: {str(e)}")

# /translate
@bot.message_handler(commands=['translate'])
def translate_command(message):
    bot.reply_to(message, "🌍 SRT ဖိုင် (.srt) ကို ပို့ပါ။")
    bot.register_next_step_handler(message, process_translate)

def process_translate(message):
    try:
        if not message.document or not message.document.file_name.endswith('.srt'):
            bot.reply_to(message, "❌ .srt ဖိုင် တစ်ခု ပို့ပါ။")
            return
        file_info = message.document
        file_path = bot.get_file(file_info.file_id)
        downloaded_file = bot.download_file(file_path.file_path)
        srt_content = downloaded_file.decode('utf-8')
        status = bot.reply_to(message, "⏳ ဘာသာပြန်နေပါပြီ...")
        translated_srt = translate_srt_full(srt_content)
        srt_file = io.BytesIO(translated_srt.encode('utf-8'))
        srt_file.name = f"translated_{message.from_user.id}.srt"
        bot.send_document(message.chat.id, srt_file, caption="✅ ဒီမှာ ဘာသာပြန်ပြီးသား SRT ဖိုင်ပါ။ (မြန်မာလို)")
        bot.delete_message(message.chat.id, status.message_id)
    except Exception as e:
        bot.reply_to(message, f"❌ အမှားဖြစ်သွားတယ်: {str(e)}")

# /tts (Default - Nilar)
@bot.message_handler(commands=['tts'])
def tts_command(message):
    bot.reply_to(message, "🔊 Voiceover လုပ်ချင်တဲ့ စာသားကို ရိုက်ထည့်ပါ။ (မူရင်းအသံ - Nilar)")
    bot.register_next_step_handler(message, lambda m: process_tts(m, 'my-MM-NilarNeural'))

# /tts_nilar
@bot.message_handler(commands=['tts_nilar'])
def tts_nilar_command(message):
    bot.reply_to(message, "🔊 Voiceover လုပ်ချင်တဲ့ စာသားကို ရိုက်ထည့်ပါ။ (Nilar - အမျိုးသမီး)")
    bot.register_next_step_handler(message, lambda m: process_tts(m, 'my-MM-NilarNeural'))

# /tts_thiha
@bot.message_handler(commands=['tts_thiha'])
def tts_thiha_command(message):
    bot.reply_to(message, "🔊 Voiceover လုပ်ချင်တဲ့ စာသားကို ရိုက်ထည့်ပါ။ (Thiha - အမျိုးသား)")
    bot.register_next_step_handler(message, lambda m: process_tts(m, 'my-MM-ThihaNeural'))

def process_tts(message, voice_name):
    try:
        text = message.text
        if not text:
            bot.reply_to(message, "❌ စာသားတစ်ခုခု ရိုက်ထည့်ပါ။")
            return
        voice_display = "Nilar (အမျိုးသမီး)" if "Nilar" in voice_name else "Thiha (အမျိုးသား)"
        status = bot.reply_to(message, f"⏳ Voiceover ဖန်တီးနေပါပြီ... (Edge TTS - {voice_display})")
        combined_audio = generate_voiceover_full(text, voice_name)
        audio_file = io.BytesIO(combined_audio)
        audio_file.name = f"voiceover_{message.from_user.id}.mp3"
        bot.send_audio(message.chat.id, audio_file, caption=f"✅ ဒီမှာ သင့် Voiceover ဖိုင်ပါ။ (Edge TTS - {voice_display})")
        bot.delete_message(message.chat.id, status.message_id)
    except Exception as e:
        bot.reply_to(message, f"❌ အမှားဖြစ်သွားတယ်: {str(e)}")

# /recap
@bot.message_handler(commands=['recap'])
def recap_command(message):
    bot.reply_to(message, "🎬 Recap လုပ်ချင်တဲ့ Video ဖိုင် (MP4) ကို ပို့ပါ။")
    bot.register_next_step_handler(message, process_recap)

def process_recap(message):
    try:
        if not message.video and not message.document:
            bot.reply_to(message, "❌ Video ဖိုင် တစ်ခု ပို့ပါ။")
            return
        if message.video:
            file_info = message.video
        else:
            file_info = message.document
        file_path = bot.get_file(file_info.file_id)
        downloaded_file = bot.download_file(file_path.file_path)
        temp_file = os.path.join(TEMP_FOLDER, f"video_{message.from_user.id}_{int(time.time())}.mp4")
        with open(temp_file, 'wb') as f:
            f.write(downloaded_file)
        status = bot.reply_to(message, "⏳ Video ကို ခွဲခြမ်းစိတ်ဖြာနေပါပြီ...")
        transcript = transcribe_with_groq(temp_file)
        os.remove(temp_file)
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-1.5-flash')
        prompt = f"""အောက်ပါ Video Transcript ကိုဖတ်ပြီး ရုပ်ရှင်ဇာတ်လမ်းအနှစ်ချုပ် (Movie Recap) ပုံစံနဲ့ မြန်မာလို ရေးပါ။ 
        ဇာတ်လမ်းအကျဉ်း၊ အဓိကဇာတ်ကောင်တွေ၊ ဇာတ်လမ်းအဆုံးသတ်ကို ထည့်သွင်းပါ။
        Transcript: {transcript[:5000]}"""
        response = model.generate_content(prompt)
        recap = response.text
        bot.reply_to(message, f"🎬 **Movie Recap**\n\n{recap}", parse_mode='Markdown')
        bot.send_document(message.chat.id, io.BytesIO(recap.encode('utf-8')), visible_file_name="recap.txt", caption="📄 ဒီမှာ Recap စာသားဖိုင်ပါ။")
        bot.delete_message(message.chat.id, status.message_id)
    except Exception as e:
        bot.reply_to(message, f"❌ အမှားဖြစ်သွားတယ်: {str(e)}")

# ============================================
# ၆။ Main Entry Point
# ============================================
if __name__ == '__main__':
    # Polling Mode ကို သုံးမယ်
    print("🔄 Using polling mode...")
    bot.remove_webhook()
    
    # Polling ကို Background Thread မှာ run မယ်
    import threading
    threading.Thread(target=bot.infinity_polling, daemon=True).start()
    
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
