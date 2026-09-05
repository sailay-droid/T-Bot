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
# ၂။ Flask & Bot Setup (Webhook အတွက် threaded=False ထည့်ပါ)
# ============================================
bot = telebot.TeleBot(BOT_TOKEN, threaded=False)
app = Flask(__name__)

TEMP_FOLDER = "/tmp/"
if not os.path.exists(TEMP_FOLDER):
    os.makedirs(TEMP_FOLDER)

# ============================================
# ၃။ Helper Functions
# ============================================

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

# ============================================
# ၃.၁ Translation - Gemini REST API (gemini-3.6-flash)
# ============================================
def translate_with_gemini(text):
    """Gemini REST API (gemini-3.6-flash) ကို သုံးပြီး မြန်မာလို ဘာသာပြန်မယ်"""
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent"
    headers = {"Content-Type": "application/json"}
    params = {"key": GEMINI_API_KEY}
    
    prompt = f"""You are a professional translator. Translate the following English text into natural, fluent Burmese (Myanmar). Use everyday language and maintain the original tone and meaning. Only return the translated text.

Text to translate:
{text}"""
    
    data = {"contents": [{"parts": [{"text": prompt}]}]}
    response = requests.post(url, headers=headers, params=params, json=data, timeout=120)
    
    if response.status_code == 200:
        result = response.json()
        try:
            return result["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as e:
            raise Exception(f"Unexpected API response: {result}")
    else:
        raise Exception(f"Gemini API Error: {response.status_code} - {response.text}")

def translate_text_with_fallback(text):
    """Gemini ကိုပဲ သုံးမယ် (Error ဖြစ်ရင် မူရင်းစာသားကို ပြန်ပေးမယ်)"""
    try:
        result = translate_with_gemini(text)
        # Gemini က တစ်ခါတလေ ဘာသာပြန်မရဘူးဆိုရင် မူရင်းစာသားကို ပြန်ပေးမယ်
        if not result or len(result.strip()) < 5:
            return text
        return result
    except Exception as e:
        print(f"⚠️ Gemini failed: {e}")
        return text  # မူရင်းစာသားကို ပြန်ပေးမယ်
# ============================================
# ၃.၂ SRT ဖိုင် ဘာသာပြန်ခြင်း
# ============================================
def translate_srt_full(srt_content):
    """SRT ဖိုင်ကို လိုင်းလိုက်ခွဲပြီး အပိုင်းလိုက် ဘာသာပြန်မယ်"""
    lines = srt_content.split('\n')
    
    # စာသားတွေကို စုမယ် (Timestamps နဲ့ နံပါတ်တွေကို ချန်ထားမယ်)
    text_lines = []
    text_indices = []
    for i, line in enumerate(lines):
        if line.strip() and not line.strip().isdigit() and '-->' not in line:
            text_lines.append(line.strip())
            text_indices.append(i)
    
    # စာသားအားလုံးကို တစ်ခါတည်း မပေါင်းဘဲ အပိုင်းလိုက် ဘာသာပြန်မယ်
    CHUNK_SIZE = 100  # စာကြောင်း ၁၀၀ စီ ခွဲမယ်
    translated_parts = []
    translated_indices = []
    
    for i in range(0, len(text_lines), CHUNK_SIZE):
        chunk_lines = text_lines[i:i+CHUNK_SIZE]
        chunk_text = '\n'.join(chunk_lines)
        
        # ဘာသာပြန်မယ်
        translated = translate_text_with_fallback(chunk_text)
        translated_parts.append(translated)
        
        # ဘာသာပြန်ပြီးသား စာကြောင်းတွေကို ခွဲမယ်
        translated_lines = translated.split('\n')
        for j, line in enumerate(translated_lines):
            if j < len(chunk_lines):
                translated_indices.append((text_indices[i+j], line))
        
        time.sleep(0.3)  # Rate Limit အတွက်
    
    # မူရင်း SRT ပုံစံအတိုင်း ပြန်တည်ဆောက်မယ်
    result_lines = lines.copy()
    for idx, translated_text in translated_indices:
        if idx < len(result_lines):
            result_lines[idx] = translated_text
    
    return '\n'.join(result_lines)

# ============================================
# ၃.၃ Text-to-Speech - Edge TTS
# ============================================
async def tts_with_edge(text, voice="my-MM-NilarNeural"):
    communicate = edge_tts.Communicate(text, voice)
    audio_data = b""
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_data += chunk["data"]
    return audio_data

def tts_with_gtts_fallback(text):
    from gtts import gTTS
    tts = gTTS(text[:1000], lang='en', slow=False)
    audio_bytes = io.BytesIO()
    tts.write_to_fp(audio_bytes)
    audio_bytes.seek(0)
    return audio_bytes.read()

def generate_voiceover_full(text, voice_name="my-MM-NilarNeural"):
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

# ============================================
# ၄။ Flask Routes (Webhook)
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

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message,
        "🎬 **MyRCBot - Movie Recap Assistant**\n\n"
        "📌 **Command များ:**\n"
        "1️⃣ `/transcribe` - Video/Audio ကနေ SRT Transcript ထုတ်ပေးမယ်\n"
        "2️⃣ `/translate` - SRT ဖိုင် မြန်မာလို ဘာသာပြန်ပေးမယ်\n"
        "3️⃣ `/tts` - စာသားကို Voiceover (Nilar) ပြောင်းပေးမယ်\n"
        "4️⃣ `/tts_nilar` - Nilar (အမျိုးသမီး) အသံနဲ့ Voiceover\n"
        "5️⃣ `/tts_thiha` - Thiha (အမျိုးသား) အသံနဲ့ Voiceover\n"
        "6️⃣ `/recap` - Video ကနေ ဇာတ်လမ်းအနှစ်ချုပ် ထုတ်ပေးမယ်\n\n"
        "💡 သုံးနည်း: Command ကိုနှိပ်ပြီး ဖိုင်/စာသား ပို့ပါ။",
        parse_mode='Markdown')

# ============================================
# /transcribe - Video/Audio ကနေ SRT ထုတ်ခြင်း
# ============================================
@bot.message_handler(commands=['transcribe'])
def transcribe_command(message):
    msg = bot.reply_to(message, "🎤 Video/Audio ဖိုင် (MP4, MP3) ကို ပို့ပါ။")
    bot.register_next_step_handler(msg, process_transcribe)

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

# ============================================
# /translate - SRT ဖိုင် ဘာသာပြန်ခြင်း (ဒုတိယနည်းလမ်း)
# ============================================
@bot.message_handler(commands=['translate'])
def translate_command(message):
    msg = bot.reply_to(message, "🌍 SRT ဖိုင် (.srt) ကို ပို့ပါ။ (Gemini API နဲ့ မြန်မာလို ဘာသာပြန်ပေးမယ်)")
    bot.register_next_step_handler(msg, process_translate)

def process_translate(message):
    try:
        if not message.document:
            bot.reply_to(message, "❌ ဖိုင် (.srt) တစ်ခု ပို့ပါ။")
            return
        
        if not message.document.file_name.endswith('.srt'):
            bot.reply_to(message, "❌ .srt ဖိုင် သာ ပို့ပါ။")
            return
        
        file_info = message.document
        file_path = bot.get_file(file_info.file_id)
        downloaded_file = bot.download_file(file_path.file_path)
        srt_content = downloaded_file.decode('utf-8')
        
        # SRT ဖိုင်ရဲ့ စာကြောင်းအရေအတွက်ကို ပြောပါ
        line_count = len(srt_content.split('\n'))
        status = bot.reply_to(message, f"⏳ ဘာသာပြန်နေပါပြီ... (စာကြောင်း {line_count} ကြောင်း) ခဏစောင့်ပါ။")
        
        # SRT ဖိုင်ကို ဘာသာပြန်မယ်
        translated_srt = translate_srt_full(srt_content)
        
        # Document ကို ပြန်ပို့မယ်
        srt_bytes = translated_srt.encode('utf-8')
        srt_file = io.BytesIO(srt_bytes)
        srt_file.seek(0)
        srt_file.name = f"translated_{message.from_user.id}.srt"
        
        bot.send_document(
            message.chat.id,
            srt_file,
            caption="✅ ဒီမှာ ဘာသာပြန်ပြီးသား SRT ဖိုင်ပါ။ (မြန်မာလို - Gemini)"
        )
        bot.delete_message(message.chat.id, status.message_id)
        
    except Exception as e:
        bot.reply_to(message, f"❌ အမှားဖြစ်သွားတယ်: {str(e)}")
        # Error ရဲ့ အသေးစိတ်ကို Log ထဲမှာ ပြမယ်
        print(f"❌ process_translate error: {e}")
# ============================================
# /tts - Voiceover (Edge TTS)
# ============================================
@bot.message_handler(commands=['tts'])
def tts_command(message):
    msg = bot.reply_to(message, "🔊 Voiceover လုပ်ချင်တဲ့ စာသားကို ရိုက်ထည့်ပါ။ (မူရင်းအသံ - Nilar)")
    bot.register_next_step_handler(msg, lambda m: process_tts(m, 'my-MM-NilarNeural'))

@bot.message_handler(commands=['tts_nilar'])
def tts_nilar_command(message):
    msg = bot.reply_to(message, "🔊 Voiceover လုပ်ချင်တဲ့ စာသားကို ရိုက်ထည့်ပါ။ (Nilar - အမျိုးသမီး)")
    bot.register_next_step_handler(msg, lambda m: process_tts(m, 'my-MM-NilarNeural'))

@bot.message_handler(commands=['tts_thiha'])
def tts_thiha_command(message):
    msg = bot.reply_to(message, "🔊 Voiceover လုပ်ချင်တဲ့ စာသားကို ရိုက်ထည့်ပါ။ (Thiha - အမျိုးသား)")
    bot.register_next_step_handler(msg, lambda m: process_tts(m, 'my-MM-ThihaNeural'))

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

# ============================================
# /recap - Video ကနေ ဇာတ်လမ်းအနှစ်ချုပ် ထုတ်ခြင်း
# ============================================
@bot.message_handler(commands=['recap'])
def recap_command(message):
    msg = bot.reply_to(message, "🎬 Recap လုပ်ချင်တဲ့ Video ဖိုင် (MP4) ကို ပို့ပါ။")
    bot.register_next_step_handler(msg, process_recap)

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
        
        # Recap အတွက် Gemini 3.6 Flash ကို သုံးမယ်
        url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent"
        params = {"key": GEMINI_API_KEY}
        prompt = f"""အောက်ပါ Video Transcript ကိုဖတ်ပြီး ရုပ်ရှင်ဇာတ်လမ်းအနှစ်ချုပ် (Movie Recap) ပုံစံနဲ့ မြန်မာလို ရေးပါ။ 
        ဇာတ်လမ်းအကျဉ်း၊ အဓိကဇာတ်ကောင်တွေ၊ ဇာတ်လမ်းအဆုံးသတ်ကို ထည့်သွင်းပါ။
        Transcript: {transcript[:5000]}"""
        data = {"contents": [{"parts": [{"text": prompt}]}]}
        response = requests.post(url, headers={"Content-Type": "application/json"}, params=params, json=data, timeout=60)
        
        if response.status_code == 200:
            result = response.json()
            recap = result["candidates"][0]["content"]["parts"][0]["text"]
        else:
            recap = f"Recap generation failed: {response.status_code} - {response.text}"
        
        bot.reply_to(message, f"🎬 **Movie Recap**\n\n{recap}", parse_mode='Markdown')
        bot.send_document(message.chat.id, io.BytesIO(recap.encode('utf-8')), visible_file_name="recap.txt", caption="📄 ဒီမှာ Recap စာသားဖိုင်ပါ။")
        bot.delete_message(message.chat.id, status.message_id)
    except Exception as e:
        bot.reply_to(message, f"❌ အမှားဖြစ်သွားတယ်: {str(e)}")

# ============================================
# ၆။ Webhook သတ်မှတ်ခြင်း (Gunicorn စတင်တာနဲ့)
# ============================================
if RENDER_URL:
    webhook_url = f"{RENDER_URL}/webhook"
    bot.remove_webhook()
    bot.set_webhook(url=webhook_url)
    print(f"✅ Webhook set to: {webhook_url}")
else:
    print("⚠️ RENDER_EXTERNAL_URL not set! Webhook not configured.")

# ============================================
# ၇။ Main Entry Point (Flask)
# ============================================
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
