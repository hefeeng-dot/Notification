"""
Blink — lembretes recorrentes discretos para Windows.

Pop-up minimalista no canto da tela, ícone na bandeja, lembretes rápidos por
atalho global (Ctrl+Alt+L), contador e meta diária, foco automático em chamadas
e tela cheia, e pomodoro (Ctrl+Alt+P).

Executar:  pyw blink.pyw         (abre a janela)
           pyw blink.pyw --min   (inicia só na bandeja)
"""
import base64
import ctypes
import datetime as dt
import json
import math
import os
import queue
import shutil
import random
import re
import socket
import struct
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
import wave
import webbrowser
import tkinter as tk
import tkinter.font as tkfont
from ctypes import wintypes

from PIL import Image, ImageChops, ImageDraw, ImageTk

APP = "Blink"
OLD_APP = "Lembretes"                         # nome antigo: pasta de dados e autostart
FROZEN = getattr(sys, "frozen", False)          # rodando como .exe (PyInstaller)
BASE = os.path.dirname(sys.executable if FROZEN else os.path.abspath(__file__))
DATA_DIR = os.path.join(os.environ.get("APPDATA") or BASE, APP)
CONFIG = os.path.join(DATA_DIR, "config.json")
SOUND_DIR = os.path.join(DATA_DIR, "sons")
LEGACY_CONFIG = os.path.join(BASE, "config.json")   # local antigo, ao lado do script


def _migrate_old_data_dir():
    """Traz os dados da pasta antiga (%APPDATA%/Lembretes) para a pasta nova na primeira execução."""
    old = os.path.join(os.path.dirname(DATA_DIR), OLD_APP)
    if os.path.exists(DATA_DIR) or not os.path.isdir(old):
        return
    try:
        os.rename(old, DATA_DIR)
    except OSError:
        try:
            shutil.copytree(old, DATA_DIR)
        except OSError:
            pass


_migrate_old_data_dir()
PORT = 47821          # garante uma única instância
GRACE_MIN = 3         # tolerância para disparar um lembrete atrasado
AWAY_SEC = 90         # sem mexer no mouse/teclado por esse tempo = ausente
MAX_POPUPS = 4
DIAS = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]

PALETTE = ["#8ea4d2", "#7fb7c9", "#9bb59a", "#d6b98c", "#d49a9a", "#b3a1d6", "#9a9a9a"]
POMO_FOCO, POMO_PAUSA = "#c98f8a", "#9bb59a"

THEMES = {
    "claro": dict(bg="#f7f6f3", row_hover="#efeee9", hover="#e5e3dc", line="#e7e5df",
                  border="#d9d7d0", input="#ffffff", surface="#ffffff", fg="#1b1b1d",
                  sub="#6f6e6a", faint="#a9a7a0", inv="#ffffff", knob_off="#ffffff",
                  danger="#b5534f", dark=False),
    "escuro": dict(bg="#111113", row_hover="#18181b", hover="#26262a", line="#212124",
                   border="#2f2f34", input="#18181b", surface="#1c1c1f", fg="#ededf0",
                   sub="#8e8e96", faint="#5b5b62", inv="#111113", knob_off="#8e8e96",
                   danger="#d98a85", dark=True),
}

G_ADD, G_REMOVE, G_PAUSE, G_PLAY, G_SETTINGS = "", "", "", "", ""
G_TIMER, G_SKIP, G_STOP, G_CLOSE, G_CHECK = "", "", "", "", ""

# Ilha no topo da tela: sempre escura, como uma "dynamic island"
ISL = dict(bg="#0c0c0d", fg="#f2f2f4", sub="#9b9ba4", faint="#5e5e66", line="#232326")
KEY = "#010203"   # cor tornada transparente na janela da ilha

# Períodos do dia usados pelas atividades do Notion
PERIODS = [("golden", "Golden Hour"), ("manha", "Manhã"), ("tarde", "Tarde"), ("noite", "Noite")]
PERIOD_COLORS = {"golden": "#d6b98c", "manha": "#8ea4d2", "tarde": "#7fb7c9", "noite": "#b3a1d6", "": "#9a9a9a"}
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro",
         "outubro", "novembro", "dezembro"]
DIAS_LONGOS = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]

SOUNDS = [("nenhum", "Nenhum"), ("madeira", "Madeira"), ("gota", "Gota"), ("sino", "Sino")]
VOLUMES = {"baixo": 0.16, "medio": 0.32, "alto": 0.55}

DEFAULT_SETTINGS = {
    "duracao": 8, "adiar_min": 10, "opacidade": 0.97, "tema": "claro", "posicao": "inferior",
    "som": "madeira", "volume": "medio", "iniciar_com_windows": False,
    "foco_chamadas": True, "foco_tela_cheia": True,
    "pomo_foco": 25, "pomo_pausa": 5, "pomo_longa": 15, "pomo_ciclos": 4, "pomo_segurar": True,
    "ilha": True, "olhos_min": 30,
    "notion_token": "", "notion_db": "", "notion_avisar": True,
    "notion_horarios": {"golden": "06:00", "manha": "08:00", "tarde": "13:00", "noite": "19:00"},
}

DEFAULT_CFG = {
    "settings": DEFAULT_SETTINGS,
    "lembretes": [
        {"titulo": "Beber água", "mensagem": "Um copo agora.", "tipo": "intervalo",
         "intervalo": 60, "inicio": "08:00", "fim": "18:00", "horarios": [],
         "dias": [0, 1, 2, 3, 4], "cor": "#7fb7c9", "ativo": True, "meta": 8},
        {"titulo": "Alongar e ajustar a postura", "mensagem": "Levante, estique, respire.",
         "tipo": "intervalo", "intervalo": 90, "inicio": "09:00", "fim": "18:00", "horarios": [],
         "dias": [0, 1, 2, 3, 4], "cor": "#9bb59a", "ativo": True, "meta": 0},
        {"titulo": "Almoço", "mensagem": "", "tipo": "horarios", "intervalo": 60,
         "inicio": "08:00", "fim": "18:00", "horarios": ["12:00"],
         "dias": [0, 1, 2, 3, 4, 5, 6], "cor": "#d6b98c", "ativo": False, "meta": 0},
    ],
    "avulsos": [],
    "historico": {},
}

_OLD_TITLES = {"💧 Beber água": "Beber água", "🧘 Alongar e ajustar a postura": "Alongar e ajustar a postura",
               "🍽️ Almoço": "Almoço"}
_OLD_COLORS = {"#6c8cff": "#8ea4d2", "#4fb3ff": "#7fb7c9", "#7bd88f": "#9bb59a",
               "#ffb86c": "#d6b98c", "#ff6b81": "#d49a9a", "#c792ea": "#b3a1d6"}

u32 = ctypes.windll.user32
k32 = ctypes.windll.kernel32


# ───────────────────────── tempo e recorrência ─────────────────────────

def parse_hm(s):
    m = re.fullmatch(r"\s*(\d{1,2}):?(\d{2})\s*", s or "")
    if not m:
        raise ValueError(s)
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        raise ValueError(s)
    return h * 60 + mi


def fmt_hm(minutes):
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def occurrences(lemb, date):
    """Minutos do dia (desde 00:00) em que o lembrete dispara nesta data."""
    if date.weekday() not in lemb.get("dias", []):
        return []
    try:
        if lemb.get("tipo") == "intervalo":
            a, b = parse_hm(lemb["inicio"]), parse_hm(lemb["fim"])
            return list(range(a, b + 1, max(1, int(lemb.get("intervalo", 60)))))
        return sorted({parse_hm(h) for h in lemb.get("horarios", [])})
    except (ValueError, KeyError):
        return []


def next_occurrence(lemb, now):
    for d in range(8):
        date = now.date() + dt.timedelta(days=d)
        for m in occurrences(lemb, date):
            when = dt.datetime.combine(date, dt.time(m // 60, m % 60))
            if when > now:
                return when
    return None


def fmt_when(when, now, today_word=""):
    if when is None:
        return ""
    delta = (when.date() - now.date()).days
    day = today_word if delta == 0 else "amanhã" if delta == 1 else DIAS[when.weekday()].lower()
    return f"{day} {when:%H:%M}".strip()


def describe_when(when, now):
    delta = (when.date() - now.date()).days
    day = "hoje" if delta == 0 else "amanhã" if delta == 1 else DIAS[when.weekday()].lower()
    base = f"{day} às {when:%H:%M}"
    mins = math.ceil((when - now).total_seconds() / 60)
    if 0 < mins < 180:
        dur = f"{mins} min" if mins < 60 else f"{mins // 60} h {mins % 60:02d}"
        return f"em {dur}  ·  {base}"
    return base


def dias_texto(dias):
    dias = sorted(dias)
    if dias == list(range(7)):
        return "todos os dias"
    if dias == [0, 1, 2, 3, 4]:
        return "seg a sex"
    if dias == [5, 6]:
        return "fins de semana"
    return ", ".join(DIAS[d].lower() for d in dias) or "nenhum dia"


def recorrencia_texto(l):
    if l.get("tipo") == "intervalo":
        n = int(l.get("intervalo", 60))
        cada = f"{n // 60} h" if n % 60 == 0 else f"{n} min" if n < 60 else f"{n // 60} h {n % 60:02d}"
        base = f"A cada {cada}, {l.get('inicio')}–{l.get('fim')}"
    else:
        base = " · ".join(l.get("horarios", [])) or "Sem horários"
    return f"{base}  ·  {dias_texto(l.get('dias', []))}"


def parse_quick(text, now):
    """'17:30 buscar a Sarah', '25 min alongar', 'em 1h ligar', 'amanhã 9h reunião'."""
    s = f" {text.strip()} "
    tomorrow = False
    m = re.search(r"\s(amanh[ãa])(?=\s)", s, re.I)
    if m:
        tomorrow = True
        s = s[:m.start()] + " " + s[m.end():]
    pats = [
        ("hdur", r"\s(?:em|daqui\s+a|\+)\s*(\d{1,2})\s*h(?:oras?)?\s*(?:(\d{1,2})\s*(?:minutos|min|m)?)?(?=\s)"),
        ("mdur", r"\s(?:em\s+|daqui\s+a\s+|\+\s*)?(\d{1,3})\s*(?:minutos|minuto|min|m)(?=\s)"),
        ("hm", r"\s(?:[àa]s\s+)?(\d{1,2})\s*[:h]\s*(\d{2})(?=\s)"),
        ("h", r"\s(?:[àa]s\s+)?(\d{1,2})\s*h(?=\s)"),
    ]
    when = None
    for kind, p in pats:
        m = re.search(p, s, re.I)
        if not m:
            continue
        if kind == "hdur":
            when = now + dt.timedelta(hours=int(m.group(1)), minutes=int(m.group(2) or 0))
        elif kind == "mdur":
            if int(m.group(1)) < 1:
                continue
            when = now + dt.timedelta(minutes=int(m.group(1)))
        else:
            h = int(m.group(1))
            mi = int(m.group(2)) if kind == "hm" else 0
            if h > 23 or mi > 59:
                continue
            when = now.replace(hour=h, minute=mi, second=0, microsecond=0)
            if tomorrow:
                when += dt.timedelta(days=1)
            elif when <= now:
                when += dt.timedelta(days=1)
        s = s[:m.start()] + " " + s[m.end():]
        break
    if when is None:
        raise ValueError("Diga quando — ex.: 17:30, 25 min, em 1h")
    titulo = re.sub(r"\s+", " ", s).strip(" -–—:,.")
    titulo = (titulo[0].upper() + titulo[1:]) if titulo else "Lembrete"
    return titulo, when


# ───────────────────────── Windows ─────────────────────────

def work_area():
    class RECT(ctypes.Structure):
        _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long), ("r", ctypes.c_long), ("b", ctypes.c_long)]
    r = RECT()
    u32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0)
    return r.l, r.t, r.r, r.b


def idle_seconds():
    """Segundos desde o último uso de mouse/teclado (inclui tela bloqueada)."""
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
    li = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
    if not u32.GetLastInputInfo(ctypes.byref(li)):
        return 0.0
    now = k32.GetTickCount() & 0xFFFFFFFF
    return ((now - li.dwTime) & 0xFFFFFFFF) / 1000.0


def mic_in_use():
    """Algum app está usando o microfone agora (chamada no Teams, Meet, Zoom…)."""
    import winreg
    base = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"

    def scan(path):
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
        except OSError:
            return False
        with key:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(key, i)
                except OSError:
                    return False
                i += 1
                if sub == "NonPackaged":
                    if scan(path + "\\NonPackaged"):
                        return True
                    continue
                try:
                    with winreg.OpenKey(key, sub) as sk:
                        start = winreg.QueryValueEx(sk, "LastUsedTimeStart")[0]
                        stop = winreg.QueryValueEx(sk, "LastUsedTimeStop")[0]
                    if start and stop == 0:
                        return True
                except OSError:
                    pass
    return scan(base)


def fullscreen_busy():
    """Vídeo/jogo em tela cheia ou modo apresentação."""
    st = ctypes.c_int(0)
    try:
        if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(st)) != 0:
            return False
    except Exception:
        return False
    return st.value in (2, 3, 4)


def system_light():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 1
    except OSError:
        return True


def hwnd_of(win):
    win.update_idletasks()
    return u32.GetParent(win.winfo_id()) or win.winfo_id()


def _colorref(c):
    return ctypes.c_int(int(c[1:3], 16) | int(c[3:5], 16) << 8 | int(c[5:7], 16) << 16)


def style_window(win, t):
    """Barra de título no tom da janela (Windows 11)."""
    try:
        hwnd = hwnd_of(win)
        dwm = ctypes.windll.dwmapi
        dark = ctypes.c_int(1 if t["dark"] else 0)
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark), 4)
        for attr, col in ((35, t["bg"]), (36, t["fg"]), (34, t["border"])):
            v = _colorref(col)
            dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), 4)
    except Exception:
        pass


def style_floating(win, t, no_activate=True):
    """Cantos arredondados, borda fina e (opcional) sem roubar o foco."""
    try:
        hwnd = hwnd_of(win)
        if no_activate:
            u32.SetWindowLongW(hwnd, -20, u32.GetWindowLongW(hwnd, -20) | 0x80 | 0x08000000)
        dwm = ctypes.windll.dwmapi
        pref = ctypes.c_int(2)
        dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), 4)
        col = _colorref(t["border"])
        dwm.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(col), 4)
    except Exception:
        pass


def force_foreground(hwnd):
    fg = u32.GetForegroundWindow()
    cur = k32.GetCurrentThreadId()
    other = u32.GetWindowThreadProcessId(fg, None)
    if other and other != cur:
        u32.AttachThreadInput(other, cur, True)
    u32.BringWindowToTop(hwnd)
    u32.SetForegroundWindow(hwnd)
    if other and other != cur:
        u32.AttachThreadInput(other, cur, False)


class Hotkeys(threading.Thread):
    """Atalhos globais (funcionam com o app em segundo plano)."""
    WM_HOTKEY, WM_QUIT = 0x0312, 0x0012

    def __init__(self, q, binds):
        super().__init__(daemon=True)
        self.q, self.binds = q, binds          # [(mods, vk, mensagem)]
        self.ok, self.tid = {}, None
        self.ready = threading.Event()

    def run(self):
        self.tid = k32.GetCurrentThreadId()
        for i, (mods, vk, msg) in enumerate(self.binds, 1):
            self.ok[msg] = bool(u32.RegisterHotKey(None, i, mods | 0x4000, vk))
        self.ready.set()
        m = wintypes.MSG()
        while u32.GetMessageW(ctypes.byref(m), None, 0, 0) > 0:
            if m.message == self.WM_HOTKEY and 1 <= m.wParam <= len(self.binds):
                self.q.put(self.binds[m.wParam - 1][2])
        for i in range(1, len(self.binds) + 1):
            u32.UnregisterHotKey(None, i)

    def stop(self):
        if self.tid:
            u32.PostThreadMessageW(self.tid, self.WM_QUIT, 0, 0)


# ───────────────────────── sons ─────────────────────────

_SOUND_LOCK = threading.Lock()


def _synth(name, sr=44100):
    """Sons curtos, graves e abafados (filtro passa-baixa)."""
    if name == "madeira":
        dur = 0.9
        buf = [0.0] * int(sr * dur)
        for t0, f, amp in ((0.0, 330.0, 1.0), (0.14, 247.0, 0.85)):
            s0 = int(t0 * sr)
            for i in range(len(buf) - s0):
                t = i / sr
                atk = 1 - math.exp(-t * 700)
                v = math.sin(2 * math.pi * f * t) * math.exp(-t * 11)
                v += 0.2 * math.sin(2 * math.pi * f * 2.01 * t) * math.exp(-t * 30)
                buf[s0 + i] += v * atk * amp
        cutoff = 1300
    elif name == "gota":
        dur = 0.7
        buf = [0.0] * int(sr * dur)
        ph = 0.0
        for i in range(len(buf)):
            t = i / sr
            f = 360 + 480 * math.exp(-t * 26)
            ph += 2 * math.pi * f / sr
            buf[i] = math.sin(ph) * (1 - math.exp(-t * 500)) * math.exp(-t * 8)
        cutoff = 1600
    else:  # sino suave
        dur = 2.2
        buf = [0.0] * int(sr * dur)
        partials = ((392.0, 1.0, 2.8), (588.0, 0.16, 3.8), (784.0, 0.22, 4.6), (1176.0, 0.07, 7.0))
        for i in range(len(buf)):
            t = i / sr
            atk = 1 - math.exp(-t * 90)
            buf[i] = atk * sum(a * math.sin(2 * math.pi * f * t) * math.exp(-t * d) for f, a, d in partials)
        cutoff = 1000
    a = 1 - math.exp(-2 * math.pi * cutoff / sr)
    y = 0.0
    for i, x in enumerate(buf):
        y += a * (x - y)
        buf[i] = y
    fade = int(0.03 * sr)
    for i in range(fade):
        buf[-1 - i] *= i / fade
    peak = max(abs(v) for v in buf) or 1.0
    return [v / peak for v in buf]


def sound_path(name, vol):
    os.makedirs(SOUND_DIR, exist_ok=True)
    path = os.path.join(SOUND_DIR, f"{name}_{vol}_v1.wav")
    with _SOUND_LOCK:
        if not os.path.exists(path):
            amp = VOLUMES.get(vol, VOLUMES["medio"])
            data = b"".join(struct.pack("<h", int(v * amp * 32767)) for v in _synth(name))
            tmp = path + ".tmp"
            with wave.open(tmp, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(44100)
                w.writeframes(data)
            os.replace(tmp, path)
    return path


def play_sound(name, vol):
    if not name or name == "nenhum":
        return
    try:
        import winsound
        winsound.PlaySound(sound_path(name, vol),
                           winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
    except Exception:
        pass


# ───────────────────────── configuração ─────────────────────────

def load_cfg():
    os.makedirs(DATA_DIR, exist_ok=True)
    src = CONFIG if os.path.exists(CONFIG) else LEGACY_CONFIG
    try:
        with open(src, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        cfg = json.loads(json.dumps(DEFAULT_CFG))
    st = cfg.setdefault("settings", {})
    if isinstance(st.get("som"), bool):          # versão antiga: liga/desliga
        st["som"] = "madeira"
    for k, v in DEFAULT_SETTINGS.items():
        st.setdefault(k, json.loads(json.dumps(v)))
    for k, v in DEFAULT_SETTINGS["notion_horarios"].items():
        st["notion_horarios"].setdefault(k, v)
    if st["tema"] not in ("claro", "escuro", "auto"):
        st["tema"] = "claro"
    cfg.setdefault("lembretes", [])
    cfg.setdefault("avulsos", [])
    hist = cfg.setdefault("historico", {})
    limite = (dt.date.today() - dt.timedelta(days=60)).isoformat()
    for d in [d for d in hist if d < limite]:
        del hist[d]
    for l in cfg["lembretes"]:
        l.setdefault("id", uuid.uuid4().hex[:8])
        l.setdefault("meta", 0)
        l["titulo"] = _OLD_TITLES.get(l.get("titulo"), l.get("titulo", ""))
        l["cor"] = _OLD_COLORS.get((l.get("cor") or "").lower(), l.get("cor") or PALETTE[0])
    return cfg


def save_cfg(cfg):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = CONFIG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG)


def autostart_command():
    if FROZEN:
        return f'"{sys.executable}" --min'
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(pyw):
        exe = pyw
    return f'"{exe}" "{os.path.abspath(__file__)}" --min'


def set_autostart(enabled):
    import winreg
    key = r"Software\Microsoft\Windows\CurrentVersion\Run"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
        try:
            winreg.DeleteValue(k, OLD_APP)     # entrada do nome antigo
        except FileNotFoundError:
            pass
        if enabled:
            winreg.SetValueEx(k, APP, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(k, APP)
            except FileNotFoundError:
                pass


# ───────────────────────── token protegido (Windows DPAPI) ─────────────────────────

class _BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data, encrypt):
    buf = ctypes.create_string_buffer(data, len(data))
    src = _BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = _BLOB()
    fn = ctypes.windll.crypt32.CryptProtectData if encrypt else ctypes.windll.crypt32.CryptUnprotectData
    if not fn(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("DPAPI")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        k32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def protect(text):
    """Criptografa com a conta do Windows: só este usuário, neste PC, consegue ler."""
    if not text:
        return ""
    return "dpapi:" + base64.b64encode(_dpapi(text.encode("utf-8"), True)).decode()


def unprotect(s):
    if not s or not s.startswith("dpapi:"):
        return s or ""
    try:
        return _dpapi(base64.b64decode(s[6:]), False).decode("utf-8")
    except (OSError, ValueError):
        return ""


# ───────────────────────── Notion ─────────────────────────

class NotionError(Exception):
    def __init__(self, msg, status=0):
        super().__init__(msg)
        self.status = status


def notion_db_id(link):
    s = (link or "").strip().split("?")[0].split("#")[0].lower()
    m = re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", s) or \
        re.search(r"(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])", s)
    if not m:
        raise NotionError("Link do banco inválido — copie o link do banco no Notion.")
    return m.group(0).replace("-", "")


def _notion_req(token, method, path, body=None, ver="2022-06-28"):
    req = urllib.request.Request(
        "https://api.notion.com" + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}", "Notion-Version": ver,
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            msg = json.load(e).get("message", "")
        except Exception:
            msg = ""
        friendly = {401: "Token inválido — confira o token da integração.",
                    403: "Sem permissão para esse banco.",
                    404: "Banco não encontrado — conecte a integração ao banco (••• → Conexões).",
                    429: "O Notion pediu uma pausa; tento de novo em instantes."}
        raise NotionError(friendly.get(e.code, msg or f"Erro {e.code} do Notion."), e.code)
    except (urllib.error.URLError, TimeoutError, OSError):
        raise NotionError("Sem conexão com o Notion.")


def _norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower().strip()


DONE_NAMES = {"status", "feito", "feita", "concluido", "concluida", "done", "completo", "pronto", "check"}
DONE_VALUES = {"done", "concluido", "concluida", "feito", "feita", "completo", "finalizado", "pronto"}


def _schema_info(props):
    """Descobre no esquema do banco: data, campo de 'feito' e se é rotina (coluna Dia)."""
    info = {"date": None, "done": None, "done_value": None, "todo_value": None, "rotina": False,
            "title": None, "prio": None, "setor": None}
    for name, p in (props or {}).items():
        t, n = p.get("type"), _norm(name)
        if t == "title":
            info["title"] = name
        if t == "select" and (n.startswith("prioridad") or n == "priority"):
            info["prio"] = (name, [o.get("name") for o in (p.get("select") or {}).get("options", [])])
        elif t == "select" and (n.startswith("setor") or n.startswith("area") or n == "categoria"):
            info["setor"] = (name, [o.get("name") for o in (p.get("select") or {}).get("options", [])])
        if t == "date" and not info["date"]:
            info["date"] = name
        elif t in ("select", "multi_select") and n.startswith("dia"):
            info["rotina"] = True
        elif t == "checkbox" and n in DONE_NAMES and (not info["done"] or info["done"][1] != "checkbox"):
            info["done"] = (name, "checkbox")
        elif t == "status" and not info["done"]:
            info["done"] = (name, "status")
            st = p.get("status") or {}
            opts = {o.get("id"): o.get("name") for o in st.get("options", [])}
            for g in st.get("groups", []):
                ids = g.get("option_ids") or []
                if _norm(g.get("name")) in ("complete", "concluido") and ids:
                    info["done_value"] = opts.get(ids[0])
                if _norm(g.get("name")) in ("to-do", "to do", "a fazer") and ids:
                    info["todo_value"] = opts.get(ids[0])
    return info


def notion_fetch(token, link):
    """Páginas do banco + esquema. Bancos de tarefas já vêm filtrados (até hoje, não feitas)."""
    if not token:
        raise NotionError("Informe o token da integração.")
    dbid = notion_db_id(link)

    def paginate(path, ver, filt):
        pages, cursor = [], None
        while True:
            body = {"page_size": 100}
            if filt:
                body["filter"] = filt
            if cursor:
                body["start_cursor"] = cursor
            res = _notion_req(token, "POST", path, body, ver)
            pages += res.get("results", [])
            if not res.get("has_more") or len(pages) >= 2000:
                return pages
            cursor = res.get("next_cursor")

    path = ver = parent = None
    try:
        db = _notion_req(token, "GET", f"/v1/databases/{dbid}", ver="2025-09-03")
        sources = db.get("data_sources") or []
        if sources:
            dsid = sources[0]["id"]
            ds = _notion_req(token, "GET", f"/v1/data_sources/{dsid}", ver="2025-09-03")
            props, path, ver = ds.get("properties"), f"/v1/data_sources/{dsid}/query", "2025-09-03"
            parent = {"type": "data_source_id", "data_source_id": dsid}
    except NotionError as e:
        if e.status in (401, 403, 404):
            raise
    if not path:
        db = _notion_req(token, "GET", f"/v1/databases/{dbid}", ver="2022-06-28")
        props, path, ver = db.get("properties"), f"/v1/databases/{dbid}/query", "2022-06-28"
        parent = {"database_id": dbid}

    info = _schema_info(props)
    info["parent"], info["ver"] = parent, ver
    filt = None
    if info["date"] and not info["rotina"]:
        today = dt.date.today()
        dp = info["date"]
        conds = [{"property": dp, "date": {"on_or_before": today.isoformat()}}]
        if info["done"] and info["done"][1] == "checkbox":
            conds.append({"or": [{"property": info["done"][0], "checkbox": {"equals": False}},
                                 {"property": dp, "date": {"equals": today.isoformat()}}]})
        else:
            conds.append({"property": dp, "date": {"on_or_after": (today - dt.timedelta(days=21)).isoformat()}})
        filt = {"and": conds}
    return {"pages": paginate(path, ver, filt), "info": info}


def notion_parse(result):
    """Converte páginas do Notion em atividades simples."""
    if isinstance(result, list):
        result = {"pages": result, "info": {}}
    info = result.get("info") or {}
    acts = []
    for p in result.get("pages", []):
        if p.get("archived") or p.get("in_trash"):
            continue
        a = {"id": p.get("id", ""), "titulo": "", "dias": [], "periodos": [], "tempo": None, "data": None,
             "freq": "", "url": p.get("url", ""), "done": False, "done_prop": None, "prioridade": "", "setor": "",
             "done_value": info.get("done_value"), "todo_value": info.get("todo_value")}
        for name, v in (p.get("properties") or {}).items():
            t, n = v.get("type"), _norm(name)
            if t == "title":
                a["titulo"] = "".join(x.get("plain_text", "") for x in v.get("title") or []).strip()
            elif t in ("multi_select", "select"):
                if t == "multi_select":
                    vals = [o.get("name", "") for o in v.get("multi_select") or []]
                else:
                    vals = [v["select"]["name"]] if v.get("select") else []
                if n.startswith("dia"):
                    a["dias"] += vals
                elif n.startswith("period"):
                    a["periodos"] += vals
                elif n.startswith("frequen"):
                    a["freq"] = vals[0] if vals else ""
                elif n.startswith("prioridad") or n == "priority":
                    a["prioridade"] = vals[0] if vals else ""
                elif n.startswith("setor") or n.startswith("area") or n == "categoria":
                    a["setor"] = vals[0] if vals else ""
            elif t == "number" and ("tempo" in n or "min" in n):
                a["tempo"] = v.get("number")
            elif t == "date" and v.get("date") and a["data"] is None and (not info.get("date") or name == info["date"]):
                a["data"] = v["date"].get("start")
            elif t == "checkbox" and n in DONE_NAMES and (a["done_prop"] is None or a["done_prop"][1] != "checkbox"):
                a["done_prop"], a["done"] = (name, "checkbox"), bool(v.get("checkbox"))
            elif t == "status" and a["done_prop"] is None:
                a["done_prop"] = (name, "status")
                a["done"] = _norm((v.get("status") or {}).get("name")) in DONE_VALUES
        if a["titulo"]:
            acts.append(a)
    return acts


def _match_option(token, options):
    nt = _norm(token)
    if not nt:
        return None
    for o in options or []:
        if _norm(o) == nt:
            return o
    for o in options or []:
        if _norm(o).startswith(nt):
            return o
    return None


def parse_task(text, now, info=None):
    """'ligar pro fornecedor amanhã 15h !alta #dunamis' → título, quando, com hora?, prioridade, setor."""
    info = info or {}
    prio_opts = (info.get("prio") or (None, []))[1]
    setor_opts = (info.get("setor") or (None, []))[1]
    prio = setor = None
    rest = []
    for w in text.split():
        if w.startswith("!"):
            k = w.strip("!") or "alta"
            prio = _match_option(k, prio_opts) if prio_opts else k.capitalize()
        elif w.startswith("#") and len(w) > 1:
            setor = _match_option(w[1:], setor_opts) if setor_opts else w[1:].capitalize()
        else:
            rest.append(w)
    txt = " ".join(rest).strip()
    if not txt:
        raise ValueError("Escreva a tarefa.")
    try:
        titulo, when = parse_quick(txt, now)
        return titulo, when, True, prio, setor
    except ValueError:
        pass
    day = now.date()
    m = re.search(r"(?:^|\s)(amanh[ãa]|hoje)(?=\s|$)", txt, re.I)
    if m:
        if _norm(m.group(1)).startswith("amanh"):
            day += dt.timedelta(days=1)
        txt = (txt[:m.start()] + " " + txt[m.end():]).strip()
    titulo = re.sub(r"\s+", " ", txt).strip(" -–—:,.")
    titulo = (titulo[0].upper() + titulo[1:]) if titulo else "Tarefa"
    return titulo, day, False, prio, setor


def notion_create(token, info, titulo, when, timed, prio=None, setor=None):
    """Cria a tarefa no banco e devolve a página criada."""
    if not info or not info.get("parent") or not info.get("title"):
        raise NotionError("Sincronize o Notion antes de criar tarefas.")
    props = {info["title"]: {"title": [{"text": {"content": titulo}}]}}
    if info.get("date") and when:
        start = when.astimezone().isoformat(timespec="seconds") if timed else when.isoformat()
        props[info["date"]] = {"date": {"start": start}}
    for key, val in (("prio", prio), ("setor", setor)):
        if val and info.get(key):
            props[info[key][0]] = {"select": {"name": val}}
    return _notion_req(token, "POST", "/v1/pages", {"parent": info["parent"], "properties": props},
                       ver=info.get("ver") or "2022-06-28")


def notion_set_done(token, act, value):
    """Marca/desmarca a tarefa no próprio Notion."""
    prop = act.get("done_prop")
    if not prop:
        return False
    name, typ = prop
    if typ == "checkbox":
        val = {"checkbox": bool(value)}
    elif act.get("done_value"):
        val = {"status": {"name": act["done_value"] if value else (act.get("todo_value") or "Not started")}}
    else:
        return False
    _notion_req(token, "PATCH", f"/v1/pages/{act['id']}", {"properties": {name: val}})
    return True


_WD = ["segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo"]


def _parse_iso(s):
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None, False
    timed = "T" in s
    if d.tzinfo:
        d = d.astimezone().replace(tzinfo=None)
    return d, timed


def notion_applies(act, date):
    """A atividade acontece nesta data? (regras da coluna Dia, ou data igual a hoje)"""
    if act.get("data"):
        d, _ = _parse_iso(act["data"])
        if d and d.date() == date:
            return True
    wd = _WD[date.weekday()]
    for v in act.get("dias", []):
        nv = _norm(v)
        if nv in ("diario", "todo dia", "todos os dias", "dia", "diariamente"):
            return True
        if "primeiro dia do mes" in nv:
            if date.day == 1:
                return True
            continue
        if "primeira semana" in nv:
            if date.day <= 7:
                return True
            continue
        for tk in re.split(r"[/,;]|\s+e\s+", nv):
            tk = tk.replace("-feira", "").replace(" feira", "").strip()
            if tk.endswith("s") and tk[:-1] in _WD:
                tk = tk[:-1]
            if tk == wd:
                return True
    return not act.get("dias") and _norm(act.get("freq")) == "diario"


def notion_period(act):
    found = set()
    for v in act.get("periodos", []):
        nv = _norm(v)
        for key, word in (("golden", "golden"), ("manha", "manh"), ("tarde", "tarde"), ("noite", "noite")):
            if word in nv:
                found.add(key)
    for key, _ in PERIODS:
        if key in found:
            return key
    return ""


# ───────────────────────── imagens com antialias ─────────────────────────

_IMG = {}
SS = 4  # supersampling


def mix(a, b, t):
    pa = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    pb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(pa, pb))


def _rgba(c):
    return None if c is None else tuple(int(c[i:i + 2], 16) for i in (1, 3, 5)) + (255,)


def rounded(w, h, r, fill=None, outline=None, width=1.0):
    key = ("r", w, h, r, fill, outline, width)
    if key not in _IMG:
        im = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
        if fill or outline:
            ImageDraw.Draw(im).rounded_rectangle(
                [0, 0, w * SS - 1, h * SS - 1], radius=r * SS, fill=_rgba(fill),
                outline=_rgba(outline), width=max(1, round(width * SS)) if outline else 0)
        _IMG[key] = ImageTk.PhotoImage(im.resize((w, h), Image.LANCZOS))
    return _IMG[key]


def toggle_photo(w, h, track, knob, on):
    key = ("tp", w, h, track, knob, on)
    if key not in _IMG:
        W, H = w * SS, h * SS
        im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.rounded_rectangle([0, 0, W - 1, H - 1], radius=H // 2, fill=_rgba(track))
        p = int(H * 0.15)
        kd = H - 2 * p
        x = W - p - kd if on else p
        d.ellipse([x, p, x + kd, p + kd], fill=_rgba(knob))
        _IMG[key] = ImageTk.PhotoImage(im.resize((w, h), Image.LANCZOS))
    return _IMG[key]


def circle_photo(d, fill=None, outline=None, width=1.0, ring=None):
    key = ("c", d, fill, outline, width, ring)
    if key not in _IMG:
        D = d * SS
        im = Image.new("RGBA", (D, D), (0, 0, 0, 0))
        dr = ImageDraw.Draw(im)
        if ring:
            dr.ellipse([0, 0, D - 1, D - 1], outline=_rgba(ring), width=max(1, round(width * SS)))
            o = int(D * 0.135)
            dr.ellipse([o, o, D - 1 - o, D - 1 - o], fill=_rgba(fill))
        elif fill:
            dr.ellipse([0, 0, D - 1, D - 1], fill=_rgba(fill))
        else:
            dr.ellipse([0, 0, D - 1, D - 1], outline=_rgba(outline), width=max(1, round(width * SS)))
        _IMG[key] = ImageTk.PhotoImage(im.resize((d, d), Image.LANCZOS))
    return _IMG[key]


def corner_photos(r):
    """Quatro cantos arredondados (com antialias) da ilha, guardados em cache por raio."""
    key = ("corner", r)
    if key not in _IMG:
        S = 4
        bg = tuple(int(KEY[i:i + 2], 16) for i in (1, 3, 5))
        im = Image.new("RGB", (2 * r * S, 2 * r * S), bg)
        ImageDraw.Draw(im).ellipse([0, 0, 2 * r * S - 1, 2 * r * S - 1], fill=_rgba(ISL["bg"])[:3])
        im = im.resize((2 * r, 2 * r), Image.LANCZOS)
        _IMG[key] = tuple(ImageTk.PhotoImage(im.crop(box)) for box in
                          ((0, 0, r, r), (r, 0, 2 * r, r), (0, r, r, 2 * r), (r, r, 2 * r, 2 * r)))
    return _IMG[key]


def make_icon_image(size=64):
    s = size * SS
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=s // 4, fill=(27, 27, 29, 255))
    w = (247, 246, 243, 255)
    d.ellipse([s * .32, s * .20, s * .68, s * .56], fill=w)
    d.rectangle([s * .32, s * .38, s * .68, s * .64], fill=w)
    d.rounded_rectangle([s * .22, s * .62, s * .78, s * .70], radius=s * .04, fill=w)
    d.ellipse([s * .44, s * .72, s * .56, s * .82], fill=w)
    return img.resize((size, size), Image.LANCZOS)


# ───────────────────────── componentes ─────────────────────────

def bind_tree(widget, seq, fn):
    widget.bind(seq, fn, add=True)
    for c in widget.winfo_children():
        bind_tree(c, seq, fn)


def is_inside(widget, root_widget):
    w = widget
    while w is not None:
        if w is root_widget:
            return True
        w = getattr(w, "master", None)
    return False


def pointer_inside(container):
    try:
        x, y = container.winfo_pointerxy()
        w = container.winfo_containing(x, y)
        return w is not None and is_inside(w, container)
    except (tk.TclError, KeyError):
        return False


class Btn(tk.Label):
    """Botão em pílula. kind: primary | ghost | link"""

    def __init__(self, parent, app, text, command, kind="primary", padx=18, h=34, font=None):
        t, s = app.t, app.scale
        self.command = command
        font = font or app.F["btn"]
        bg = parent.cget("bg")
        if kind == "link":
            super().__init__(parent, text=text, font=font, bg=bg, fg=t["sub"], bd=0, cursor="hand2")
            self.n, self.hv, self.fg_n, self.fg_h = "", "", t["sub"], t["fg"]
        else:
            w = int(tkfont.Font(font=font).measure(text) + 2 * padx * s)
            hh = int(h * s)
            if kind == "primary":
                self.n = rounded(w, hh, hh // 2, t["fg"])
                self.hv = rounded(w, hh, hh // 2, mix(t["fg"], t["bg"], .18))
                self.fg_n = self.fg_h = t["inv"]
            else:
                self.n = rounded(w, hh, hh // 2, None, t["border"])
                self.hv = rounded(w, hh, hh // 2, t["hover"], t["border"])
                self.fg_n = self.fg_h = t["fg"]
            super().__init__(parent, image=self.n, text=text, compound="center", font=font,
                             bg=bg, fg=self.fg_n, bd=0, cursor="hand2")
        self.bind("<Enter>", lambda e: self.configure(image=self.hv, fg=self.fg_h))
        self.bind("<Leave>", lambda e: self.configure(image=self.n, fg=self.fg_n))
        self.bind("<Button-1>", lambda e: (self.command(), "break")[1])


class IconBtn(tk.Label):
    def __init__(self, parent, app, glyph, command, size=34, font=None):
        t, s = app.t, app.scale
        d = int(size * s)
        self.blank = rounded(d, d, d // 2)
        self.hv = rounded(d, d, d // 2, t["hover"])
        super().__init__(parent, image=self.blank, text=glyph, compound="center",
                         font=font or app.F["icon"], bg=parent.cget("bg"), fg=t["sub"],
                         bd=0, cursor="hand2")
        self.bind("<Enter>", lambda e: self.configure(image=self.hv, fg=t["fg"]))
        self.bind("<Leave>", lambda e: self.configure(image=self.blank, fg=t["sub"]))
        self.bind("<Button-1>", lambda e: (command(), "break")[1])


class Toggle(tk.Label):
    def __init__(self, parent, app, value, command=None):
        t, s = app.t, app.scale
        w, h = int(36 * s), int(20 * s)
        self.imgs = {True: toggle_photo(w, h, t["fg"], t["inv"], True),
                     False: toggle_photo(w, h, t["border"], t["knob_off"], False)}
        self.value, self.command = bool(value), command
        super().__init__(parent, image=self.imgs[self.value], bg=parent.cget("bg"), bd=0, cursor="hand2")
        self.bind("<Button-1>", self._click)

    def _click(self, _e):
        self.set(not self.value)
        if self.command:
            self.command(self.value)
        return "break"

    def set(self, v):
        self.value = bool(v)
        self.configure(image=self.imgs[self.value])


class Pill(tk.Label):
    """Pílula selecionável (dias da semana, opções)."""

    def __init__(self, parent, app, text, w, h, selected=False, command=None, outline=True):
        t = app.t
        r = h // 2
        self.t, self.command = t, command
        self.on = rounded(w, h, r, t["fg"])
        self.off = rounded(w, h, r, None, t["border"] if outline else None)
        self.hov = rounded(w, h, r, t["hover"], t["border"] if outline else None)
        self.selected, self.hovering = selected, False
        super().__init__(parent, text=text, compound="center", font=app.F["pill"], bd=0,
                         padx=0, pady=0, highlightthickness=0, bg=parent.cget("bg"), cursor="hand2")
        self.bind("<Enter>", lambda e: self._h(True))
        self.bind("<Leave>", lambda e: self._h(False))
        self.bind("<Button-1>", self._click)
        self._paint()

    def _h(self, v):
        self.hovering = v
        self._paint()

    def _click(self, _e):
        if self.command:
            self.command(self)
        return "break"

    def set(self, v):
        self.selected = v
        self._paint()

    def _paint(self):
        t = self.t
        if self.selected:
            self.configure(image=self.on, fg=t["inv"])
        elif self.hovering:
            self.configure(image=self.hov, fg=t["fg"])
        else:
            self.configure(image=self.off, fg=t["sub"])


class Segmented(tk.Label):
    """Controle segmentado dentro de uma cápsula."""

    def __init__(self, parent, app, options, value, command=None):
        t, s = app.t, app.scale
        f = tkfont.Font(font=app.F["pill"])
        pw = int(max(f.measure(lbl) for _, lbl in options) + 30 * s)
        ph, pad = int(28 * s), int(3 * s)
        W, H = pw * len(options) + 2 * pad, ph + 2 * pad
        self.bgimg = rounded(W, H, H // 2, t["input"], t["border"])
        super().__init__(parent, image=self.bgimg, bg=parent.cget("bg"), bd=0, padx=0, pady=0,
                         highlightthickness=0)
        self.value, self.command, self.pills = value, command, {}
        for i, (val, lbl) in enumerate(options):
            p = Pill(self, app, lbl, pw, ph, selected=(val == value), command=self._pick, outline=False)
            p.configure(bg=t["input"])
            p.val = val
            p.place(x=pad + i * pw, y=pad)
            self.pills[val] = p

    def _pick(self, pill):
        self.value = pill.val
        for v, p in self.pills.items():
            p.set(v == self.value)
        if self.command:
            self.command(self.value)


class Field(tk.Frame):
    """Campo de texto plano com borda fina."""

    def __init__(self, parent, app, var, width=None, justify="left", show=""):
        t, s = app.t, app.scale
        super().__init__(parent, bg=t["input"], highlightthickness=1,
                         highlightbackground=t["border"], highlightcolor=t["border"])
        self.e = tk.Entry(self, textvariable=var, bg=t["input"], fg=t["fg"], insertbackground=t["fg"], show=show,
                          relief="flat", bd=0, font=app.F["body"], justify=justify,
                          disabledbackground=t["input"], disabledforeground=t["faint"],
                          selectbackground=t["hover"], selectforeground=t["fg"], insertwidth=1)
        if width:
            self.e.configure(width=width)
        self.e.pack(fill="x", padx=int(10 * s), pady=int(7 * s))
        self.e.bind("<FocusIn>", lambda _: self.configure(highlightbackground=t["sub"]))
        self.e.bind("<FocusOut>", lambda _: self.configure(highlightbackground=t["border"]))
        self.bind("<Button-1>", lambda _: self.e.focus_set())


class Stepper(tk.Frame):
    def __init__(self, parent, app, value, lo, hi, step, suffix=""):
        t = app.t
        super().__init__(parent, bg=parent.cget("bg"))
        self.lo, self.hi, self.step = lo, hi, step
        self.var = tk.StringVar(value=str(value))
        IconBtn(self, app, G_REMOVE, lambda: self._add(-1), size=28, font=app.F["icon_s"]).pack(side="left")
        Field(self, app, self.var, width=4, justify="center").pack(side="left", padx=4)
        IconBtn(self, app, G_ADD, lambda: self._add(1), size=28, font=app.F["icon_s"]).pack(side="left")
        if suffix:
            tk.Label(self, text=suffix, font=app.F["sub"], bg=self.cget("bg"), fg=t["sub"],
                     width=3, anchor="w").pack(side="left", padx=(6, 0))

    def get(self):
        try:
            v = int(self.var.get())
        except ValueError:
            raise ValueError("Use apenas números.")
        return max(self.lo, min(self.hi, v))

    def _add(self, d):
        try:
            v = int(self.var.get())
        except ValueError:
            v = self.lo
        v = (v // self.step + (1 if d > 0 else (0 if v % self.step else -1))) * self.step
        self.var.set(str(max(self.lo, min(self.hi, v))))


# ───────────────────────── pop-up ─────────────────────────

class Popup:
    BASE_W = 360

    def __init__(self, app, lemb, sound=True):
        self.app, self.lemb = app, lemb
        t, s, F = app.t, app.scale, app.F
        st = app.st
        sistema = bool(lemb.get("_sistema"))
        accent = lemb.get("cor") or PALETTE[0]
        surf = t["surface"]
        self.W = int(self.BASE_W * s)
        self.total = self.remaining = max(2, int(st["duracao"])) * 1000
        self.alpha, self.alpha_target = 0.0, float(st["opacidade"])
        self.x = self.y = self.ty = None
        self.closing = self.hover = self.animating = False

        w = self.win = tk.Toplevel(app.root)
        w.withdraw()
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.attributes("-alpha", 0.0)
        w.configure(bg=surf)

        bh = max(2, int(2 * s))
        self.bar = tk.Canvas(w, height=bh, bg=surf, highlightthickness=0, bd=0)
        self.bar.pack(side="bottom", fill="x")
        self.bar_line = self.bar.create_rectangle(0, 0, self.W, bh, fill=mix(accent, surf, .15), width=0)
        self.bh = bh

        body = tk.Frame(w, bg=surf)
        body.pack(fill="both", expand=True, padx=int(20 * s), pady=(int(16 * s), int(12 * s)))

        r1 = tk.Frame(body, bg=surf)
        r1.pack(fill="x")
        ls = tkfont.Font(font=F["title"]).metrics("linespace")
        dot = app.dot(accent)
        tk.Label(r1, image=dot, bg=surf, bd=0).pack(side="left", anchor="n",
                                                     pady=((ls - dot.height()) // 2, 0),
                                                     padx=(0, int(12 * s)))
        tk.Label(r1, text=dt.datetime.now().strftime("%H:%M"), font=F["small"], bg=surf,
                 fg=t["faint"]).pack(side="right", anchor="n", pady=(int(1 * s), 0))
        tk.Label(r1, text=lemb.get("titulo", ""), font=F["title"], bg=surf, fg=t["fg"], anchor="w",
                 justify="left", wraplength=self.W - int(130 * s)).pack(side="left", fill="x", expand=True)

        indent = dot.width() + int(12 * s)
        if lemb.get("mensagem"):
            tk.Label(body, text=lemb["mensagem"], font=F["sub"], bg=surf, fg=t["sub"], anchor="w",
                     justify="left", wraplength=self.W - int(60 * s) - indent).pack(
                fill="x", padx=(indent, 0), pady=(int(3 * s), 0))

        acts = tk.Frame(body, bg=surf)
        acts.pack(fill="x", pady=(int(12 * s), 0), padx=(indent, 0))
        ok = tk.Label(acts, text="Ok" if sistema else "Feito", font=F["small_b"],
                      bg=surf, fg=t["fg"], cursor="hand2")
        ok.pack(side="right")
        ok.bind("<Button-1>", self._done)
        if not sistema:
            sz = tk.Label(acts, text=f"Adiar {st['adiar_min']} min", font=F["small"], bg=surf,
                          fg=t["sub"], cursor="hand2")
            sz.pack(side="right", padx=(0, int(18 * s)))
            sz.bind("<Enter>", lambda e: sz.configure(fg=t["fg"]))
            sz.bind("<Leave>", lambda e: sz.configure(fg=t["sub"]))
            sz.bind("<Button-1>", self._snooze)
            meta = int(lemb.get("meta") or 0)
            if meta and not lemb.get("_avulso"):
                n = app.count_today(lemb)
                tk.Label(acts, text=f"{n} de {meta} hoje", font=F["small"], bg=surf,
                         fg=t["faint"]).pack(side="left")

        bind_tree(w, "<Button-1>", lambda e: self.close())
        bind_tree(w, "<Enter>", lambda e: setattr(self, "hover", True))
        bind_tree(w, "<Leave>", self._leave)

        w.update_idletasks()
        self.h = w.winfo_reqheight()
        style_floating(w, t)
        w.deiconify()

        app.popups.append(self)
        while len(app.popups) > MAX_POPUPS:
            app.popups[0].close()
        app.layout_popups()
        self.win.after(40, self._tick)
        if sound:
            app.play()

    def _leave(self, _e):
        self.win.after(10, self._check_leave)

    def _check_leave(self):
        if not self.closing and not pointer_inside(self.win):
            self.hover = False
            self.remaining = max(self.remaining, 2500)

    def move_to(self, x, ty, direction=1):
        self.x, self.ty = x, ty
        if self.y is None:
            self.y = ty + 18 * self.app.scale * direction
        if not self.animating:
            self._anim()

    def _anim(self):
        if not self.win.winfo_exists():
            return
        self.animating = True
        settled = True
        dy = self.ty - self.y
        if abs(dy) > 0.5:
            self.y += dy * 0.2
            settled = False
        else:
            self.y = self.ty
        if self.alpha < self.alpha_target:
            self.alpha = min(self.alpha_target, self.alpha + 0.07)
            settled = False
        elif self.alpha > self.alpha_target:
            self.alpha = max(self.alpha_target, self.alpha - 0.1)
            settled = False
        self.win.attributes("-alpha", self.alpha)
        self.win.geometry(f"{self.W}x{self.h}+{int(self.x)}+{int(round(self.y))}")
        if self.closing and self.alpha <= 0:
            self.win.destroy()
            return
        if settled:
            self.animating = False
            return
        self.win.after(14, self._anim)

    def _tick(self):
        if self.closing or not self.win.winfo_exists():
            return
        # só conta o tempo com o mouse fora dele e com alguém usando o PC
        if not self.hover and idle_seconds() < 10:
            self.remaining -= 40
        frac = max(0.0, self.remaining / self.total)
        self.bar.coords(self.bar_line, 0, 0, self.W * frac, self.bh)
        if self.remaining <= 0:
            self.close()
            return
        self.win.after(40, self._tick)

    def _snooze(self, _e):
        self.app.snooze(self.lemb)
        self.close()
        return "break"

    def _done(self, _e):
        self.app.mark_done(self.lemb)
        self.close()
        return "break"

    def close(self):
        if self.closing:
            return
        self.closing = True
        self.alpha_target = 0.0
        if self in self.app.popups:
            self.app.popups.remove(self)
            self.app.layout_popups()
        if not self.animating:
            self._anim()


# ───────────────────────── ilha no topo ─────────────────────────

def _cursor():
    pt = wintypes.POINT()
    u32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


_TIMER_ON = [0]


def _fast_timer(on):
    """Timer de 1 ms do Windows só durante animações (o padrão é ~15,6 ms e causa engasgos)."""
    try:
        if on and not _TIMER_ON[0]:
            ctypes.windll.winmm.timeBeginPeriod(1)
            _TIMER_ON[0] = 1
        elif not on and _TIMER_ON[0]:
            ctypes.windll.winmm.timeEndPeriod(1)
            _TIMER_ON[0] = 0
    except Exception:
        pass


def set_no_activate(win):
    try:
        hwnd = hwnd_of(win)
        u32.SetWindowLongW(hwnd, -20, u32.GetWindowLongW(hwnd, -20) | 0x80 | 0x08000000)
    except Exception:
        pass


def _trunc(s, n):
    return s if len(s) <= n else s[:n - 1].rstrip() + "…"


class Island:
    """Leve o mouse ao topo da tela, no centro: uma cápsula escura desce com o essencial."""
    BASE_W = 470
    DWELL_MS, LEAVE_MS, STEP = 220, 320, 50

    def __init__(self, app):
        self.app = app
        self.win = None
        self.state = "hidden"
        self.dwell = self.out = 0
        app.root.after(500, self.poll)
        app.root.after(800, self._build)

    def poll(self):
        try:
            self._poll()
        except Exception:
            pass
        self.app.root.after(self.STEP, self.poll)

    def _poll(self):
        x, y = _cursor()
        if self.state == "hidden":
            sw = u32.GetSystemMetrics(0)
            if self.app.st.get("ilha", True) and y <= 0 and abs(x - sw / 2) <= sw * 0.2:
                self.dwell += self.STEP
                if self.dwell >= self.DWELL_MS and not fullscreen_busy():
                    self.dwell = 0
                    self.open()
            else:
                self.dwell = 0
        elif self.state == "open":
            x0, y0, x1, y1 = self.rect
            m = int(30 * self.app.scale)
            if x0 - m <= x <= x1 + m and y <= y1 + m:
                self.out = 0
            else:
                self.out += self.STEP
                if self.out >= self.LEAVE_MS:
                    self.close()

    OPEN_S, CLOSE_S = 0.46, 0.24

    def _build(self):
        """Gota (janela com alfa real) + janela de conteúdo preta por cima. Criadas uma vez só."""
        app, s = self.app, self.app.scale
        self.W = int(self.BASE_W * s)
        self.F = int(18 * s)                    # curva côncava onde a gota encontra o topo
        self.R = int(30 * s)                    # raio da base
        self.pad = int(26 * s)
        self.top_pad, self.bot_pad = int(16 * s), int(22 * s)
        self.WT = self.W + 2 * self.F
        l, tp, r, b = work_area()
        self.HMAX = int((b - tp) * 0.95)
        self.wx = (u32.GetSystemMetrics(0) - self.WT) // 2
        self.aw = AlphaWindow(app.root, self.WT, self.HMAX)
        self.win = self.aw.win
        self.win.withdraw()
        cw = self.cw = tk.Toplevel(app.root)
        cw.withdraw()
        cw.overrideredirect(True)
        cw.attributes("-topmost", True)
        cw.configure(bg=ISL["bg"])
        self.body = tk.Frame(cw, bg=ISL["bg"])
        self.body.pack(fill="both", expand=True)
        self.cw_styled = False
        self.ch = 0

    def open(self):
        if not getattr(self, "aw", None):
            self._build()
        peek = getattr(self.app, "peek", None)
        s = self.app.scale
        if peek and peek.active:                # nasce da gotinha dos olhos
            self.w0, self.h0 = peek.PW, peek.h
            peek.hide()
        else:
            self.w0, self.h0 = int(52 * s), 0
        self.state, self.out = "opening", 0
        self.cw.withdraw()
        self._fill()
        self._draw(self.w0, self.h0)
        self.win.deiconify()
        _fast_timer(True)
        self.t0 = time.perf_counter()
        self._anim()

    def _measure(self):
        self.body.update_idletasks()
        self.ch = self.body.winfo_reqheight()
        self.H = min(self.HMAX - 4, self.top_pad + self.ch + self.bot_pad)

    def _place_content(self):
        cwid = self.W - 2 * self.pad
        self.cw.geometry(f"{cwid}x{self.ch}+{self.wx + self.F + self.pad}+{self.top_pad}")

    def _draw(self, w, h):
        w, h = max(4, int(w)), max(0, int(h))
        f = min(self.F, h // 2)
        r = max(1, min(self.R, w // 2, max(1, h)))
        if h <= 0:
            self.aw.push_mask(Image.new("L", (self.WT, 1), 0), self.wx, 0, ISL["bg"])
        else:
            self.aw.push_mask(drop_mask(self.WT, w, h, f, r), self.wx, 0, ISL["bg"])
        x0 = self.wx + (self.WT - w) // 2 - f
        self.rect = (x0, 0, x0 + w + 2 * f, h)

    @staticmethod
    def _spring(p):
        c1 = 1.25
        c3 = c1 + 1
        return 1 + c3 * (p - 1) ** 3 + c1 * (p - 1) ** 2

    def _anim(self):
        if not self.aw.win.winfo_exists():
            return
        t = time.perf_counter() - self.t0
        if self.state == "opening":
            p = min(1.0, t / self.OPEN_S)
            eh = self._spring(min(1.0, p * 1.2))              # a gota escorre primeiro…
            ew = self._spring(max(0.0, (p - 0.18) / 0.82))    # …e depois se espalha
            w = self.w0 + (self.W - self.w0) * ew
            h = self.h0 + (self.H - self.h0) * eh
        else:
            p = min(1.0, t / self.CLOSE_S)
            ew = 1 - min(1.0, p * 1.25) ** 2                   # estreita…
            eh = 1 - p ** 2.2                                  # …e recolhe para o topo
            w = int(52 * self.app.scale) + (self.W - int(52 * self.app.scale)) * ew
            h = self.H * eh
        self._draw(w, h)
        if p < 1:
            self.win.after(5, self._anim)
            return
        _fast_timer(False)
        if self.state == "opening":
            self.state = "open"
            self._place_content()
            self.cw.deiconify()
            if not self.cw_styled:
                set_no_activate(self.cw)
                self.cw_styled = True
            self.cw.lift()
        else:
            self.win.withdraw()
            self.state = "hidden"

    def close(self):
        if self.state not in ("open", "opening") or not getattr(self, "aw", None):
            return
        was_opening = self.state == "opening"
        self.cw.withdraw()
        self.state = "closing"
        _fast_timer(True)
        self.t0 = time.perf_counter()
        if not was_opening:           # se já está animando, o laço em curso continua
            self._anim()

    def refresh(self):
        if self.state != "open":
            return
        self._fill()
        self._draw(self.W, self.H)
        self._place_content()

    def _act(self, fn):
        self.close()
        self.app.root.after(60, fn)

    # conteúdo
    def _fill(self):
        app, s, F, c = self.app, self.app.scale, self.app.F, ISL
        b = self.body
        for w in b.winfo_children():
            w.destroy()
        now = dt.datetime.now()
        lab = lambda parent, **kw: tk.Label(parent, bg=c["bg"], bd=0, **kw)

        top = tk.Frame(b, bg=c["bg"])
        top.pack(fill="x")
        left = tk.Frame(top, bg=c["bg"])
        left.pack(side="left")
        lab(left, text=now.strftime("%H:%M"), font=F["isl_time"], fg=c["fg"]).pack(anchor="w")
        lab(left, text=f"{DIAS_LONGOS[now.weekday()]}, {now.day} de {MESES[now.month - 1]}",
            font=F["small"], fg=c["sub"]).pack(anchor="w")
        chip = app.island_status()
        if chip:
            right = tk.Frame(top, bg=c["bg"])
            right.pack(side="right", anchor="n", pady=(int(6 * s), 0))
            lab(right, image=app.dot(chip[1]) if chip[1] else "").pack(side="left", padx=(0, int(8 * s)))
            lab(right, text=chip[0], font=F["small_b"], fg=c["fg"]).pack(side="left")
        if app.pomo.running:
            bw = self.W - 2 * self.pad
            bar = tk.Canvas(b, height=max(2, int(2 * s)), width=bw, bg=c["line"], highlightthickness=0, bd=0)
            bar.pack(fill="x", pady=(int(12 * s), 0))
            frac = 1 - app.pomo.remaining() / max(1, app.pomo.total)
            bar.create_rectangle(0, 0, bw * frac, int(2 * s) + 1, width=0,
                                 fill=POMO_FOCO if app.pomo.phase == "foco" else POMO_PAUSA)

        def section(text):
            lab(b, text=text.upper(), font=F["caps"], fg=c["faint"]).pack(
                anchor="w", pady=(int(18 * s), int(6 * s)))

        up = app.upcoming(3)
        if up:
            section("A seguir")
            for when, titulo, cor in up:
                r = tk.Frame(b, bg=c["bg"])
                r.pack(fill="x", pady=int(3 * s))
                lab(r, image=app.dot(cor)).pack(side="left", padx=(0, int(12 * s)))
                lab(r, text=fmt_when(when, now), font=F["sub"], fg=c["sub"]).pack(side="right")
                lab(r, text=_trunc(titulo, 44), font=F["body"], fg=c["fg"], anchor="w").pack(
                    side="left", fill="x", expand=True)

        nt = app.notion_today()
        if nt is not None:
            pend = [a for a in nt if not a["done"]]
            section(f"Hoje no Notion  ·  {len(nt) - len(pend)} de {len(nt)}")
            if app.notion_err and not app.notion_acts:
                lab(b, text=app.notion_err, font=F["small"], fg=c["sub"]).pack(anchor="w")
            elif not nt:
                lab(b, text="Nada pendente para hoje.", font=F["sub"], fg=c["sub"]).pack(anchor="w")
            elif not pend:
                lab(b, text="Tudo feito por hoje.", font=F["sub"], fg=c["sub"]).pack(anchor="w")
            cur = app.current_period()
            pend.sort(key=lambda a: (a["periodo"] != cur, a["ordem"]))
            ring = circle_photo(int(16 * s), outline=c["faint"], width=1.3 * s)
            for a in pend[:5]:
                r = tk.Frame(b, bg=c["bg"], cursor="hand2")
                r.pack(fill="x", pady=int(3 * s))
                chk = lab(r, image=ring, cursor="hand2")
                chk.pack(side="left", padx=(0, int(10 * s)))
                extra = a["info"]
                lab(r, text=extra, font=F["small"], fg=c["faint"]).pack(side="right")
                lab(r, text=_trunc(a["titulo"], 38), font=F["body"], fg=c["fg"], anchor="w").pack(
                    side="left", fill="x", expand=True)
                bind_tree(r, "<Button-1>", lambda e, a=a: (app.set_notion_done(a["id"], True), self.refresh()))
            if len(pend) > 5:
                lab(b, text=f"+ {len(pend) - 5} mais", font=F["small"], fg=c["faint"]).pack(
                    anchor="w", pady=(int(4 * s), 0))

        tk.Frame(b, bg=c["line"], height=1).pack(fill="x", pady=(int(16 * s), int(10 * s)))
        foot = tk.Frame(b, bg=c["bg"])
        foot.pack(fill="x")
        links = [("Lembrete rápido", app.open_quick)]
        if app.notion_configured():
            links.append(("Nova tarefa", app.open_task))
        links += [("Parar pomodoro" if app.pomo.running else "Pomodoro", app.pomo.toggle), ("Abrir", app.show)]
        for i, (txt, fn) in enumerate(links):
            l = lab(foot, text=txt, font=F["small"], fg=c["sub"], cursor="hand2")
            l.pack(side="right" if i == len(links) - 1 else "left", padx=(0, int(18 * s)))
            l.bind("<Enter>", lambda e, l=l: l.configure(fg=c["fg"]))
            l.bind("<Leave>", lambda e, l=l: l.configure(fg=c["sub"]))
            l.bind("<Button-1>", lambda e, fn=fn: self._act(fn))
        self._measure()


# ───────────────────────── olhinhos curiosos ─────────────────────────

EYE = "#f2f2f4"

# Janela com transparência real por pixel (bordas suaves sobre qualquer fundo)
_u32 = ctypes.WinDLL("user32")
_gdi = ctypes.WinDLL("gdi32")
_VP = ctypes.c_void_p
_u32.GetDC.restype = _VP
_u32.GetDC.argtypes = [_VP]
_gdi.CreateCompatibleDC.restype = _VP
_gdi.CreateCompatibleDC.argtypes = [_VP]
_gdi.CreateDIBSection.restype = _VP
_gdi.CreateDIBSection.argtypes = [_VP, _VP, ctypes.c_uint, ctypes.POINTER(_VP), _VP, ctypes.c_uint]
_gdi.SelectObject.restype = _VP
_gdi.SelectObject.argtypes = [_VP, _VP]
_u32.UpdateLayeredWindow.argtypes = [_VP, _VP, _VP, _VP, _VP, _VP, ctypes.c_uint, _VP, ctypes.c_uint]
_u32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
_u32.GetWindowLongPtrW.argtypes = [_VP, ctypes.c_int]
_u32.SetWindowLongPtrW.argtypes = [_VP, ctypes.c_int, ctypes.c_ssize_t]


class _BMIH(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class _BLEND(ctypes.Structure):
    _fields_ = [("op", ctypes.c_ubyte), ("flags", ctypes.c_ubyte), ("alpha", ctypes.c_ubyte), ("fmt", ctypes.c_ubyte)]


class AlphaWindow:
    """Toplevel do Tk cujo conteúdo é uma imagem RGBA (PIL) enviada direto ao Windows."""

    def __init__(self, root, w, h):
        self.w, self.h = w, h
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.geometry(f"{w}x{h}+0+-{h + 50}")
        self.win.update_idletasks()
        self.hwnd = hwnd_of(self.win)
        ex = _u32.GetWindowLongPtrW(self.hwnd, -20)
        _u32.SetWindowLongPtrW(self.hwnd, -20, ex | 0x80000 | 0x80 | 0x08000000)   # layered, tool, no-activate
        self.screen = _u32.GetDC(None)
        self.mem = _gdi.CreateCompatibleDC(self.screen)
        bmi = _BMIH(ctypes.sizeof(_BMIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        self.bits = _VP()
        self.bmp = _gdi.CreateDIBSection(self.mem, ctypes.byref(bmi), 0, ctypes.byref(self.bits), None, 0)
        _gdi.SelectObject(self.mem, self.bmp)
        self.blend = _BLEND(0, 0, 255, 1)
        # 1º quadro (transparente) antes de qualquer withdraw: sem ele o Windows recusa os próximos
        self._send(b"", -w - 100, -h - 100, w, h)

    def _send(self, data, x, y, w, h):
        """Sempre envia a janela inteira (o Windows recusa tamanhos menores); o resto fica transparente."""
        n = min(len(data), self.w * self.h * 4)
        ctypes.memmove(self.bits, data, n)
        rest = self.w * self.h * 4 - n
        if rest > 0:
            ctypes.memset(self.bits.value + n, 0, rest)
        pos, size, src = wintypes.POINT(int(x), int(y)), wintypes.SIZE(self.w, self.h), wintypes.POINT(0, 0)
        _u32.UpdateLayeredWindow(self.hwnd, self.screen, ctypes.byref(pos), ctypes.byref(size), self.mem,
                                 ctypes.byref(src), 0, ctypes.byref(self.blend), 2)

    def push(self, img, x, y):
        """img: RGBA do tamanho da janela. Pré-multiplica o alfa e envia."""
        r, g, b, a = img.split()
        data = Image.merge("RGBA", (ImageChops.multiply(b, a), ImageChops.multiply(g, a),
                                    ImageChops.multiply(r, a), a)).tobytes()
        self._send(data, x, y, img.width, min(img.height, self.h))

    _LUTS = {}

    def push_mask(self, mask, x, y, color):
        """Forma de uma cor só (máscara L, largura = da janela, altura ≤ da janela). Bem rápido."""
        if color not in self._LUTS:
            c = [int(color[i:i + 2], 16) for i in (5, 3, 1)]          # B, G, R
            self._LUTS[color] = [[round(i * v / 255) for i in range(256)] for v in c]
        lb, lg, lr = self._LUTS[color]
        h = min(mask.height, self.h)
        if mask.height > h:
            mask = mask.crop((0, 0, mask.width, h))
        data = Image.merge("RGBA", (mask.point(lb), mask.point(lg), mask.point(lr), mask)).tobytes()
        self._send(data, x, y, mask.width, h)


def drop_mask(total_w, w, h, f, r, S=2):
    """Gota presa ao topo: corpo com base arredondada + junções côncavas que se abrem na borda da tela."""
    h = max(1, int(h))
    m = Image.new("L", (total_w * S, h * S), 0)
    d = ImageDraw.Draw(m)
    bx0 = (total_w - w) / 2 * S
    bx1 = bx0 + w * S
    fS = f * S
    rS = max(1, min(r * S, w * S / 2, h * S))
    if fS > 0:
        d.rectangle([bx0 - fS, 0, bx1 + fS, min(fS, h * S)], fill=255)
    d.rounded_rectangle([bx0, -rS - 2, bx1 - 1, h * S - 1], radius=rS, fill=255)
    if fS > 0:
        d.ellipse([bx0 - 2 * fS, 0, bx0, 2 * fS], fill=0)
        d.ellipse([bx1, 0, bx1 + 2 * fS, 2 * fS], fill=0)
    return m.reduce(S)


class _GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD), ("hwndActive", wintypes.HWND),
                ("hwndFocus", wintypes.HWND), ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND), ("rcCaret", wintypes.RECT)]


def caret_pos():
    """Onde o texto está sendo digitado (apps que expõem o cursor de texto do Windows)."""
    gi = _GUITHREADINFO()
    gi.cbSize = ctypes.sizeof(gi)
    if not u32.GetGUIThreadInfo(0, ctypes.byref(gi)) or not gi.hwndCaret:
        return None
    pt = wintypes.POINT(gi.rcCaret.left, gi.rcCaret.bottom)
    if not u32.ClientToScreen(gi.hwndCaret, ctypes.byref(pt)):
        return None
    if pt.x == 0 and pt.y == 0:
        return None
    return pt.x, pt.y


class Peek:
    """De tempos em tempos dois olhinhos descem do topo, piscam, olham em volta e espiam o que você faz."""
    DUR = 6.4
    DROP, RISE = 0.42, 0.32

    def __init__(self, app):
        self.app = app
        self.win = None
        self.active = False
        self.last = time.time()
        app.root.after(20000, self._schedule)

    # agenda
    def _schedule(self):
        try:
            mins = int(self.app.st.get("olhos_min", 30) or 0)
            if mins > 0 and time.time() - self.last >= mins * 60 and self.can_show():
                self.show()
        except Exception:
            pass
        self.app.root.after(20000, self._schedule)

    def can_show(self):
        app = self.app
        return (not self.active and app.island.state == "hidden" and not app.is_paused()
                and idle_seconds() < 60 and not app.hold_reason() and not fullscreen_busy())

    # janela
    SS = 3

    def _build(self):
        s = self.app.scale
        self.PW, self.PH = int(118 * s), int(40 * s)
        self.F = int(11 * s)                         # junção côncava com o topo
        self.WT = self.PW + 2 * self.F
        self.EW, self.EH = 11 * s, 17 * s
        self.GAP = 30 * s                            # distância entre os centros dos olhos
        self.GX, self.GY = 6 * s, 3.5 * s            # alcance do olhar
        self.aw = AlphaWindow(self.app.root, self.WT, int(self.PH * 1.2) + 4)
        self.win = self.aw.win
        self.wx = (u32.GetSystemMetrics(0) - self.WT) // 2
        self.win.configure(cursor="hand2")
        self.win.bind("<Button-1>", lambda e: self._to_island())
        self.w, self.h = self.PW, 0
        self.win.withdraw()

    def _render(self, w, h, gx, gy, open_):
        S = self.SS
        h = max(1, int(round(h)))
        f = min(self.F, h // 2)
        mask = drop_mask(self.WT, w, h, f, min(self.PH // 2, int(w) // 2, h), S)
        img = Image.new("RGBA", (self.WT, h), _rgba(ISL["bg"]))
        img.putalpha(mask)
        grow = min(1.0, max(0.0, (h / self.PH - 0.45) / 0.4))     # os olhos surgem com a gota
        if grow > 0:
            em = Image.new("L", (self.WT * S, h * S), 0)
            d = ImageDraw.Draw(em)
            ew = self.EW * S * (0.6 + 0.4 * grow)
            eh = max(2.2 * S, self.EH * S * grow * open_ * (1 - 0.12 * max(0.0, gy)))   # aperta ao olhar p/ baixo
            cx = self.WT * S / 2 + gx * self.GX * S
            cy = h * S * 0.53 + gy * self.GY * S
            for side in (-1, 1):
                ex = cx + side * self.GAP * S / 2 * (w / self.PW)
                d.rounded_rectangle([ex - ew / 2, cy - eh / 2, ex + ew / 2, cy + eh / 2],
                                    radius=min(ew, eh) / 2, fill=255)
            img = Image.composite(Image.new("RGBA", img.size, _rgba(EYE)), img, em.reduce(S))
        self.aw.push(img, self.wx, 0)

    def show(self):
        if self.active:
            return
        if not getattr(self, "aw", None):
            self._build()
        self.active = True
        self.last = time.time()
        self.gx = self.gy = 0.0
        self.mouse = _cursor()
        self.mouse_t = time.perf_counter()
        extra = random.uniform(2.6, self.DUR - 1.6)
        self.blinks = sorted([0.7, extra, self.DUR - 0.8])
        self.w, self.h = self.PW * 0.42, 0
        self._render(self.w, 1, 0, 0, 1.0)
        self.win.deiconify()
        _fast_timer(True)
        self.t0 = time.perf_counter()
        self._frame()

    def hide(self):
        if self.active and self.win:
            self.win.withdraw()
        self.active = False
        _fast_timer(False)

    def _to_island(self):
        self.app.island.open()          # a ilha nasce da gotinha (e a esconde)

    def _target(self, cx, cy):
        """Para onde olhar: texto sendo digitado (se o mouse está parado) ou o ponteiro."""
        m = _cursor()
        now = time.perf_counter()
        if m != self.mouse:
            self.mouse, self.mouse_t = m, now
        tx, ty = m
        if now - self.mouse_t > 1.2:
            c = caret_pos()
            if c:
                tx, ty = c
        sx, sy = self.wx + cx, cy
        dx, dy = tx - sx, ty - sy
        dist = math.hypot(dx, dy) or 1
        reach = min(1.0, dist / (300 * self.app.scale))
        return dx / dist * reach, dy / dist * reach

    def _frame(self):
        if not self.active or not self.win.winfo_exists():
            return
        t = time.perf_counter() - self.t0
        if t >= self.DUR:
            self.hide()
            return
        PW, PH = self.PW, self.PH
        if t < self.DROP:                                # escorre do topo como uma gota…
            q = t / self.DROP
            eh = Island._spring(min(1.0, q * 1.15))
            ew = Island._spring(max(0.0, (q - 0.15) / 0.85))
        elif t > self.DUR - self.RISE:                   # …e recolhe de volta
            q = (t - (self.DUR - self.RISE)) / self.RISE
            ew = 1 - min(1.0, q * 1.25) ** 2
            eh = 1 - q ** 2.2
        else:
            ew = eh = 1.0
        self.w, self.h = PW * (0.42 + 0.58 * ew), PH * eh
        cx, cy = self.WT / 2, self.h / 2

        # roteiro do olhar: lados, depois curioso (segue você), depois centro
        if 1.0 <= t < 1.55:
            tx, ty = -1.0, 0.1
        elif 1.7 <= t < 2.25:
            tx, ty = 1.0, 0.1
        elif 2.45 <= t < self.DUR - 1.0:
            tx, ty = self._target(cx, cy)
        else:
            tx, ty = 0.0, 0.0
        self.gx += (tx - self.gx) * 0.14
        self.gy += (ty - self.gy) * 0.14

        # piscar: fecha e abre em ~0.2 s
        open_ = 1.0
        for b in self.blinks:
            u = (t - b) / 0.2
            if 0 <= u <= 1:
                open_ = min(open_, abs(math.cos(math.pi * u)))
        self._render(self.w, self.h, self.gx, self.gy, open_)
        self.win.after(8, self._frame)


# ───────────────────────── lembrete rápido ─────────────────────────

class QuickAdd:
    MODES = {
        "lembrete": ("LEMBRETE RÁPIDO", "Ex.:  17:30 buscar a Sarah   ·   25 min alongar   ·   amanhã 9h reunião",
                     "Ctrl + Alt + L"),
        "notion": ("NOVA TAREFA NO NOTION", "Ex.:  orçamento Origo amanhã !alta #dunamis   ·   sem data = hoje",
                   "Ctrl + Alt + N"),
    }

    def __init__(self, app, mode="lembrete"):
        self.app, self.mode = app, mode
        head, self.HINT, shortcut = self.MODES[mode]
        t, s, F = app.t, app.scale, app.F
        surf = t["surface"]
        self.W = int(580 * s)
        w = self.win = tk.Toplevel(app.root)
        w.withdraw()
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.configure(bg=surf)

        body = tk.Frame(w, bg=surf)
        body.pack(fill="both", expand=True, padx=int(28 * s), pady=(int(22 * s), int(16 * s)))
        tk.Label(body, text=head, font=F["caps"], bg=surf, fg=t["faint"]).pack(anchor="w")
        self.var = tk.StringVar()
        self.e = tk.Entry(body, textvariable=self.var, font=F["quick"], bg=surf, fg=t["fg"],
                          insertbackground=t["fg"], relief="flat", bd=0, insertwidth=2,
                          selectbackground=t["hover"], selectforeground=t["fg"])
        self.e.pack(fill="x", pady=(int(8 * s), int(6 * s)))
        self.prev = tk.Label(body, text=self.HINT, font=F["sub"], bg=surf, fg=t["faint"], anchor="w")
        self.prev.pack(fill="x")
        tk.Frame(body, bg=t["line"], height=1).pack(fill="x", pady=(int(16 * s), int(10 * s)))
        foot = tk.Frame(body, bg=surf)
        foot.pack(fill="x")
        tk.Label(foot, text="Enter salva   ·   Esc fecha", font=F["small"], bg=surf,
                 fg=t["faint"]).pack(side="left")
        tk.Label(foot, text=shortcut, font=F["small"], bg=surf, fg=t["faint"]).pack(side="right")
        self.sending = False

        self.e.bind("<KeyRelease>", self._update)
        self.e.bind("<Return>", self._save)
        self.e.bind("<Escape>", lambda e: self.close())
        self.e.bind("<FocusOut>", lambda e: self.win.after(200, self._maybe_close))

        w.update_idletasks()
        h = w.winfo_reqheight()
        l, tp, r, b = work_area()
        x = l + (r - l - self.W) // 2
        y = tp + int((b - tp) * 0.26)
        w.geometry(f"{self.W}x{h}+{x}+{y}")
        style_floating(w, t, no_activate=False)
        w.deiconify()
        w.after(20, self.focus)

    def focus(self):
        try:
            force_foreground(hwnd_of(self.win))
        except Exception:
            pass
        self.win.focus_force()
        self.e.focus_set()

    def _maybe_close(self):
        if self.win.winfo_exists() and self.win.focus_get() is None and not self.sending:
            self.close()

    def _task_text(self, titulo, when, timed, prio, setor, now):
        if timed:
            quando = describe_when(when, now)
        else:
            delta = (when - now.date()).days
            quando = "hoje" if delta == 0 else "amanhã" if delta == 1 else f"{DIAS[when.weekday()].lower()} {when:%d/%m}"
        extras = "   ·   ".join(x for x in (quando, prio, setor) if x)
        return f"{titulo}   →   {extras}"

    def _update(self, _e=None):
        t = self.app.t
        txt = self.var.get().strip()
        if not txt:
            self.prev.configure(text=self.HINT, fg=t["faint"])
            return
        try:
            now = dt.datetime.now()
            if self.mode == "notion":
                self.prev.configure(text=self._task_text(*parse_task(txt, now, self.app.notion_info), now),
                                    fg=t["sub"])
                return
            titulo, when = parse_quick(txt, now)
            self.prev.configure(text=f"{titulo}   →   {describe_when(when, now)}", fg=t["sub"])
        except ValueError as ex:
            self.prev.configure(text=str(ex), fg=t["faint"])

    def _save_task(self):
        t = self.app.t
        if self.sending:
            return "break"
        now = dt.datetime.now()
        try:
            parsed = parse_task(self.var.get(), now, self.app.notion_info)
        except ValueError as ex:
            self.prev.configure(text=str(ex), fg=t["danger"])
            return "break"
        self.sending = True
        self.prev.configure(text="Enviando para o Notion…", fg=t["sub"])
        result = {}
        self.app.create_notion_task(*parsed, result)
        resumo = self._task_text(*parsed, now)

        def check():
            if not self.win.winfo_exists():
                if "ok" in result:
                    self.app.task_created(result["ok"])
                elif not result:
                    self.app.root.after(150, check)
                return
            if not result:
                self.win.after(150, check)
            elif "ok" in result:
                self.app.task_created(result["ok"])
                self.e.configure(state="disabled", disabledbackground=t["surface"], disabledforeground=t["sub"])
                self.prev.configure(text="Criada no Notion   ·   " + resumo.split("→")[-1].strip(), fg=t["fg"])
                self.win.after(700, self.close)
            else:
                self.sending = False
                self.prev.configure(text=result["err"], fg=t["danger"])
        check()
        return "break"

    def _save(self, _e=None):
        t = self.app.t
        if self.mode == "notion":
            return self._save_task()
        try:
            now = dt.datetime.now()
            titulo, when = parse_quick(self.var.get(), now)
        except ValueError as ex:
            self.prev.configure(text=str(ex), fg=t["danger"])
            return "break"
        self.app.add_avulso(titulo, when)
        self.e.configure(state="disabled", disabledbackground=t["surface"], disabledforeground=t["sub"])
        self.prev.configure(text=f"Salvo   ·   {describe_when(when, now)}", fg=t["fg"])
        self.win.after(650, self.close)
        return "break"

    def close(self):
        if self.app.quick is self:
            self.app.quick = None
        try:
            self.win.destroy()
        except tk.TclError:
            pass


# ───────────────────────── pomodoro ─────────────────────────

class Pomodoro:
    KEYS = {"foco": "pomo_foco", "pausa": "pomo_pausa", "longa": "pomo_longa"}

    def __init__(self, app):
        self.app = app
        self.phase = self.end = self.left = None
        self.total, self.cycle = 1, 0

    @property
    def running(self):
        return self.phase is not None

    @property
    def paused(self):
        return self.left is not None

    def mins(self, phase):
        return int(self.app.st[self.KEYS[phase]])

    def begin(self, phase):
        self.phase = phase
        self.total = self.mins(phase) * 60
        self.end = dt.datetime.now() + dt.timedelta(seconds=self.total)
        self.left = None

    def remaining(self):
        if self.left is not None:
            return self.left
        return max(0.0, (self.end - dt.datetime.now()).total_seconds())

    def start(self):
        self.cycle = 0
        self.begin("foco")
        extra = " Os lembretes esperam até a pausa." if self.app.st["pomo_segurar"] else ""
        Popup(self.app, {"titulo": "Foco iniciado", "_sistema": True, "cor": POMO_FOCO,
                         "mensagem": f"{self.mins('foco')} minutos.{extra}"}, sound=False)
        self.app.on_pomo_change()

    def stop(self):
        self.phase = self.left = None
        self.app.on_pomo_change()

    def toggle(self):
        if self.running:
            self.stop()
        else:
            self.start()

    def toggle_pause(self):
        if not self.running:
            return
        if self.paused:
            self.end = dt.datetime.now() + dt.timedelta(seconds=self.left)
            self.left = None
        else:
            self.left = self.remaining()
        self.app.on_pomo_change()

    def skip(self):
        self.advance(skipped=True)

    def advance(self, skipped=False):
        app = self.app
        if self.phase == "foco":
            if not skipped:
                self.cycle += 1
                app.log_pomo()
            longa = not skipped and self.cycle % max(1, int(app.st["pomo_ciclos"])) == 0
            self.begin("longa" if longa else "pausa")
            if not skipped:
                Popup(app, {"titulo": f"Pausa de {self.mins(self.phase)} min", "_sistema": True,
                            "cor": POMO_PAUSA, "mensagem": "Pomodoro concluído. Levante, alongue, respire."})
        else:
            self.begin("foco")
            if not skipped:
                Popup(app, {"titulo": "De volta ao foco", "_sistema": True, "cor": POMO_FOCO,
                            "mensagem": f"{self.mins('foco')} minutos de concentração."})
        app.on_pomo_change()

    def tick(self):
        if self.running and not self.paused and self.remaining() <= 0:
            self.advance()

    def label(self):
        if self.phase == "foco":
            n = int(self.app.st["pomo_ciclos"])
            return f"Foco  ·  {self.cycle % n + 1} de {n}"
        return "Pausa longa" if self.phase == "longa" else "Pausa curta"


# ───────────────────────── diálogos ─────────────────────────

class Dialog(tk.Toplevel):
    def __init__(self, app, title):
        super().__init__(app.root)
        self.app = app
        t, s = app.t, app.scale
        self.withdraw()
        self.title(title)
        self.configure(bg=t["bg"])
        self.resizable(False, False)
        self.transient(app.root)
        self.iconphoto(False, app.icon_photo)
        self.body = tk.Frame(self, bg=t["bg"])
        self.body.pack(fill="both", expand=True, padx=int(32 * s), pady=(int(26 * s), int(26 * s)))
        tk.Label(self.body, text=title, font=app.F["h2"], bg=t["bg"], fg=t["fg"]).pack(anchor="w")
        self.bind("<Escape>", lambda e: self.destroy())

    def section(self, parent, text, top=18):
        tk.Label(parent, text=text.upper(), font=self.app.F["caps"], bg=self.app.t["bg"],
                 fg=self.app.t["faint"]).pack(anchor="w", pady=(int(top * self.app.scale), int(8 * self.app.scale)))

    def footer(self):
        t, s = self.app.t, self.app.scale
        tk.Frame(self.body, bg=t["line"], height=1).pack(fill="x", pady=(int(22 * s), int(18 * s)))
        f = tk.Frame(self.body, bg=t["bg"])
        f.pack(fill="x")
        return f

    def present(self):
        self.app.center(self, over=self.app.root)
        self.deiconify()
        style_window(self, self.app.t)
        self.grab_set()
        self.focus_force()


class Editor(Dialog):
    def __init__(self, app, lemb=None):
        super().__init__(app, "Editar lembrete" if lemb else "Novo lembrete")
        t, s, F = app.t, app.scale, app.F
        self.result = None
        self.orig = lemb or {}
        d = {"titulo": "", "mensagem": "", "tipo": "intervalo", "intervalo": 60, "inicio": "08:00",
             "fim": "18:00", "horarios": ["09:00"], "dias": [0, 1, 2, 3, 4], "cor": PALETTE[0],
             "ativo": True, "meta": 0}
        d.update(self.orig)
        self.cor = d["cor"]
        self.armed_delete = False
        b = self.body

        self.v_titulo = tk.StringVar(value=d["titulo"])
        self.v_msg = tk.StringVar(value=d["mensagem"])
        self.v_ini = tk.StringVar(value=d["inicio"])
        self.v_fim = tk.StringVar(value=d["fim"])
        self.v_hor = tk.StringVar(value=", ".join(d["horarios"]) or "09:00")

        self.section(b, "Título", top=20)
        ft = Field(b, app, self.v_titulo, width=46)
        ft.pack(fill="x")
        self.section(b, "Mensagem  (opcional)", top=12)
        Field(b, app, self.v_msg).pack(fill="x")

        self.section(b, "Repetição")
        self.seg = Segmented(b, app, [("intervalo", "Intervalo"), ("horarios", "Horários fixos")],
                             d["tipo"], command=self._sync)
        self.seg.pack(anchor="w")
        self.panel = tk.Frame(b, bg=t["bg"])
        self.panel.pack(fill="x", pady=(int(12 * s), 0))

        self.p_int = tk.Frame(self.panel, bg=t["bg"])
        lab = dict(font=F["sub"], bg=t["bg"], fg=t["sub"])
        tk.Label(self.p_int, text="A cada", **lab).pack(side="left", padx=(0, 8))
        self.step = Stepper(self.p_int, app, d["intervalo"], 1, 1440, 5, "min")
        self.step.pack(side="left")
        tk.Label(self.p_int, text="das", **lab).pack(side="left", padx=(int(12 * s), 8))
        Field(self.p_int, app, self.v_ini, width=5, justify="center").pack(side="left")
        tk.Label(self.p_int, text="às", **lab).pack(side="left", padx=8)
        Field(self.p_int, app, self.v_fim, width=5, justify="center").pack(side="left")

        self.p_hor = tk.Frame(self.panel, bg=t["bg"])
        Field(self.p_hor, app, self.v_hor).pack(fill="x")
        tk.Label(self.p_hor, text="Separe por vírgula — ex.: 09:00, 13:30, 17:00", font=F["small"],
                 bg=t["bg"], fg=t["faint"]).pack(anchor="w", pady=(int(6 * s), 0))

        self.section(b, "Dias")
        df = tk.Frame(b, bg=t["bg"])
        df.pack(anchor="w")
        pw, ph = int(46 * s), int(30 * s)
        self.days = []
        for i, n in enumerate(DIAS):
            p = Pill(df, app, n, pw, ph, selected=i in d["dias"], command=lambda p: p.set(not p.selected))
            p.pack(side="left", padx=(0, int(6 * s)))
            self.days.append(p)

        two = tk.Frame(b, bg=t["bg"])
        two.pack(fill="x")
        left = tk.Frame(two, bg=t["bg"])
        left.pack(side="left", anchor="n")
        right = tk.Frame(two, bg=t["bg"])
        right.pack(side="right", anchor="n")
        self.section(left, "Cor")
        cf = tk.Frame(left, bg=t["bg"])
        cf.pack(anchor="w")
        self.swatches = {}
        palette = PALETTE + ([self.cor] if self.cor not in PALETTE else [])
        for c in palette:
            lb = tk.Label(cf, bg=t["bg"], bd=0, cursor="hand2")
            lb.pack(side="left", padx=(0, int(4 * s)))
            lb.bind("<Button-1>", lambda e, c=c: self._set_cor(c))
            self.swatches[c] = lb
        self._set_cor(self.cor)
        self.section(right, "Meta por dia")
        self.meta = Stepper(right, app, d.get("meta", 0), 0, 50, 1, "×")
        self.meta.pack(anchor="w")
        tk.Label(b, text="Meta: conta cada “Feito”. Ao atingir, o lembrete descansa até amanhã (0 = sem meta).",
                 font=F["small"], bg=t["bg"], fg=t["faint"]).pack(anchor="w", pady=(int(10 * s), 0))

        self.err = tk.Label(b, text="", font=F["small"], bg=t["bg"], fg=t["danger"])
        self.err.pack(anchor="w", pady=(int(8 * s), 0))

        f = self.footer()
        if lemb:
            self.del_btn = Btn(f, app, "Excluir", self._delete, kind="link")
            self.del_btn.pack(side="left")
        Btn(f, app, "Salvar", self._save).pack(side="right")
        Btn(f, app, "Testar", self._test, kind="ghost").pack(side="right", padx=(0, int(8 * s)))

        self.bind("<Return>", lambda e: self._save())
        self._sync(d["tipo"])
        self.present()
        ft.e.focus_set()
        ft.e.icursor("end")

    def _sync(self, tipo):
        for p in (self.p_int, self.p_hor):
            p.pack_forget()
        (self.p_int if tipo == "intervalo" else self.p_hor).pack(fill="x")

    def _set_cor(self, c):
        self.cor = c
        s, t = self.app.scale, self.app.t
        d = int(30 * s)
        for col, lb in self.swatches.items():
            img = circle_photo(d, fill=col, ring=t["fg"], width=1.3 * s) if col == c else \
                circle_photo(int(d * 0.73), fill=col)
            lb.configure(image=img, width=d, height=d)

    def _collect(self):
        titulo = self.v_titulo.get().strip()
        if not titulo:
            raise ValueError("Dê um título ao lembrete.")
        dias = [i for i, p in enumerate(self.days) if p.selected]
        if not dias:
            raise ValueError("Escolha pelo menos um dia.")
        l = dict(self.orig)
        l.update({"titulo": titulo, "mensagem": self.v_msg.get().strip(), "tipo": self.seg.value,
                  "dias": dias, "cor": self.cor})
        l.setdefault("ativo", True)
        l.setdefault("id", uuid.uuid4().hex[:8])
        l.setdefault("horarios", [])
        try:
            l["meta"] = self.meta.get()
            if l["tipo"] == "intervalo":
                n = self.step.get()
                a, b = parse_hm(self.v_ini.get()), parse_hm(self.v_fim.get())
                if a > b:
                    raise ValueError("O horário inicial precisa ser antes do final.")
                l.update(intervalo=n, inicio=fmt_hm(a), fim=fmt_hm(b))
            else:
                hs = [h for h in re.split(r"[,;\s]+", self.v_hor.get()) if h]
                if not hs:
                    raise ValueError("Informe ao menos um horário.")
                l["horarios"] = [fmt_hm(m) for m in sorted({parse_hm(h) for h in hs})]
        except ValueError as e:
            msg = str(e)
            raise ValueError(msg if msg.startswith(("O ", "Informe", "Use")) else "Horário inválido — use o formato HH:MM.")
        return l

    def _test(self):
        try:
            self.err.configure(text="")
            Popup(self.app, self._collect())
        except ValueError as e:
            self.err.configure(text=str(e))

    def _save(self):
        try:
            self.result = ("save", self._collect())
        except ValueError as e:
            self.err.configure(text=str(e))
            return
        self.destroy()

    def _delete(self):
        if not self.armed_delete:
            self.armed_delete = True
            self.del_btn.configure(text="Clique de novo para excluir", fg=self.app.t["danger"])
            self.del_btn.fg_n = self.del_btn.fg_h = self.app.t["danger"]
            return
        self.result = ("delete", self.orig)
        self.destroy()


class SettingsDialog(Dialog):
    TABS = [("geral", "Geral"), ("som", "Som"), ("foco", "Foco"), ("pomodoro", "Pomodoro"), ("notion", "Notion")]

    def __init__(self, app, tab="geral"):
        super().__init__(app, "Configurações")
        t, s, F = app.t, app.scale, app.F
        st = app.st
        b = self.body

        tabs = Segmented(b, app, self.TABS, tab, command=self._show)
        tabs.pack(anchor="w", pady=(int(18 * s), int(6 * s)))
        self.holder = tk.Frame(b, bg=t["bg"])
        self.holder.pack(fill="x")
        self.panels = {k: tk.Frame(self.holder, bg=t["bg"]) for k, _ in self.TABS}

        def row(parent, label, hint=None):
            r = tk.Frame(parent, bg=t["bg"])
            r.pack(fill="x", pady=int(7 * s))
            txt = tk.Frame(r, bg=t["bg"])
            txt.pack(side="left", fill="x", expand=True)
            tk.Label(txt, text=label, font=F["body"], bg=t["bg"], fg=t["fg"]).pack(anchor="w")
            if hint:
                tk.Label(txt, text=hint, font=F["small"], bg=t["bg"], fg=t["faint"]).pack(anchor="w")
            return r

        def note(parent, text):
            tk.Label(parent, text=text, font=F["small"], bg=t["bg"], fg=t["faint"], justify="left",
                     wraplength=int(420 * s)).pack(anchor="w", pady=(int(12 * s), 0))

        # Geral
        p = self.panels["geral"]
        self.section(p, "Aparência", top=12)
        self.tema = Segmented(row(p, "Tema", "Automático segue o Windows"), app,
                              [("claro", "Claro"), ("escuro", "Escuro"), ("auto", "Automático")], st["tema"])
        self.tema.pack(side="right")
        self.pos = Segmented(row(p, "Posição do pop-up"), app,
                             [("inferior", "Embaixo"), ("superior", "Em cima")], st["posicao"])
        self.pos.pack(side="right")
        self.op = Stepper(row(p, "Opacidade"), app, int(round(st["opacidade"] * 100)), 40, 100, 5, "%")
        self.op.pack(side="right")
        self.section(p, "Pop-up")
        self.dur = Stepper(row(p, "Tempo na tela", "O mouse em cima pausa a contagem"),
                           app, st["duracao"], 2, 120, 1, "s")
        self.dur.pack(side="right")
        self.adiar = Stepper(row(p, "Adiar por"), app, st["adiar_min"], 1, 240, 5, "min")
        self.adiar.pack(side="right")
        self.auto = Toggle(row(p, "Iniciar com o Windows", "Abre direto na bandeja"), app,
                           st["iniciar_com_windows"])
        self.auto.pack(side="right")
        self.ilha = Toggle(row(p, "Ilha no topo da tela", "Leve o mouse ao topo, no centro"), app,
                           st.get("ilha", True))
        self.ilha.pack(side="right")
        r = row(p, "Olhinhos curiosos", "Descem do topo, piscam e espiam  ·  0 = desligado")
        self.olhos = Stepper(r, app, st.get("olhos_min", 30), 0, 240, 5, "min")
        self.olhos.pack(side="right")
        Btn(r, app, "ver", lambda: (self.app.peek.hide(), self.app.peek.show()), kind="link",
            font=F["small"]).pack(side="right", padx=(0, int(10 * s)))
        ok = app.hotkeys.ok if app.hotkeys else {}
        aviso = "" if all(ok.values()) and ok else "   (algum atalho está em uso por outro app)"
        note(p, "Atalhos:  Ctrl + Alt + L  lembrete rápido   ·   Ctrl + Alt + N  tarefa no Notion   ·   "
                "Ctrl + Alt + P  pomodoro" + aviso)

        # Som
        p = self.panels["som"]
        self.section(p, "Som dos lembretes", top=12)
        self.som = Segmented(row(p, "Som"), app, SOUNDS, st["som"],
                             command=lambda v: play_sound(v, self.vol.value))
        self.som.pack(side="right")
        self.vol = Segmented(row(p, "Volume"), app,
                             [("baixo", "Baixo"), ("medio", "Médio"), ("alto", "Alto")], st["volume"],
                             command=lambda v: play_sound(self.som.value, v))
        self.vol.pack(side="right")
        note(p, "Sons graves e abafados, pensados para avisar sem assustar. Clique numa opção para ouvir.")

        # Foco
        p = self.panels["foco"]
        self.section(p, "Foco automático", top=12)
        self.f_call = Toggle(row(p, "Durante chamadas", "Microfone em uso — Teams, Meet, Zoom…"),
                             app, st["foco_chamadas"])
        self.f_call.pack(side="right")
        self.f_full = Toggle(row(p, "Em tela cheia", "Vídeos, jogos e apresentações"),
                             app, st["foco_tela_cheia"])
        self.f_full.pack(side="right")
        note(p, "Nesses momentos os lembretes não aparecem. Eles ficam guardados e surgem num resumo "
                "assim que você terminar.")

        # Pomodoro
        p = self.panels["pomodoro"]
        self.section(p, "Pomodoro", top=12)
        self.pf = Stepper(row(p, "Foco"), app, st["pomo_foco"], 5, 120, 5, "min")
        self.pf.pack(side="right")
        self.pp = Stepper(row(p, "Pausa curta"), app, st["pomo_pausa"], 1, 30, 1, "min")
        self.pp.pack(side="right")
        self.pl = Stepper(row(p, "Pausa longa"), app, st["pomo_longa"], 5, 60, 5, "min")
        self.pl.pack(side="right")
        self.pc = Stepper(row(p, "Pausa longa a cada"), app, st["pomo_ciclos"], 2, 8, 1, "×")
        self.pc.pack(side="right")
        self.ps = Toggle(row(p, "Segurar lembretes no foco", "Eles aparecem num resumo na pausa"),
                         app, st["pomo_segurar"])
        self.ps.pack(side="right")

        # Notion
        p = self.panels["notion"]
        self.section(p, "Conexão", top=12)
        self.v_token = tk.StringVar(value=unprotect(st.get("notion_token", "")))
        self.v_db = tk.StringVar(value=st.get("notion_db", ""))
        Field(row(p, "Token da integração", "Fica criptografado neste PC"), app, self.v_token,
              width=26, show="•").pack(side="right")
        Field(row(p, "Link do banco", "Copie o link do banco no Notion"), app, self.v_db,
              width=26).pack(side="right")
        r = tk.Frame(p, bg=t["bg"])
        r.pack(fill="x", pady=(int(6 * s), 0))
        self.n_status = tk.Label(r, text=self._notion_status(), font=F["small"], bg=t["bg"], fg=t["sub"],
                                 anchor="w", justify="left", wraplength=int(300 * s))
        self.n_status.pack(side="left", fill="x", expand=True)
        Btn(r, app, "Testar conexão", self._test_notion, kind="ghost", h=30, padx=14).pack(side="right")
        self.section(p, "Avisos por período")
        self.n_avisar = Toggle(row(p, "Avisar no início de cada período", "Lista o que falta fazer"),
                               app, st.get("notion_avisar", True))
        self.n_avisar.pack(side="right")
        hf = tk.Frame(p, bg=t["bg"])
        hf.pack(fill="x", pady=(int(4 * s), 0))
        self.v_per = {}
        for key, label in PERIODS:
            cell = tk.Frame(hf, bg=t["bg"])
            cell.pack(side="left", padx=(0, int(14 * s)))
            tk.Label(cell, text=label, font=F["small"], bg=t["bg"], fg=t["sub"]).pack(anchor="w")
            self.v_per[key] = tk.StringVar(value=st["notion_horarios"].get(key, ""))
            Field(cell, app, self.v_per[key], width=5, justify="center").pack(anchor="w", pady=(int(3 * s), 0))
        link = Btn(p, app, "Como conectar o Notion  ↗", self._help_notion, kind="link", font=F["small"])
        link.pack(anchor="w", pady=(int(14 * s), 0))

        # altura fixa = maior aba
        self.update_idletasks()
        W = max(pp.winfo_reqwidth() for pp in self.panels.values())
        H = max(pp.winfo_reqheight() for pp in self.panels.values())
        self.holder.configure(width=W, height=H)
        self.holder.pack_propagate(False)
        self._show(tab)

        self.err = tk.Label(b, text="", font=F["small"], bg=t["bg"], fg=t["danger"])
        self.err.pack(anchor="w", pady=(int(6 * s), 0))
        f = self.footer()
        Btn(f, app, "Sair do app", app.quit, kind="link").pack(side="left")
        Btn(f, app, "Salvar", self._save).pack(side="right")
        Btn(f, app, "Testar", self._test, kind="ghost").pack(side="right", padx=(0, int(8 * s)))
        self.present()

    def _show(self, key):
        for p in self.panels.values():
            p.pack_forget()
        self.panels[key].pack(fill="both", expand=True)

    def _notion_status(self):
        app = self.app
        if not app.notion_configured():
            return "Não conectado."
        if app.notion_err:
            return app.notion_err
        nt = app.notion_today() or []
        quando = f", sincronizado às {app.notion_at:%H:%M}" if app.notion_at else ""
        return f"Conectado  ·  {len(app.notion_acts)} atividades, {len(nt)} hoje{quando}"

    def _help_notion(self):
        webbrowser.open("https://www.notion.so/profile/integrations")
        self.n_status.configure(
            text="1. Crie uma integração interna e copie o token.  2. No banco, ••• → Conexões → adicione "
                 "a integração.  3. Cole o token e o link do banco aqui.", fg=self.app.t["sub"])

    def _test_notion(self):
        token, link = self.v_token.get().strip(), self.v_db.get().strip()
        self.n_status.configure(text="Testando…", fg=self.app.t["sub"])
        result = {}

        def run():
            try:
                acts = notion_parse(notion_fetch(token, link))
                if any(a.get("dias") for a in acts):
                    hoje = sum(1 for a in acts if notion_applies(a, dt.date.today()))
                    result["ok"] = f"Conectado  ·  rotina com {len(acts)} atividades, {hoje} hoje"
                else:
                    result["ok"] = f"Conectado  ·  {len(acts)} tarefas até hoje"
            except NotionError as e:
                result["err"] = str(e)
            except Exception as e:
                result["err"] = f"Erro: {e}"
        threading.Thread(target=run, daemon=True).start()

        def check():
            if not self.winfo_exists():
                return
            if not result:
                self.after(150, check)
            elif "ok" in result:
                self.n_status.configure(text=result["ok"], fg=self.app.t["fg"])
            else:
                self.n_status.configure(text=result["err"], fg=self.app.t["danger"])
        check()

    def _values(self):
        try:
            return {"duracao": self.dur.get(), "adiar_min": self.adiar.get(),
                    "opacidade": self.op.get() / 100, "tema": self.tema.value, "posicao": self.pos.value,
                    "som": self.som.value, "volume": self.vol.value,
                    "iniciar_com_windows": self.auto.value,
                    "foco_chamadas": self.f_call.value, "foco_tela_cheia": self.f_full.value,
                    "pomo_foco": self.pf.get(), "pomo_pausa": self.pp.get(), "pomo_longa": self.pl.get(),
                    "pomo_ciclos": self.pc.get(), "pomo_segurar": self.ps.value,
                    "ilha": self.ilha.value, "olhos_min": self.olhos.get(), "notion_avisar": self.n_avisar.value,
                    "notion_db": self.v_db.get().strip(),
                    "notion_horarios": {k: self._hm(v.get()) for k, v in self.v_per.items()}}
        except ValueError as e:
            self.err.configure(text=str(e))
            return None

    @staticmethod
    def _hm(s):
        try:
            return fmt_hm(parse_hm(s))
        except ValueError:
            raise ValueError("Horário de período inválido — use HH:MM.")

    def _test(self):
        v = self._values()
        if v:
            app = self.app
            old, old_t = app.cfg["settings"], app.t
            app.cfg["settings"] = {**old, **v}
            app.t = THEMES[app.theme_name()]
            Popup(app, {"titulo": "Assim fica o seu lembrete", "cor": PALETTE[0],
                        "mensagem": "Discreto, no canto da tela. Clique para fechar."})
            app.cfg["settings"], app.t = old, old_t

    def _save(self):
        v = self._values()
        if not v:
            return
        st = self.app.st
        if v["iniciar_com_windows"] != st["iniciar_com_windows"]:
            try:
                set_autostart(v["iniciar_com_windows"])
            except OSError as e:
                self.err.configure(text=f"Não consegui alterar a inicialização: {e}")
                return
        token = self.v_token.get().strip()
        notion_changed = (token != unprotect(st.get("notion_token", ""))) or v["notion_db"] != st.get("notion_db")
        v["notion_token"] = protect(token)
        st.update(v)
        self.destroy()
        if notion_changed:
            self.app.notion_err = None
            if not self.app.notion_configured():
                self.app.notion_acts = []
            self.app.sync_notion()
        if self.app.theme_name() != self.app.theme_cur:
            self.app.apply_theme()
        self.app.layout_popups()
        self.app.save()
        if st["som"] != "nenhum":
            threading.Thread(target=sound_path, args=(st["som"], st["volume"]), daemon=True).start()


# ───────────────────────── app principal ─────────────────────────

class App:
    def __init__(self, start_hidden=False):
        self.cfg = load_cfg()
        self.q = queue.Queue()
        self.popups, self.snoozes = [], []
        self.fired, self.fired_date = set(), dt.date.today()
        self.last_check = dt.datetime.now()
        self.missed = {}                  # id -> [lembrete, vezes]
        self.paused_until = None
        self.tray = self.hotkeys = self.quick = None
        self._mic = (0.0, False)
        self.notion_acts, self.notion_err, self.notion_at, self._syncing = [], None, None, False
        self.notion_info = {}
        try:
            with open(os.path.join(DATA_DIR, "notion_cache.json"), encoding="utf-8") as f:
                cache = json.load(f)
            if isinstance(cache, dict):
                self.notion_acts, self.notion_info = cache.get("acts", []), cache.get("info", {})
            else:
                self.notion_acts = cache
        except (OSError, ValueError):
            pass
        if self.st["iniciar_com_windows"]:
            try:
                set_autostart(True)       # mantém o caminho do executável atualizado
            except OSError:
                pass

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title(APP)
        self.scale = max(1.0, self.root.winfo_fpixels("1i") / 96)
        self._fonts()
        s = self.scale
        self.root.geometry(f"{int(660 * s)}x{int(600 * s)}")
        self.root.minsize(int(540 * s), int(420 * s))
        self.root.protocol("WM_DELETE_WINDOW", self.hide)
        self.icon_photo = ImageTk.PhotoImage(make_icon_image(64))
        self.root.iconphoto(True, self.icon_photo)
        self.root.bind_all("<MouseWheel>", self._wheel)
        self.root.bind("<Control-n>", lambda e: self.new())

        self.pomo = Pomodoro(self)
        self.hotkeys = Hotkeys(self.q, [(0x2 | 0x1, 0x4C, "quick"), (0x2 | 0x1, 0x50, "pomo"),
                                        (0x2 | 0x1, 0x4E, "task")])
        self.hotkeys.start()
        self.hotkeys.ready.wait(2)

        self.apply_theme()
        self.has_tray = self._start_tray()
        save_cfg(self.cfg)
        if self.st["som"] != "nenhum":
            threading.Thread(target=sound_path, args=(self.st["som"], self.st["volume"]), daemon=True).start()

        if not (start_hidden and self.has_tray):
            self.center(self.root)
            self.show()

        self.island = Island(self)
        self.peek = Peek(self)
        self.root.after(1000, self.tick)
        self.root.after(200, self.poll)
        self.root.after(2000, self.watch_return)
        self.root.after(1000, self.pomo_loop)
        self.root.after(1500, self.notion_loop)

    def _fonts(self):
        fams = set(tkfont.families(self.root))

        def pick(*names):
            return next((n for n in names if n in fams), "Segoe UI")
        disp_l = pick("Segoe UI Variable Display Light", "Segoe UI Light")
        disp = pick("Segoe UI Variable Display", "Segoe UI")
        text = pick("Segoe UI Variable Text", "Segoe UI")
        text_b = pick("Segoe UI Variable Text Semibold", "Segoe UI Semibold")
        small = pick("Segoe UI Variable Small", "Segoe UI")
        small_b = pick("Segoe UI Variable Small Semibol", "Segoe UI Semibold")
        icon = pick("Segoe Fluent Icons", "Segoe MDL2 Assets")
        self.F = {
            "h1": (disp_l, 24), "h2": (disp, 16), "title": (text_b, 10), "row": (text, 11),
            "body": (text, 10), "sub": (text, 9), "small": (small, 8), "small_b": (small_b, 8),
            "caps": (small_b, 7), "btn": (text_b, 9), "pill": (text, 9), "time": (disp_l, 13),
            "icon": (icon, 11), "icon_s": (icon, 8), "quick": (disp_l, 17), "pomo": (disp_l, 26),
            "isl_time": (disp_l, 22), "done": (text, 10, "overstrike"),
        }

    @property
    def st(self):
        return self.cfg["settings"]

    def theme_name(self):
        tema = self.st["tema"]
        if tema == "auto":
            return "claro" if system_light() else "escuro"
        return tema

    def play(self):
        play_sound(self.st["som"], self.st["volume"])

    def dot(self, color, filled=True):
        d = max(8, int(8 * self.scale))
        if filled:
            return circle_photo(d, fill=color)
        return circle_photo(d, outline=self.t["faint"], width=1.2 * self.scale)

    # ── interface ──
    def apply_theme(self):
        self.theme_cur = self.theme_name()
        self.t = THEMES[self.theme_cur]
        for w in self.root.winfo_children():
            if not isinstance(w, tk.Toplevel):
                w.destroy()
        self._build()
        self.refresh()
        self.refresh_pomo()
        style_window(self.root, self.t)

    def _build(self):
        t, s, F = self.t, self.scale, self.F
        self.root.configure(bg=t["bg"])
        P = int(36 * s)
        self.P = P

        self.head = head = tk.Frame(self.root, bg=t["bg"])
        head.pack(fill="x", padx=P, pady=(int(30 * s), 0))
        left = tk.Frame(head, bg=t["bg"])
        left.pack(side="left", fill="x", expand=True)
        tk.Label(left, text=APP, font=F["h1"], bg=t["bg"], fg=t["fg"]).pack(anchor="w")
        self.lbl_status = tk.Label(left, text="", font=F["sub"], bg=t["bg"], fg=t["sub"])
        self.lbl_status.pack(anchor="w", pady=(int(2 * s), 0))
        right = tk.Frame(head, bg=t["bg"])
        right.pack(side="right", anchor="n", pady=(int(6 * s), 0))
        IconBtn(right, self, G_TIMER, self.pomo.toggle).pack(side="left")
        self.btn_pause = IconBtn(right, self, G_PAUSE, self.toggle_pause)
        self.btn_pause.pack(side="left", padx=(int(2 * s), 0))
        IconBtn(right, self, G_SETTINGS, lambda: SettingsDialog(self)).pack(side="left", padx=(int(2 * s), 0))

        # faixa do pomodoro
        self.band = tk.Frame(self.root, bg=t["bg"])
        card = tk.Frame(self.band, bg=t["row_hover"])
        card.pack(fill="x")
        inner = tk.Frame(card, bg=t["row_hover"])
        inner.pack(fill="x", padx=int(20 * s), pady=(int(14 * s), int(12 * s)))
        txt = tk.Frame(inner, bg=t["row_hover"])
        txt.pack(side="left")
        self.lbl_phase = tk.Label(txt, text="", font=F["caps"], bg=t["row_hover"], fg=t["faint"])
        self.lbl_phase.pack(anchor="w")
        self.lbl_clock = tk.Label(txt, text="", font=F["pomo"], bg=t["row_hover"], fg=t["fg"])
        self.lbl_clock.pack(anchor="w")
        ctr = tk.Frame(inner, bg=t["row_hover"])
        ctr.pack(side="right")
        IconBtn(ctr, self, G_STOP, self.pomo.stop).pack(side="right")
        IconBtn(ctr, self, G_SKIP, self.pomo.skip).pack(side="right", padx=(0, int(2 * s)))
        self.btn_pp = IconBtn(ctr, self, G_PAUSE, self.pomo.toggle_pause)
        self.btn_pp.pack(side="right", padx=(0, int(2 * s)))
        self.pomo_bar = tk.Canvas(card, height=max(2, int(2 * s)), bg=t["line"], highlightthickness=0, bd=0)
        self.pomo_bar.pack(fill="x", side="bottom")
        self.pomo_fill = self.pomo_bar.create_rectangle(0, 0, 0, int(2 * s) + 1, width=0, fill=POMO_FOCO)

        foot = tk.Frame(self.root, bg=t["bg"])
        foot.pack(side="bottom", fill="x", padx=P, pady=(int(16 * s), int(28 * s)))
        Btn(foot, self, "Novo lembrete", self.new).pack(side="left")
        Btn(foot, self, "Lembrete rápido", self.open_quick, kind="ghost").pack(side="left", padx=(int(8 * s), 0))
        self.lbl_count = tk.Label(foot, text="", font=F["small"], bg=t["bg"], fg=t["faint"])
        self.lbl_count.pack(side="right")

        self.wrap = wrap = tk.Frame(self.root, bg=t["bg"])
        wrap.pack(fill="both", expand=True, padx=P - int(12 * s), pady=(int(20 * s), 0))
        self.canvas = tk.Canvas(wrap, bg=t["bg"], highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.list = tk.Frame(self.canvas, bg=t["bg"])
        win_id = self.canvas.create_window(0, 0, window=self.list, anchor="nw")
        self.list.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(win_id, width=e.width))

    def _wheel(self, e):
        try:
            if e.widget.winfo_toplevel() is not self.root:
                return
            if self.list.winfo_height() > self.canvas.winfo_height():
                self.canvas.yview_scroll(-int(e.delta / 120), "units")
        except (AttributeError, tk.TclError):
            pass

    def center(self, win, over=None):
        win.update_idletasks()
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        if win is self.root:
            w, h = int(660 * self.scale), int(600 * self.scale)
        if over is not None and over.winfo_viewable():
            x = over.winfo_rootx() + (over.winfo_width() - w) // 2
            y = over.winfo_rooty() + (over.winfo_height() - h) // 3
        else:
            x = (win.winfo_screenwidth() - w) // 2
            y = (win.winfo_screenheight() - h) // 3
        win.geometry(f"+{max(0, x)}+{max(0, y)}")

    def _caps(self, text, top, link=None):
        s = self.scale
        f = tk.Frame(self.list, bg=self.t["bg"])
        f.pack(fill="x", padx=int(12 * s), pady=(int(top * s), int(2 * s)))
        tk.Label(f, text=text.upper(), font=self.F["caps"], bg=self.t["bg"], fg=self.t["faint"]).pack(side="left")
        if link:
            Btn(f, self, link[0], link[1], kind="link", font=self.F["small"]).pack(side="right")

    def refresh(self):
        t, s, F = self.t, self.scale, self.F
        for w in self.list.winfo_children():
            w.destroy()
        self.next_labels = []
        L, A = self.cfg["lembretes"], self.cfg["avulsos"]
        if not L and not A:
            tk.Label(self.list, text="Nenhum lembrete por enquanto.", font=F["row"], bg=t["bg"],
                     fg=t["sub"]).pack(pady=(int(60 * s), int(4 * s)))
            tk.Label(self.list, text="Crie o primeiro no botão abaixo, ou use Ctrl + Alt + L.",
                     font=F["sub"], bg=t["bg"], fg=t["faint"]).pack()
        nt = self.notion_today()
        sections = 0
        if A:
            self._caps("Rápidos", 0)
            sections += 1
            for i, a in enumerate(A):
                self._row_avulso(a, first=(i == 0))
        if nt is not None:
            feitos = sum(1 for a in nt if a["done"])
            cap = f"Hoje no Notion  ·  {feitos} de {len(nt)}"
            if self.notion_err:
                cap += f"  ·  {self.notion_err}"
            elif not self.notion_acts:
                cap += "  ·  sincronizando…"
            self._caps(cap, 22 if sections else 0, link=("+  Nova tarefa", self.open_task))
            sections += 1
            for i, a in enumerate(nt):
                self._row_notion(a, first=(i == 0))
        if L and sections:
            self._caps("Recorrentes", 22)
        for i, l in enumerate(L):
            self._row(l, first=(i == 0))
        self.refresh_times()

    def _row_notion(self, a, first):
        t, s, F = self.t, self.scale, self.F
        if not first:
            tk.Frame(self.list, bg=t["line"], height=1).pack(fill="x", padx=int(12 * s))
        row = tk.Frame(self.list, bg=t["bg"], cursor="hand2")
        row.pack(fill="x")
        inner = tk.Frame(row, bg=t["bg"])
        inner.pack(fill="x", padx=int(12 * s), pady=int(11 * s))
        d = int(18 * s)
        cor = a["cor"]
        if a["done"]:
            chk = tk.Label(inner, image=circle_photo(d, fill=cor), text=G_CHECK, compound="center",
                           font=F["icon_s"], fg="#ffffff", bg=t["bg"], bd=0)
        else:
            chk = tk.Label(inner, image=circle_photo(d, outline=t["faint"], width=1.3 * s), bg=t["bg"], bd=0)
        chk.pack(side="left", padx=(0, int(12 * s)))
        hora = fmt_hm(a["hora"]) + "  ·  " if a["hora"] is not None else ""
        extra = hora + a["info"]
        tk.Label(inner, text=extra, font=F["small"], bg=t["bg"],
                 fg=t["danger"] if a.get("atrasada") else t["faint"]).pack(side="right")
        tk.Label(inner, text=a["titulo"], font=F["done"] if a["done"] else F["body"], bg=t["bg"],
                 fg=t["faint"] if a["done"] else t["fg"], anchor="w").pack(side="left", fill="x", expand=True)
        self._hover_row(row, click=lambda e, a=a: self.set_notion_done(a["id"]))

    def _hover_row(self, row, click=None):
        t = self.t

        def paint(color):
            stack = [row]
            while stack:
                w = stack.pop()
                try:
                    w.configure(bg=color)
                except tk.TclError:
                    pass
                stack.extend(w.winfo_children())

        bind_tree(row, "<Enter>", lambda e: paint(t["row_hover"]))
        bind_tree(row, "<Leave>", lambda e: row.after(
            10, lambda: row.winfo_exists() and not pointer_inside(row) and paint(t["bg"])))
        if click:
            bind_tree(row, "<Button-1>", click)

    def _row(self, l, first):
        t, s, F = self.t, self.scale, self.F
        on = bool(l.get("ativo"))
        meta = int(l.get("meta") or 0)
        n = self.count_today(l)
        if not first:
            tk.Frame(self.list, bg=t["line"], height=1).pack(fill="x", padx=int(12 * s))
        row = tk.Frame(self.list, bg=t["bg"], cursor="hand2")
        row.pack(fill="x")
        inner = tk.Frame(row, bg=t["bg"])
        inner.pack(fill="x", padx=int(12 * s), pady=int(16 * s))

        tk.Label(inner, image=self.dot(l.get("cor") or PALETTE[0], on), bg=t["bg"], bd=0).pack(
            side="left", padx=(0, int(16 * s)))
        tog = Toggle(inner, self, on, command=lambda v, l=l: self.set_active(l, v))
        tog.pack(side="right", padx=(int(18 * s), 0))
        nxt = tk.Label(inner, text="", font=F["time"], bg=t["bg"], fg=t["fg"] if on else t["faint"])
        nxt.pack(side="right")
        self.next_labels.append((nxt, l))
        txt = tk.Frame(inner, bg=t["bg"])
        txt.pack(side="left", fill="x", expand=True)
        tk.Label(txt, text=l["titulo"], font=F["row"], bg=t["bg"], fg=t["fg"] if on else t["faint"],
                 anchor="w").pack(fill="x")
        extra = f"  ·  {n} de {meta} hoje" if meta else (f"  ·  {n}× hoje" if n else "")
        tk.Label(txt, text=recorrencia_texto(l) + extra, font=F["sub"], bg=t["bg"],
                 fg=t["sub"] if on else t["faint"], anchor="w").pack(fill="x", pady=(int(2 * s), 0))
        if meta and on:
            bw, bh = int(140 * s), max(2, int(2 * s))
            bar = tk.Canvas(txt, width=bw, height=bh, bg=t["bg"], highlightthickness=0, bd=0)
            bar.pack(anchor="w", pady=(int(8 * s), 0))
            bar.create_rectangle(0, 0, bw, bh, width=0, fill=t["line"])
            bar.create_rectangle(0, 0, bw * min(1.0, n / meta), bh, width=0, fill=l.get("cor") or PALETTE[0])
        self._hover_row(row, click=lambda e, l=l: self.edit(l))

    def _row_avulso(self, a, first):
        t, s, F = self.t, self.scale, self.F
        if not first:
            tk.Frame(self.list, bg=t["line"], height=1).pack(fill="x", padx=int(12 * s))
        row = tk.Frame(self.list, bg=t["bg"])
        row.pack(fill="x")
        inner = tk.Frame(row, bg=t["bg"])
        inner.pack(fill="x", padx=int(12 * s), pady=int(14 * s))
        tk.Label(inner, image=self.dot(a.get("cor") or PALETTE[0]), bg=t["bg"], bd=0).pack(
            side="left", padx=(0, int(16 * s)))
        IconBtn(inner, self, G_CLOSE, lambda a=a: self.remove_avulso(a), size=30,
                font=self.F["icon_s"]).pack(side="right", padx=(int(14 * s), 0))
        when = dt.datetime.fromisoformat(a["quando"])
        tk.Label(inner, text=fmt_when(when, dt.datetime.now()), font=F["time"], bg=t["bg"],
                 fg=t["fg"]).pack(side="right")
        tk.Label(inner, text=a["titulo"], font=F["row"], bg=t["bg"], fg=t["fg"], anchor="w").pack(
            side="left", fill="x", expand=True)
        self._hover_row(row)

    def hold_reason(self):
        st = self.st
        if self.pomo.phase == "foco" and not self.pomo.paused and st["pomo_segurar"]:
            return "Em foco"
        if st["foco_chamadas"] and self.mic_busy():
            return "Em chamada"
        if st["foco_tela_cheia"] and fullscreen_busy():
            return "Tela cheia"
        return None

    def mic_busy(self):
        now = dt.datetime.now().timestamp()
        if now - self._mic[0] > 3:
            try:
                self._mic = (now, mic_in_use())
            except Exception:
                self._mic = (now, False)
        return self._mic[1]

    def refresh_times(self):
        now = dt.datetime.now()
        nxt = None
        for lbl, l in getattr(self, "next_labels", []):
            on = l.get("ativo")
            done = self.goal_reached(l)
            when = next_occurrence(l, now) if on else None
            if done and when and when.date() == now.date():
                when = None
                for d in range(1, 8):
                    date = now.date() + dt.timedelta(days=d)
                    occ = occurrences(l, date)
                    if occ:
                        when = dt.datetime.combine(date, dt.time(occ[0] // 60, occ[0] % 60))
                        break
            if when and (nxt is None or when < nxt[0]):
                nxt = (when, l)
            if lbl.winfo_exists():
                if not on:
                    lbl.configure(text="—")
                elif done:
                    lbl.configure(text="meta ✓", fg=self.t["sub"])
                else:
                    lbl.configure(text=fmt_when(when, now))
        for a in self.cfg["avulsos"]:
            when = dt.datetime.fromisoformat(a["quando"])
            if nxt is None or when < nxt[0]:
                nxt = (when, a)
        reason = self.hold_reason()
        if self.is_paused():
            txt = f"Pausado até {self.paused_until:%H:%M}"
        elif reason:
            txt = f"{reason}  ·  lembretes guardados para depois"
        elif nxt:
            txt = f"Próximo  ·  {nxt[1]['titulo']}, {fmt_when(nxt[0], now, 'hoje às').replace('amanhã ', 'amanhã às ')}"
        else:
            txt = "Nada agendado"
        self.lbl_status.configure(text=txt)
        ativos = sum(1 for l in self.cfg["lembretes"] if l.get("ativo"))
        n = len(self.cfg["lembretes"])
        pomos = self.cfg["historico"].get(dt.date.today().isoformat(), {}).get("_pomo", 0)
        partes = [f"{ativos} de {n} ativos"] if n else []
        if pomos:
            partes.append(f"{pomos} pomodoro{'s' if pomos > 1 else ''} hoje")
        self.lbl_count.configure(text="   ·   ".join(partes))
        self.btn_pause.configure(text=G_PLAY if self.is_paused() else G_PAUSE)
        self.update_tray(txt)

    def update_tray(self, txt=None):
        if not self.tray:
            return
        if self.pomo.running:
            m = int(self.pomo.remaining() // 60) + 1
            txt = f"{self.pomo.label().replace('  ·  ', ' ')} — {m} min"
        if txt:
            self.tray.title = f"{APP} — {txt}"[:120]
        try:
            self.tray.update_menu()
        except Exception:
            pass

    def refresh_pomo(self):
        if not hasattr(self, "band"):
            return
        p = self.pomo
        if not p.running:
            if self.band.winfo_manager():
                self.band.pack_forget()
            return
        if not self.band.winfo_manager():
            self.band.pack(fill="x", padx=self.P, pady=(int(20 * self.scale), 0), after=self.head)
        rem = int(math.ceil(p.remaining()))
        pomos = self.cfg["historico"].get(dt.date.today().isoformat(), {}).get("_pomo", 0)
        lbl = p.label() + ("  ·  pausado" if p.paused else "") + (f"  ·  {pomos} hoje" if pomos else "")
        self.lbl_phase.configure(text=lbl.upper())
        self.lbl_clock.configure(text=f"{rem // 60:02d}:{rem % 60:02d}",
                                 fg=self.t["sub"] if p.paused else self.t["fg"])
        self.btn_pp.configure(text=G_PLAY if p.paused else G_PAUSE)
        w = self.pomo_bar.winfo_width()
        frac = 1 - rem / max(1, p.total)
        self.pomo_bar.coords(self.pomo_fill, 0, 0, w * frac, int(2 * self.scale) + 1)
        self.pomo_bar.itemconfigure(self.pomo_fill, fill=POMO_FOCO if p.phase == "foco" else POMO_PAUSA)

    def on_pomo_change(self):
        self.refresh_pomo()
        self.refresh_times()

    def pomo_loop(self):
        self.pomo.tick()
        if self.pomo.running:
            self.refresh_pomo()
        self.root.after(1000 - dt.datetime.now().microsecond // 1000 % 1000 + 5, self.pomo_loop)

    # ── Notion ──
    def notion_configured(self):
        return bool(self.st.get("notion_token") and self.st.get("notion_db"))

    def notion_loop(self):
        self.sync_notion()
        self.root.after(10 * 60 * 1000, self.notion_loop)

    def sync_notion(self):
        if not self.notion_configured() or self._syncing:
            return
        self._syncing = True
        token, link = unprotect(self.st["notion_token"]), self.st["notion_db"]

        def run():
            try:
                res = notion_fetch(token, link)
                self.q.put(("notion_ok", (notion_parse(res), res["info"])))
            except NotionError as e:
                self.q.put(("notion_err", str(e)))
            except Exception as e:
                self.q.put(("notion_err", f"Erro ao ler o Notion: {e}"))
        threading.Thread(target=run, daemon=True).start()

    def _notion_result(self, kind, data):
        self._syncing = False
        if kind == "notion_ok":
            (self.notion_acts, self.notion_info), self.notion_err = data, None
            self.notion_at = dt.datetime.now()
            self._save_notion_cache()
        else:
            self.notion_err = data
        self.refresh()
        self.island.refresh()

    def _save_notion_cache(self):
        try:
            with open(os.path.join(DATA_DIR, "notion_cache.json"), "w", encoding="utf-8") as f:
                json.dump({"acts": self.notion_acts, "info": self.notion_info}, f, ensure_ascii=False)
        except OSError:
            pass

    def create_notion_task(self, titulo, when, timed, prio, setor, result):
        """Cria a tarefa em segundo plano; 'result' recebe ok/err."""
        token, info = unprotect(self.st["notion_token"]), dict(self.notion_info)

        def run():
            try:
                page = notion_create(token, info, titulo, when, timed, prio, setor)
                result["ok"] = notion_parse({"pages": [page], "info": info})
            except NotionError as e:
                result["err"] = str(e)
            except Exception as e:
                result["err"] = f"Erro: {e}"
        threading.Thread(target=run, daemon=True).start()

    def task_created(self, acts):
        self.notion_acts.extend(acts)
        self._save_notion_cache()
        self.refresh()
        self.island.refresh()

    def open_task(self):
        if not self.notion_configured():
            SettingsDialog(self, "notion")
            return
        if self.quick:
            try:
                self.quick.close()
            except tk.TclError:
                pass
        self.quick = QuickAdd(self, mode="notion")

    def period_start(self, key):
        try:
            return parse_hm(self.st["notion_horarios"].get(key, ""))
        except ValueError:
            return None

    def current_period(self):
        now = dt.datetime.now()
        nowm = now.hour * 60 + now.minute
        cur = PERIODS[0][0]
        for key, _ in PERIODS:
            m = self.period_start(key)
            if m is not None and m <= nowm:
                cur = key
        return cur

    def notion_today(self):
        """Atividades de hoje (None se o Notion não estiver configurado)."""
        if not self.notion_configured():
            return None
        today = dt.date.today()
        hist = self.cfg["historico"].get(today.isoformat(), {})
        labels = dict(PERIODS)
        order = {k: i for i, (k, _) in enumerate(PERIODS)}
        out = []
        if not any(a.get("dias") for a in self.notion_acts):
            return self._notion_tasks_today(today, hist)
        for a in self.notion_acts:
            if not notion_applies(a, today):
                continue
            per = notion_period(a)
            hora = None
            d, timed = _parse_iso(a.get("data") or "")
            if d and timed and d.date() == today:
                hora = d.hour * 60 + d.minute
            start = hora if hora is not None else (self.period_start(per) if per else None)
            info = labels.get(per, "Sem período") + (f"  ·  {int(a['tempo'])} min" if a.get("tempo") else "")
            out.append({**a, "periodo": per, "info": info, "cor": PERIOD_COLORS.get(per, PALETTE[-1]),
                        "hora": hora, "done": bool(hist.get("n:" + a["id"])),
                        "ordem": (order.get(per, 9), start if start is not None else 9999, a["titulo"])})
        out.sort(key=lambda a: a["ordem"])
        return out

    PRIO = {"alta": (0, "#d49a9a"), "media": (1, "#d6b98c"), "baixa": (2, "#9bb59a"), "muito baixa": (3, "#8ea4d2")}

    def _notion_tasks_today(self, today, hist):
        """Banco de tarefas: com data até hoje e não concluídas (atrasadas incluídas)."""
        out = []
        for a in self.notion_acts:
            d, timed = _parse_iso(a.get("data") or "")
            if not d or d.date() > today:
                continue
            key = "n:" + a["id"]
            done = bool(hist[key]) if key in hist else bool(a.get("done"))
            atrasada = d.date() < today
            if atrasada and done:
                continue
            rank, cor = self.PRIO.get(_norm(a.get("prioridade")), (4, PALETTE[-1]))
            partes = (["atrasada"] if atrasada else []) + [x for x in (a.get("prioridade"), a.get("setor")) if x]
            hora = d.hour * 60 + d.minute if timed and not atrasada else None
            out.append({**a, "periodo": "", "info": "  ·  ".join(partes), "cor": cor, "hora": hora,
                        "done": done, "atrasada": atrasada,
                        "ordem": (rank, hora if hora is not None else 9999, d.isoformat(), a["titulo"])})
        out.sort(key=lambda a: a["ordem"])
        return out

    def set_notion_done(self, pid, value=None):
        h = self.today_hist()
        key = "n:" + pid
        act = next((a for a in self.notion_acts if a["id"] == pid), None)
        if value is None:
            cur = bool(h[key]) if key in h else bool(act and act.get("done"))
            value = not cur
        h[key] = 1 if value else 0
        if act and act.get("done_prop"):
            act["done"] = bool(value)
            token = unprotect(self.st.get("notion_token", ""))

            def run():
                try:
                    notion_set_done(token, act, value)
                except NotionError as e:
                    self.q.put(("notion_err", f"Não consegui marcar no Notion: {e}"))
            threading.Thread(target=run, daemon=True).start()
        self.save()

    def island_status(self):
        if self.pomo.running:
            rem = int(self.pomo.remaining())
            cor = POMO_FOCO if self.pomo.phase == "foco" else POMO_PAUSA
            return (f"{self.pomo.label().split('  ·  ')[0]}  {rem // 60:02d}:{rem % 60:02d}", cor)
        if self.is_paused():
            return (f"Pausado até {self.paused_until:%H:%M}", None)
        reason = self.hold_reason()
        if reason:
            return (reason, None)
        return None

    def upcoming(self, n=3):
        now = dt.datetime.now()
        items = []
        for l in self.cfg["lembretes"]:
            if l.get("ativo") and not self.goal_reached(l):
                w = next_occurrence(l, now)
                if w:
                    items.append((w, l["titulo"], l.get("cor") or PALETTE[0]))
        for a in self.cfg["avulsos"]:
            items.append((dt.datetime.fromisoformat(a["quando"]), a["titulo"], a.get("cor") or PALETTE[0]))
        for a in self.notion_today() or []:
            if a["hora"] is not None and not a["done"]:
                w = dt.datetime.combine(now.date(), dt.time(a["hora"] // 60, a["hora"] % 60))
                if w > now:
                    items.append((w, a["titulo"], a["cor"]))
        items.sort(key=lambda x: x[0])
        return items[:n]

    # ── dados ──
    def today_hist(self):
        return self.cfg["historico"].setdefault(dt.date.today().isoformat(), {})

    def count_today(self, l):
        return self.cfg["historico"].get(dt.date.today().isoformat(), {}).get(l.get("id"), 0)

    def goal_reached(self, l):
        meta = int(l.get("meta") or 0)
        return meta > 0 and self.count_today(l) >= meta

    def mark_done(self, l):
        if l.get("_notion"):
            self.set_notion_done(l["_notion"], True)
            return
        if l.get("_sistema") or l.get("_avulso") or not l.get("id"):
            return
        h = self.today_hist()
        h[l["id"]] = h.get(l["id"], 0) + 1
        self.save()

    def log_pomo(self):
        h = self.today_hist()
        h["_pomo"] = h.get("_pomo", 0) + 1
        self.save()

    def add_avulso(self, titulo, when):
        self.cfg["avulsos"].append({"id": uuid.uuid4().hex[:8], "titulo": titulo,
                                    "quando": when.isoformat(timespec="seconds"), "cor": PALETTE[0]})
        self.cfg["avulsos"].sort(key=lambda a: a["quando"])
        self.save()

    def remove_avulso(self, a):
        if a in self.cfg["avulsos"]:
            self.cfg["avulsos"].remove(a)
            self.save()

    def open_quick(self):
        if self.quick:
            try:
                if self.quick.mode == "lembrete":
                    self.quick.focus()
                    return
                self.quick.close()
            except tk.TclError:
                pass
            self.quick = None
        self.quick = QuickAdd(self)

    def set_active(self, l, v):
        l["ativo"] = v
        self.save()

    def new(self):
        ed = Editor(self)
        self.root.wait_window(ed)
        if ed.result:
            self.cfg["lembretes"].append(ed.result[1])
            self.save()

    def edit(self, l):
        ed = Editor(self, l)
        self.root.wait_window(ed)
        if not ed.result or l not in self.cfg["lembretes"]:
            return
        action, data = ed.result
        if action == "delete":
            self.cfg["lembretes"].remove(l)
        else:
            self.cfg["lembretes"][self.cfg["lembretes"].index(l)] = data
        self.save()

    def save(self):
        save_cfg(self.cfg)
        self.refresh()

    # ── pop-ups e agenda ──
    def layout_popups(self):
        l, tp, r, b = work_area()
        gap = int(14 * self.scale)
        step = int(10 * self.scale)
        if self.st["posicao"] == "superior":
            y = tp + gap
            for p in self.popups:
                p.move_to(r - p.W - gap, y, -1)
                y += p.h + step
        else:
            y = b - gap
            for p in self.popups:
                y -= p.h
                p.move_to(r - p.W - gap, y, 1)
                y -= step

    def snooze(self, lemb):
        when = dt.datetime.now() + dt.timedelta(minutes=int(self.st["adiar_min"]))
        self.snoozes.append((when, lemb))

    def is_paused(self):
        return bool(self.paused_until and self.paused_until > dt.datetime.now())

    def toggle_pause(self):
        self.paused_until = None if self.is_paused() else dt.datetime.now() + dt.timedelta(hours=1)
        self.refresh_times()

    def deliver(self, l):
        """Mostra agora, ou guarda para o resumo (ausente, em chamada, tela cheia, foco)."""
        if idle_seconds() >= AWAY_SEC or self.hold_reason():
            self.add_missed(l)
        else:
            Popup(self, l)

    def add_missed(self, l):
        key = l.get("id") or l.get("titulo")
        entry = self.missed.setdefault(key, [l, 0])
        entry[0] = l
        entry[1] += 1

    def watch_return(self):
        """Quando a pessoa volta (ou a chamada/foco termina), mostra o que ficou para trás."""
        if self.missed and idle_seconds() < 3 and not self.is_paused() and not self.hold_reason():
            items = list(self.missed.values())
            self.missed = {}
            if len(items) == 1 and items[0][1] == 1:
                Popup(self, items[0][0])
            else:
                linhas = [f"{l['titulo']}  ·  {n}×" if n > 1 else l["titulo"] for l, n in items]
                Popup(self, {"titulo": "Enquanto você estava ocupado", "mensagem": "\n".join(linhas),
                             "cor": PALETTE[-1], "_sistema": True})
        self.root.after(2000, self.watch_return)

    def tick(self):
        now = dt.datetime.now()
        today = now.date()
        if today != self.fired_date:
            self.fired, self.fired_date = set(), today
        nowm = now.hour * 60 + now.minute
        paused = self.is_paused()
        if not paused:
            for l in self.cfg["lembretes"]:
                if not l.get("ativo"):
                    continue
                for m in occurrences(l, today):
                    key = (l["id"], m)
                    if m > nowm or key in self.fired:
                        continue
                    self.fired.add(key)
                    if self.goal_reached(l):
                        continue                 # meta do dia batida: descansa
                    when = dt.datetime.combine(today, dt.time(m // 60, m % 60))
                    if nowm < m + GRACE_MIN:
                        self.deliver(l)
                    elif when > self.last_check:
                        self.add_missed(l)      # PC estava em suspensão nesse horário
            due = [x for x in self.snoozes if x[0] <= now]
            self.snoozes = [x for x in self.snoozes if x[0] > now]
            for _, l in due:
                self.deliver(l)
        # Notion: aviso no início de cada período e atividades com horário
        if not paused and self.notion_configured() and self.notion_acts:
            acts = self.notion_today()
            if self.st.get("notion_avisar", True):
                for key, label in PERIODS:
                    m = self.period_start(key)
                    fk = ("np", key)
                    if m is None or m > nowm or fk in self.fired:
                        continue
                    self.fired.add(fk)
                    items = [a for a in acts if not a["done"] and a["hora"] is None and
                             (a["periodo"] == key or (not a["periodo"] and key == "manha"))]
                    if items and nowm < m + GRACE_MIN:
                        linhas = [_trunc(a["titulo"], 40) for a in items[:5]]
                        if len(items) > 5:
                            linhas.append(f"+ {len(items) - 5} mais")
                        n = len(items)
                        self.deliver({"id": "np-" + key, "titulo": f"{label}  ·  {n} atividade{'s' if n > 1 else ''}",
                                      "mensagem": "\n".join(linhas), "cor": PERIOD_COLORS[key], "_sistema": True})
            for a in acts:
                fk = ("nt", a["id"])
                if a["hora"] is None or a["done"] or a["hora"] > nowm or fk in self.fired:
                    continue
                self.fired.add(fk)
                if nowm < a["hora"] + GRACE_MIN:
                    self.deliver({"id": "nt-" + a["id"], "titulo": a["titulo"], "mensagem": "Do Notion",
                                  "cor": a["cor"], "_notion": a["id"]})
        # lembretes rápidos: nunca se perdem
        changed = False
        for a in list(self.cfg["avulsos"]):
            when = dt.datetime.fromisoformat(a["quando"])
            if when <= now:
                self.cfg["avulsos"].remove(a)
                changed = True
                lemb = {"id": "q" + a["id"], "titulo": a["titulo"], "mensagem": "",
                        "cor": a.get("cor") or PALETTE[0], "_avulso": True}
                if paused or now - when > dt.timedelta(minutes=GRACE_MIN):
                    self.add_missed(lemb)
                else:
                    self.deliver(lemb)
        if changed:
            self.save()
        self.last_check = now
        if self.st["tema"] == "auto" and self.theme_name() != self.theme_cur:
            self.apply_theme()
        self.refresh_times()
        self.root.after(15000 - (now.second * 1000 + now.microsecond // 1000) % 15000, self.tick)

    # ── bandeja / janela ──
    def _start_tray(self):
        try:
            import pystray
        except ImportError:
            return False
        M = pystray.MenuItem
        menu = pystray.Menu(
            M("Abrir", lambda: self.q.put("show"), default=True),
            M("Lembrete rápido    Ctrl+Alt+L", lambda: self.q.put("quick")),
            M("Nova tarefa no Notion    Ctrl+Alt+N", lambda: self.q.put("task"),
              visible=lambda i: self.notion_configured()),
            M(lambda i: "Parar pomodoro" if self.pomo.running else "Iniciar pomodoro    Ctrl+Alt+P",
              lambda: self.q.put("pomo")),
            M(lambda i: "Retomar lembretes" if self.is_paused() else "Pausar por 1 hora",
              lambda: self.q.put("pause")),
            M("Chamar os olhinhos", lambda: self.q.put("peek")),
            pystray.Menu.SEPARATOR,
            M("Sair", lambda: self.q.put("quit")),
        )
        self.tray = pystray.Icon("blink", make_icon_image(64), APP, menu)
        self.tray.run_detached()
        return True

    def poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                if isinstance(msg, tuple):
                    self._notion_result(*msg)
                elif msg == "show":
                    self.show()
                elif msg == "pause":
                    self.toggle_pause()
                elif msg == "quick":
                    self.open_quick()
                elif msg == "peek":
                    if self.island.state == "hidden":
                        self.peek.show()
                elif msg == "task":
                    self.island.close()
                    self.open_task()
                elif msg == "pomo":
                    self.pomo.toggle()
                elif msg == "test":
                    Popup(self, {"titulo": "Lembrete de teste", "mensagem": "Tudo funcionando.",
                                 "cor": PALETTE[0]})
                elif msg == "quit":
                    self.quit()
                    return
        except queue.Empty:
            pass
        self.root.after(150, self.poll)

    def show(self):
        self.root.deiconify()
        style_window(self.root, self.t)
        self.root.lift()
        self.root.focus_force()
        self.refresh_pomo()

    def hide(self):
        if self.has_tray:
            self.root.withdraw()
        else:
            self.root.iconify()

    def quit(self):
        if self.hotkeys:
            self.hotkeys.stop()
        if self.tray:
            self.tray.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


# ───────────────────────── instância única ─────────────────────────

def signal_existing():
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=0.5) as s:
            s.sendall(b"show")
        return True
    except OSError:
        return False


def serve_single_instance(app):
    try:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", PORT))
        srv.listen(2)
    except OSError:
        return

    def loop():
        while True:
            try:
                c, _ = srv.accept()
                with c:
                    if c.recv(16) == b"show":
                        app.q.put("show")
            except OSError:
                return
    threading.Thread(target=loop, daemon=True).start()


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("blink.app")
    except Exception:
        pass
    if signal_existing():
        return
    app = App(start_hidden="--min" in sys.argv)
    serve_single_instance(app)
    app.run()


if __name__ == "__main__":
    main()
