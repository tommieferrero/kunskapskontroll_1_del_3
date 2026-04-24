from flask import Flask, render_template, request, jsonify
from datetime import datetime, date, timedelta
import sqlite3
import json
import pyttsx3
import threading
import time

# --- AI Imports ---
import whisper
import sounddevice as sd
import numpy as np
import joblib
from openai import OpenAI
import pygame
import os

# =====================================================================
# AI INITIALISERING (Laddas en gång vid start)
# =====================================================================
print("Startar Eda AI-motor...")
client = OpenAI(api_key="api_nyckel_borttagen_för_säkerhet_kan_applicera_json")
modell_whisper = whisper.load_model("base")
laddad_modell = joblib.load("intent_model.pkl")
laddad_vectorizer = joblib.load("vectorizer.pkl")

# =====================================================================
# APP & CONFIG INITIALISERING
# =====================================================================
app = Flask(__name__)

with open('config.json') as f:
    config = json.load(f)

TRASH_SCHEDULE = {
    0: {'type': 'Plastica/Metallo', 'strong': '#4488ff', 'soft': '#1a2a4a'},
    1: {'type': 'Vetro/Organico',   'strong': '#ffcc00', 'soft': '#3a3000'},
    2: {'type': 'Residuo',          'strong': '#aaaaaa', 'soft': '#2a2a2a'},
    3: {'type': 'Carta/Cartone',    'strong': '#aaaaaa', 'soft': '#2a2a2a'},
    4: {'type': 'Organico',         'strong': '#aa6622', 'soft': '#2a1a0a'},
    5: {'type': 'Ingen sophantering imorgon', 'strong': '#dddddd', 'soft': '#2a2a2a'},
    6: {'type': 'Organico',         'strong': '#aa6622', 'soft': '#2a1a0a'},
}

# =====================================================================
# AI HJÄLPFUNKTIONER (Hjärnan och Munnen)
# =====================================================================
def tvatta_text(text):
    ersattningar = {"å": "a", "ä": "a", "ö": "o", "Å": "A", "Ä": "A", "Ö": "O"}
    for gammal, ny in ersattningar.items():
        text = text.replace(gammal, ny)
    return text

def ai_extrahera_kalender(text):
    saker_text = tvatta_text(text)
    
    from datetime import datetime
    nu = datetime.now()
    dagens_datum = nu.strftime('%Y-%m-%d')
    aktuell_tid = nu.strftime('%H:%M')
    veckodag = nu.strftime('%A')
    
    system_instruktion = f"""
    You are Eda, a highly precise family calendar assistant.
    CURRENT CONTEXT: Today is {veckodag}, {dagens_datum}. The current time is {aktuell_tid}.
    
    Return ONLY a valid JSON object with exactly these keys: person_id, title, event_time.
    
    Rules for person_id: 
    1=Mamma, 2=Pappa, 3=Robin, 4=Colin, 5=Mio, 6=Mika. 
    If you cannot find a matching name, return 0.
    
    Rules for title: Short Swedish title, max 3 words (e.g. "Fotboll", "Läkarbesök").
    
    Rules for event_time:
    1. The format MUST be 'YYYY-MM-DD HH:MM'.
    2. Step 1 - Find the date: Translate words like "imorgon" (tomorrow), "idag" (today) based on today's date ({dagens_datum}). If no day is mentioned, use {dagens_datum}.
    3. Step 2 - Find the time: Look carefully for words like "klockan 18", "kl 15", "halv fyra" (15:30) in the user's text. 
    4. Step 3 - Combine them. ONLY use 12:00 if the user absolutely did not mention any time at all.

    Examples:
    User: "Lägg till fotboll för Colin klockan 18 imorgon"
    JSON: {{"person_id": 4, "title": "Fotboll", "event_time": "2026-04-22 18:00"}}
    
    User: "Robin har tandläkare på fredag kl 14:15"
    JSON: {{"person_id": 3, "title": "Tandläkare", "event_time": "2026-04-24 14:15"}}
    """
    
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": system_instruktion},
                {"role": "user", "content": saker_text}
            ],
            temperature=0
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        return {"error": f"API-fel: {e}"}

def eda_pratar(text_att_saga):
    print(f"🔊 Eda säger: '{text_att_saga}'")
    ljudfil = "eda_svar.mp3"
    
    try:
        with client.audio.speech.with_streaming_response.create(
            model="tts-1",
            voice="nova",
            input=text_att_saga
        ) as response:
            response.stream_to_file(ljudfil)
            
        pygame.mixer.init()
        pygame.mixer.music.load(ljudfil)
        pygame.mixer.music.play()
        
        while pygame.mixer.music.get_busy():
            time.sleep(0.1)
            
        pygame.mixer.quit()
        if os.path.exists(ljudfil):
            os.remove(ljudfil)
            
    except Exception as e:
        print(f"Kunde inte spela upp ljudet. Fel: {e}")

# =====================================================================
# DATABAS OCH BAKGRUNDS-TRÅDAR
# =====================================================================
def init_db():
    with sqlite3.connect('project_e-da.db', timeout=10) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS event
                     (id INTEGER PRIMARY KEY,
                      person_id INTEGER,
                      title TEXT,
                      event_time TEXT,
                      is_active INTEGER,
                      is_alerted INTEGER DEFAULT 0)''')
        try:
            conn.execute("ALTER TABLE event ADD COLUMN is_alerted INTEGER DEFAULT 0")
        except:
            pass
        conn.execute('''CREATE TABLE IF NOT EXISTS avvikelse
                     (id INTEGER PRIMARY KEY,
                      typ TEXT,
                      beskrivning TEXT,
                      barn TEXT,
                      datum TEXT)''')
        conn.commit()

def cleanup_old_avvikelser():
    while True:
        yesterday = (date.today() - timedelta(days=1)).strftime('%Y-%m-%d')
        with sqlite3.connect('project_e-da.db', timeout=10) as conn:
            conn.execute("DELETE FROM avvikelse WHERE datum < ?", (yesterday,))
            conn.commit()
        time.sleep(3600)

def speak(text):
    def run():
        engine = pyttsx3.init()
        for voice in engine.getProperty('voices'):
            if 'english' in voice.name.lower() or 'en' in voice.id.lower():
                engine.setProperty('voice', voice.id)
                break
        engine.setProperty('rate', 145)
        engine.say(text)
        engine.runAndWait()
        engine.stop()
    threading.Thread(target=run, daemon=True).start()

def get_person_name(person_id):
    for person in config['people']:
        if person['id'] == person_id:
            return person['name']
    return "Unknown"

@app.template_filter('person_name')
def person_name_filter(person_id):
    return get_person_name(person_id)

def get_events():
    with sqlite3.connect('project_e-da.db', timeout=10) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM event ORDER BY event_time")
        return cursor.fetchall()

def get_future_events(week_end):
    with sqlite3.connect('project_e-da.db', timeout=10) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, person_id, title, event_time FROM event
            WHERE date(event_time) > ? AND is_active = 1
            ORDER BY event_time ASC
        """, (week_end,))
        return cursor.fetchall()

def get_history():
    two_weeks_ago = (date.today() - timedelta(days=14)).strftime('%Y-%m-%d')
    today = date.today().strftime('%Y-%m-%d')
    with sqlite3.connect('project_e-da.db', timeout=10) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, person_id, title, event_time FROM event
            WHERE date(event_time) >= ? AND date(event_time) <= ?
            ORDER BY event_time DESC
        """, (two_weeks_ago, today))
        return cursor.fetchall()

def get_avvikelser():
    today = date.today().strftime('%Y-%m-%d')
    with sqlite3.connect('project_e-da.db', timeout=10) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM avvikelse WHERE datum >= ? ORDER BY datum ASC", (today,))
        return cursor.fetchall()

def check_events():
    while True:
        now = datetime.now().strftime('%Y-%m-%dT%H:%M')
        with sqlite3.connect('project_e-da.db', timeout=10) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, person_id, title FROM event
                WHERE is_active = 1 AND is_alerted = 0 AND event_time = ?
            """, (now,))
            due = cursor.fetchall()
            for event_id, person_id, title in due:
                name = get_person_name(person_id)
                conn.execute("UPDATE event SET is_alerted = 1 WHERE id = ?", (event_id,))
                conn.commit()
                speak(f"Notification for {name}")
                time.sleep(30)
                conn2 = sqlite3.connect('project_e-da.db', timeout=10)
                conn2.execute("UPDATE event SET is_active = 0 WHERE id = ?", (event_id,))
                conn2.commit()
                conn2.close()
        time.sleep(30)

# =====================================================================
# FLASK ROUTES (Frontend endpoints)
# =====================================================================
@app.route('/alerts')
def alerts():
    with sqlite3.connect('project_e-da.db', timeout=10) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, person_id, title FROM event WHERE is_active = 1 AND is_alerted = 1")
        rows = cursor.fetchall()
    return jsonify([{'id': r[0], 'person_id': r[1], 'name': get_person_name(r[1]), 'title': r[2]} for r in rows])

@app.route('/upcoming')
def upcoming():
    now = datetime.now()
    soon = (now + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M')
    past = (now - timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M')
    with sqlite3.connect('project_e-da.db', timeout=10) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, person_id, title, event_time FROM event WHERE event_time >= ? AND event_time <= ?", (past, soon))
        rows = cursor.fetchall()
    return jsonify([{'id': r[0], 'person_id': r[1], 'title': r[2], 'event_time': r[3]} for r in rows])

@app.route('/delete/<int:event_id>', methods=['DELETE'])
def delete_event(event_id):
    with sqlite3.connect('project_e-da.db', timeout=10) as conn:
        conn.execute("DELETE FROM event WHERE id = ?", (event_id,))
        conn.commit()
    return jsonify({'status': 'ok'})

@app.route('/add_avvikelse', methods=['POST'])
def add_avvikelse():
    typ = request.form['typ']
    datum = request.form['datum']
    beskrivning = request.form.get('beskrivning', '')
    barn = request.form.get('barn', '')
    with sqlite3.connect('project_e-da.db', timeout=10) as conn:
        conn.execute("INSERT INTO avvikelse (typ, beskrivning, barn, datum) VALUES (?, ?, ?, ?)",
                     (typ, beskrivning, barn, datum))
        conn.commit()
    return jsonify({'status': 'ok'})

@app.route('/delete_avvikelse/<int:avv_id>', methods=['DELETE'])
def delete_avvikelse(avv_id):
    with sqlite3.connect('project_e-da.db', timeout=10) as conn:
        conn.execute("DELETE FROM avvikelse WHERE id = ?", (avv_id,))
        conn.commit()
    return jsonify({'status': 'ok'})

@app.route('/')
def index():
    today = date.today()
    yesterday = today - timedelta(days=1)
    tomorrow = today + timedelta(days=1)
    week_start = today + timedelta(days=2)
    week_end = today + timedelta(days=7)
    now = datetime.now()
    soon = now + timedelta(hours=1)
    past = now - timedelta(hours=1)
    weekday = today.weekday()
    trash = TRASH_SCHEDULE.get(weekday)
    is_weekend = weekday in (5, 6)
    future_events = get_future_events(week_end.strftime('%Y-%m-%d'))

    return render_template('index.html',
        config=config,
        events=get_events(),
        history=get_history(),
        avvikelser=get_avvikelser(),
        future_events=future_events,
        trash=trash,
        is_weekend=is_weekend,
        today=today.strftime('%Y-%m-%d'),
        yesterday=yesterday.strftime('%Y-%m-%d'),
        tomorrow=tomorrow.strftime('%Y-%m-%d'),
        week_start=week_start.strftime('%Y-%m-%d'),
        week_end=week_end.strftime('%Y-%m-%d'),
        now=now.strftime('%Y-%m-%dT%H:%M'),
        soon=soon.strftime('%Y-%m-%dT%H:%M'),
        past=past.strftime('%Y-%m-%dT%H:%M')
    )

@app.route('/add', methods=['POST'])
def add_event():
    person_id = int(request.form['person_id'])
    title = request.form['title']
    event_time = request.form['event_time']
    with sqlite3.connect('project_e-da.db', timeout=10) as conn:
        conn.execute("INSERT INTO event (person_id, title, event_time, is_active, is_alerted) VALUES (?, ?, ?, ?, ?)",
                     (person_id, title, event_time, 1, 0))
        conn.commit()
    speak("Task is scheduled")
    return jsonify({'status': 'ok'})

# =====================================================================
# AI RÖST-INTEGRATION (Lyssna-knappen)
# =====================================================================
@app.route('/lyssna', methods=['POST'])
def lyssna_pa_rost():
    duration = 5
    sample_rate = 16000
    
    # Spela in ljud
    print("🎤 Dashboarden lyssnar...")
    audio_data = sd.rec(int(duration * sample_rate), samplerate=sample_rate, channels=1, dtype='float32')
    sd.wait()
    
    audio_flat = audio_data.flatten()
    if np.max(np.abs(audio_flat)) < 0.01:
        eda_pratar("Jag hörde ingenting.")
        return jsonify({"status": "error", "message": "Inget ljud uppfattades."})
        
    audio_flat = audio_flat / np.max(np.abs(audio_flat))
    
    # 1. Transkribera (Whisper)
    fusk_lapp = "Mamma, Pappa, Robin, Colin, Mio, Mika."
    resultat = modell_whisper.transcribe(audio_flat, language="sv", fp16=False, initial_prompt=fusk_lapp)
    anvandar_text = resultat['text'].strip()
    
    if not anvandar_text:
        eda_pratar("Jag uppfattade inga ord.")
        return jsonify({"status": "error", "message": "Inga ord."})
        
    print(f"🗣️ Användaren sa: '{anvandar_text}'")
        
    # 2. Filtrera (Egen ML-modell)
    text_vec = laddad_vectorizer.transform([anvandar_text])
    if laddad_modell.predict(text_vec)[0] == 0:
        eda_pratar("Jag hanterar bara kalendern.")
        return jsonify({"status": "error", "message": "Ogiltigt ämne."})
        
    # 3. Extrahera (OpenAI Hjärnan)
    extraherad_data = ai_extrahera_kalender(anvandar_text)
    
    if "error" in extraherad_data:
        eda_pratar("Ett fel uppstod vid tolkningen.")
        return jsonify({"status": "error", "message": extraherad_data['error']})
        
    person = extraherad_data.get('person_id')
    titel = extraherad_data.get('title')
    tid = extraherad_data.get('event_time')
    
    if person == 0 or not titel:
        eda_pratar("Jag förstod inte vem eller vad det gällde.")
        return jsonify({"status": "error", "message": "Otydlig data."})
        
    # 4. Spara till projektets databas
    try:
        with sqlite3.connect('project_e-da.db', timeout=10) as conn:
            conn.execute("INSERT INTO event (person_id, title, event_time, is_active, is_alerted) VALUES (?, ?, ?, ?, ?)",
                         (person, titel, tid, 1, 0))
            conn.commit()
            
        # 5. Eda bekräftar (Munnen)
        eda_pratar(f"Jag har lagt till {titel} i kalendern.")
        return jsonify({"status": "success", "message": f"Tillagt: {titel}"})
        
    except Exception as e:
        eda_pratar("Ett databasfel uppstod.")
        return jsonify({"status": "error", "message": str(e)})

# =====================================================================
# STARTA SERVER
# =====================================================================
if __name__ == '__main__':
    init_db()
    threading.Thread(target=check_events, daemon=True).start()
    threading.Thread(target=cleanup_old_avvikelser, daemon=True).start()
    app.run(host='0.0.0.0', port=5000, debug=False)