import os
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"

import tkinter as tk
from tkinter import messagebox
import threading
import speech_recognition as sr
from gtts import gTTS
import datetime
import time
import tempfile
import requests
import feedparser
from PIL import Image, ImageTk
import pygame
from dateutil import parser as dtparser

# ---------------- CONFIG ----------------
WEATHER_API = "98e538d85b72214c9ea1a6d936ad7d63"
CITY = "Kottayam"

# If your mic isn't detected correctly, set this after seeing the mic list in terminal.
MIC_INDEX = None

pygame.mixer.init()

reminders = []  # (timestamp, text)

# Session state
session_active = False
stop_event = threading.Event()

# ---------------- UI SAFE ----------------
def set_status(text: str):
    status.set(text)

def ui_safe(fn, *args):
    root.after(0, lambda: fn(*args))

# ---------------- SPEAK (gTTS + pygame, Windows-safe) ----------------
def speak(text: str):
    def _worker():
        print("Assistant:", text)
        mp3_path = None
        try:
            tts = gTTS(text=text, lang="en")
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as fp:
                mp3_path = fp.name
            tts.save(mp3_path)

            pygame.mixer.music.load(mp3_path)
            pygame.mixer.music.play()

            while pygame.mixer.music.get_busy():
                if stop_event.is_set():
                    pygame.mixer.music.stop()
                    break
                time.sleep(0.05)

            pygame.mixer.music.stop()
            try:
                pygame.mixer.music.unload()
            except Exception:
                pass

        except Exception as e:
            print("TTS error:", e)
            ui_safe(set_status, "Audio failed")

        finally:
            if mp3_path and os.path.exists(mp3_path):
                for _ in range(12):
                    try:
                        os.remove(mp3_path)
                        break
                    except PermissionError:
                        time.sleep(0.1)

    threading.Thread(target=_worker, daemon=True).start()

# Optional: speak and WAIT until finished (for smooth turn-taking)
def speak_blocking(text: str):
    print("Assistant:", text)
    mp3_path = None
    try:
        tts = gTTS(text=text, lang="en")
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as fp:
            mp3_path = fp.name
        tts.save(mp3_path)

        pygame.mixer.music.load(mp3_path)
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            if stop_event.is_set():
                pygame.mixer.music.stop()
                break
            time.sleep(0.05)

        pygame.mixer.music.stop()
        try:
            pygame.mixer.music.unload()
        except Exception:
            pass

    except Exception as e:
        print("TTS error:", e)
        ui_safe(set_status, "Audio failed")

    finally:
        if mp3_path and os.path.exists(mp3_path):
            for _ in range(12):
                try:
                    os.remove(mp3_path)
                    break
                except PermissionError:
                    time.sleep(0.1)

# ---------------- LISTEN ----------------
def listen_raw(timeout=8, phrase_time_limit=7) -> str:
    r = sr.Recognizer()

    # (Helpful) show mic devices one time per run
    # comment out if you don't want it in terminal
    try:
        mics = sr.Microphone.list_microphone_names()
        if mics:
            print("\n--- Microphones ---")
            for i, name in enumerate(mics):
                print(i, ":", name)
            print("-------------------\n")
    except:
        pass

    try:
        with sr.Microphone(device_index=MIC_INDEX) as source:
            ui_safe(set_status, "Listening... (say something)")
            r.dynamic_energy_threshold = True
            r.adjust_for_ambient_noise(source, duration=0.6)
            audio = r.listen(source, timeout=timeout, phrase_time_limit=phrase_time_limit)

        text = r.recognize_google(audio).lower().strip()
        ui_safe(set_status, f"You: {text}")
        print("You:", text)
        return text

    except sr.WaitTimeoutError:
        ui_safe(set_status, "No voice detected")
        return ""
    except Exception as e:
        ui_safe(set_status, "Could not understand")
        print("Listen error:", e)
        return ""

# ---------------- WEATHER / NEWS ----------------
def get_weather_text():
    url = f"https://api.openweathermap.org/data/2.5/weather?q={CITY}&appid={WEATHER_API}&units=metric"
    data = requests.get(url, timeout=10).json()
    if str(data.get("cod")) != "200":
        return None
    temp = data["main"]["temp"]
    desc = data["weather"][0]["description"]
    return f"The temperature in {CITY} is {temp} degrees with {desc}."

def get_news_headlines(n=3):
    feed = feedparser.parse("https://news.google.com/rss?hl=en-IN&gl=IN&ceid=IN:en")
    if not feed.entries:
        return []
    return [e.title for e in feed.entries[:n]]

# ---------------- REMINDER PARSING ----------------
def parse_time_natural(text: str):
    if not text.strip():
        return None
    now = datetime.datetime.now()
    lower = text.lower()

    if "tomorrow" in lower:
        base = now + datetime.timedelta(days=1)
        lower = lower.replace("tomorrow", "").strip()
        dt = dtparser.parse(lower, default=base)
        return dt

    if "today" in lower:
        base = now
        lower = lower.replace("today", "").strip()
        dt = dtparser.parse(lower, default=base)
        return dt

    dt = dtparser.parse(text, default=now)
    if dt <= now:
        dt = dt + datetime.timedelta(days=1)
    return dt

# ---------------- REMINDERS LOOP ----------------
def check_reminders():
    while True:
        try:
            now = time.time()
            if reminders and reminders[0][0] <= now:
                _, text = reminders.pop(0)
                ui_safe(set_status, f"Reminder: {text}")
                # if a session is active, speaking is fine
                speak(f"Hey! Reminder: {text}")
        except Exception as e:
            print("Reminder loop error:", e)
        time.sleep(1)

# ---------------- CONVERSATION ENGINE ----------------
def should_end_session(text: str) -> bool:
    t = text.lower().strip()
    return ("stop" in t) or ("exit" in t) or ("quit" in t)

def ask(question: str) -> str:
    # Ask with voice, then listen for answer
    ui_safe(set_status, question)
    speak_blocking(question)
    if stop_event.is_set():
        return ""
    return listen_raw(timeout=10, phrase_time_limit=8)

def handle_command(command: str):
    if not command:
        speak_blocking("I didn't catch that. Say it again or say stop to end.")
        return

    if should_end_session(command):
        end_session()
        return

    if "time" in command:
        now = datetime.datetime.now().strftime("%I:%M %p")
        speak_blocking(f"It's {now}.")
        ui_safe(set_status, f"Time: {now}")
        return

    if "date" in command or "day" in command:
        today = datetime.datetime.now().strftime("%B %d, %Y")
        speak_blocking(f"Today is {today}.")
        ui_safe(set_status, f"Date: {today}")
        return

    if "weather" in command:
        ui_safe(set_status, "Fetching weather...")
        try:
            w = get_weather_text()
            speak_blocking(w if w else "Sorry, I couldn't fetch the weather right now.")
        except:
            speak_blocking("Sorry, I couldn't fetch the weather right now.")
        return

    if "news" in command:
        ui_safe(set_status, "Fetching news...")
        try:
            headlines = get_news_headlines(3)
            if not headlines:
                speak_blocking("No news available right now.")
                return
            speak_blocking("Here are the latest headlines.")
            for h in headlines:
                if stop_event.is_set():
                    return
                speak_blocking(h)
        except:
            speak_blocking("Sorry, I couldn't fetch the news right now.")
        return

    if "remind me" in command or "reminder" in command or "set reminder" in command:
        what = ask("Sure. What should I remind you about?")
        if stop_event.is_set() or should_end_session(what) or not what:
            speak_blocking("Okay, cancelled.")
            return

        when_text = ask("Got it. When should I remind you?")
        if stop_event.is_set() or should_end_session(when_text) or not when_text:
            speak_blocking("Okay, cancelled.")
            return

        dt = None
        try:
            dt = parse_time_natural(when_text)
        except:
            dt = None

        if not dt:
            speak_blocking("Sorry, I couldn't understand the time. Try again.")
            return

        reminders.append((dt.timestamp(), what))
        reminders.sort(key=lambda x: x[0])
        nice_time = dt.strftime("%B %d at %I:%M %p")
        speak_blocking(f"Done. I'll remind you to {what} on {nice_time}.")
        ui_safe(set_status, f"Reminder set: {what} @ {nice_time}")
        return

    # Fallback
    speak_blocking("I can do date, time, weather, news, and reminders. What would you like?")

# ---------------- SESSION CONTROL ----------------
def start_session():
    global session_active
    if session_active:
        ui_safe(set_status, "Session already running")
        return

    session_active = True
    stop_event.clear()
    ui_safe(set_status, "Session started")
    ui_safe(update_mic_button_text)

    def loop():
        # greet once
        speak_blocking("Hello! I'm listening. Say date, time, weather, news, or remind me. Say stop to end.")

        while session_active and not stop_event.is_set():
            cmd = listen_raw(timeout=12, phrase_time_limit=8)

            if stop_event.is_set():
                break

            if cmd == "":
                # silence -> keep listening
                continue

            if should_end_session(cmd):
                break

            handle_command(cmd)

            # after replying, automatically listen again
            # (loop continues)

        # end session
        end_session()

    threading.Thread(target=loop, daemon=True).start()

def end_session():
    global session_active
    if not session_active:
        return
    session_active = False
    stop_event.set()

    # stop any playing audio immediately
    try:
        pygame.mixer.music.stop()
        pygame.mixer.music.unload()
    except:
        pass

    ui_safe(set_status, "Session ended")
    ui_safe(update_mic_button_text)

def toggle_session():
    # Mic button acts as START / BREAK (stop)
    if session_active:
        end_session()
    else:
        start_session()

# ---------------- UI ----------------
root = tk.Tk()
root.title("AI Voice Assistant")
root.geometry("420x600")
root.configure(bg="#caa6ff")

status = tk.StringVar(value="Ready")

tk.Label(root, text="AI Voice Assistant",
         font=("Arial", 18, "bold"),
         bg="#caa6ff").pack(pady=12)

tk.Label(root, textvariable=status, bg="#caa6ff", font=("Arial", 11)).pack(pady=8)

# Mic button (image optional)
mic_btn = None
try:
    img = Image.open("guoc.png").resize((80, 80))
    mic_img = ImageTk.PhotoImage(img)
    mic_btn = tk.Button(root, image=mic_img, command=toggle_session, bd=0)
    mic_btn.pack(pady=18)
except:
    mic_btn = tk.Button(root, text="🎤 Start / Stop", font=("Arial", 16), command=toggle_session)
    mic_btn.pack(pady=18)

def update_mic_button_text():
    # only updates text buttons; image button keeps same look
    if isinstance(mic_btn, tk.Button) and mic_btn.cget("image") == "":
        mic_btn.config(text=("⏹ Stop Listening" if session_active else "🎤 Start Listening"))

# Optional: manual exit button
tk.Button(root, text="Exit App", command=root.destroy).pack(pady=10)

# Background reminders
threading.Thread(target=check_reminders, daemon=True).start()

root.mainloop()




