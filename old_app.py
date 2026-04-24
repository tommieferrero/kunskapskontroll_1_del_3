from flask import Flask, render_template, request, jsonify
from datetime import datetime, date, timedelta
import sqlite3
import json
import pyttsx3
import threading
import time


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


if __name__ == '__main__':
    init_db()
    threading.Thread(target=check_events, daemon=True).start()
    threading.Thread(target=cleanup_old_avvikelser, daemon=True).start()
    app.run(host='0.0.0.0', port=5000, debug=False)
