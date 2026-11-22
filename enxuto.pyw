# -*- coding: utf-8 -*-
"""
Enxuto
Editor simples para cortar e comprimir vídeos, com interface inspirada no Avidemux.
Usa o FFmpeg (embutido no instalador) para todo o processamento.
"""
import bisect
import glob
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time


def _bootstrap_runtime():
    """No Windows, aponta o Tcl/Tk e as DLLs para o Python que vem junto com o programa."""
    if os.name != "nt":
        return
    base = os.path.dirname(sys.executable)
    tcl = os.path.join(base, "tcl", "tcl8.6")
    tkd = os.path.join(base, "tcl", "tk8.6")
    if os.path.isdir(tcl):
        os.environ["TCL_LIBRARY"] = tcl
    if os.path.isdir(tkd):
        os.environ["TK_LIBRARY"] = tkd
    for d in (base, os.path.join(base, "DLLs")):
        try:
            os.add_dll_directory(d)
        except (OSError, AttributeError):
            pass
    try:  # ícone e agrupamento próprios na barra de tarefas
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Heb.Enxuto")
    except Exception:  # noqa: BLE001
        pass


_bootstrap_runtime()

import tkinter as tk  # noqa: E402
from tkinter import ttk, filedialog, messagebox  # noqa: E402

APP_NAME = "Enxuto"
VERSION = "1.2.1"
IS_WIN = os.name == "nt"
NO_WINDOW = 0x08000000 if IS_WIN else 0  # CREATE_NO_WINDOW: sem janelas de console piscando

VIDEO_OUT = {
    "H.264 (mais compatível)": "libx264",
    "H.265 (arquivo menor)": "libx265",
    "Copiar (sem recodificar)": "copy",
}
MODES = {
    "Tamanho máximo (MB)": "mb",
    "% do tamanho original": "pct",
    "Qualidade constante (CRF)": "crf",
    "Bitrate máximo": "maxrate",
}
AUDIO_OUT = {
    "AAC 128 kbps": 128,
    "AAC 160 kbps": 160,
    "AAC 96 kbps": 96,
    "AAC 64 kbps": 64,
    "Copiar": "copy",
    "Sem áudio": 0,
}
RESOLUTIONS = {
    "Original": None,
    "2160p (4K)": 2160,
    "1440p": 1440,
    "1080p": 1080,
    "720p": 720,
    "480p": 480,
    "360p": 360,
}
ASPECTS = {
    "Manter original": "keep",
    "Esticar (16:9)": "stretch",
    "Cortar bordas (16:9)": "crop",
}
PRESETS = {
    "Rápida": "faster",
    "Equilibrada": "medium",
    "Lenta (comprime melhor)": "slow",
    "Muito rápida": "veryfast",
}
FORMATS = {"MP4": ".mp4", "MKV": ".mkv"}
VIDEO_TYPES = "*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.flv *.wmv *.ts *.mts *.m2ts *.3gp *.mpg *.mpeg"


# --------------------------------------------------------------------------- tema

LIGHT = dict(bg="#f0f0f0", field="#ffffff", fg="#1a1a1a", muted="#666666", border="#b4b4b4",
             hover="#e1e1e1", press="#cfcfcf", accent="#2f6fd6", accent_hover="#3f7fe6", warn="#b05a00",
             sel="#2f6fd6", track="#cacaca", track_line="#9a9a9a", range="#8bb8ff", range_line="#2f6fd6",
             head="#d02020", tip_bg="#ffffe1", tip_fg="#000000", menubar="#e9e9e9",
             cut="#d9534f", fast="#f0ad4e")
DARK = dict(bg="#1e1f22", field="#2b2d31", fg="#e6e6e6", muted="#9aa0a6", border="#43474e",
            hover="#35383d", press="#3f4349", accent="#3d7be0", accent_hover="#5590f0", warn="#f0a35e",
            sel="#2f6fd6", track="#3a3d42", track_line="#55595f", range="#2c5596", range_line="#6ea2ff",
            head="#ff5555", tip_bg="#2b2d31", tip_fg="#e6e6e6", menubar="#26282b",
            cut="#c0504d", fast="#e0a040")
PALETTE = dict(LIGHT)
THEMES = {"Seguir o Windows": "system", "Claro": "light", "Escuro": "dark"}
SPEEDS = {"1,5×": 1.5, "2×": 2.0, "3×": 3.0, "4×": 4.0, "8×": 8.0, "16×": 16.0}

# (id, descrição, atalho padrão)
ACTIONS = [
    ("play", "Tocar / pausar", "<Key-space>"),
    ("mute", "Ligar / desligar o som da prévia", "<Key-m>"),
    ("prev_frame", "Quadro anterior", "<Key-Left>"),
    ("next_frame", "Próximo quadro", "<Key-Right>"),
    ("back_1s", "Voltar 1 segundo", "<Shift-Key-Left>"),
    ("fwd_1s", "Avançar 1 segundo", "<Shift-Key-Right>"),
    ("back_10s", "Voltar 10 segundos", "<Control-Key-Left>"),
    ("fwd_10s", "Avançar 10 segundos", "<Control-Key-Right>"),
    ("prev_key", "Keyframe anterior", "<Key-Down>"),
    ("next_key", "Próximo keyframe", "<Key-Up>"),
    ("first", "Ir para o início", "<Key-Home>"),
    ("last", "Ir para o fim", "<Key-End>"),
    ("set_a", "Marcar A (início da seleção)", "<Key-bracketleft>"),
    ("set_b", "Marcar B (fim da seleção)", "<Key-bracketright>"),
    ("goto_a", "Ir para A", ""),
    ("goto_b", "Ir para B", ""),
    ("clear_sel", "Limpar seleção", ""),
    ("cut", "Remover o trecho A–B", "<Key-Delete>"),
    ("speed", "Acelerar o trecho A–B", "<Key-r>"),
    ("undo", "Desfazer", "<Control-Key-z>"),
    ("open", "Abrir vídeo", "<Control-Key-o>"),
    ("save", "Salvar vídeo", "<Control-Key-s>"),
]
DEFAULT_KEYS = {a: d for a, _, d in ACTIONS}
KEY_NAMES = {
    "space": "Espaço", "Left": "←", "Right": "→", "Up": "↑", "Down": "↓", "bracketleft": "[",
    "bracketright": "]", "braceleft": "{", "braceright": "}", "Prior": "PgUp", "Next": "PgDn",
    "Return": "Enter", "BackSpace": "Backspace", "Escape": "Esc", "comma": ",", "period": ".",
    "minus": "-", "plus": "+", "equal": "=", "slash": "/", "backslash": "\\", "semicolon": ";",
    "apostrophe": "'", "grave": "`", "less": "<", "greater": ">", "question": "?", "colon": ":",
    "underscore": "_", "asterisk": "*", "KP_Add": "+ (teclado numérico)", "KP_Subtract": "- (teclado numérico)",
}
MOD_LABELS = {"Control": "Ctrl", "Alt": "Alt", "Shift": "Shift"}
MODIFIER_KEYS = {"Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R", "Meta_L", "Meta_R",
                 "Super_L", "Super_R", "Caps_Lock", "Win_L", "Win_R", "ISO_Level3_Shift", "Num_Lock"}


def seq_parts(seq):
    parts = seq.strip("<>").split("-")
    key = parts[-1]
    mods = [m for m in parts[:-1] if m in MOD_LABELS]
    return mods, key


def seq_label(seq):
    if not seq:
        return "—"
    mods, key = seq_parts(seq)
    name = KEY_NAMES.get(key, key.upper() if len(key) == 1 else key)
    return "+".join([MOD_LABELS[m] for m in mods] + [name])


def seq_variants(seq):
    """Sequências do Tk para registrar (letras: maiúscula e minúscula, por causa do Caps Lock)."""
    mods, key = seq_parts(seq)
    if len(key) == 1 and key.isalpha():
        keys = [key.upper()] if "Shift" in mods else [key.lower(), key.upper()]
    else:
        keys = [key]
    return ["<" + "-".join(mods + ["Key", k]) + ">" for k in keys]


def event_to_seq(e):
    ks = e.keysym
    if ks in MODIFIER_KEYS or not ks:
        return None
    mods = []
    if e.state & 0x4:
        mods.append("Control")
    if e.state & (0x20000 if IS_WIN else 0x8):
        mods.append("Alt")
    if e.state & 0x1:
        mods.append("Shift")
    if len(ks) == 1 and ks.isalpha():
        ks = ks.lower()
    return "<" + "-".join(mods + ["Key", ks]) + ">"


def config_path():
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "Enxuto", "config.json")


def load_settings():
    try:
        with open(config_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(data):
    try:
        os.makedirs(os.path.dirname(config_path()), exist_ok=True)
        with open(config_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def windows_prefers_dark():
    if not IS_WIN:
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


def set_titlebar_dark(win, dark):
    """Barra de título escura no Windows 10 (20H1+) e 11."""
    if not IS_WIN:
        return
    try:
        import ctypes
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        val = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), ctypes.sizeof(val)) == 0:
                break
    except Exception:  # noqa: BLE001
        pass


def style_widgets(st, P, base_font):
    """Aplica a paleta P em todos os estilos ttk (tema clam)."""
    st.theme_use("clam")
    st.configure(".", background=P["bg"], foreground=P["fg"], fieldbackground=P["field"],
                 bordercolor=P["border"], lightcolor=P["bg"], darkcolor=P["bg"], troughcolor=P["track"],
                 selectbackground=P["sel"], selectforeground="#ffffff", insertcolor=P["fg"],
                 arrowcolor=P["fg"], focuscolor=P["accent"], font=(base_font, 9))
    st.map(".", foreground=[("disabled", P["muted"])])
    st.configure("TButton", background=P["field"], bordercolor=P["border"], lightcolor=P["field"],
                 darkcolor=P["field"], padding=(8, 3))
    st.map("TButton", background=[("pressed", P["press"]), ("active", P["hover"]), ("disabled", P["bg"])],
           lightcolor=[("pressed", P["press"]), ("active", P["hover"])],
           darkcolor=[("pressed", P["press"]), ("active", P["hover"])],
           bordercolor=[("focus", P["accent"])])
    st.configure("Accent.TButton", background=P["accent"], foreground="#ffffff", lightcolor=P["accent"],
                 darkcolor=P["accent"], bordercolor=P["accent"], font=(base_font, 10, "bold"), padding=(10, 6))
    st.map("Accent.TButton", background=[("pressed", P["accent"]), ("active", P["accent_hover"])],
           lightcolor=[("active", P["accent_hover"])], darkcolor=[("active", P["accent_hover"])],
           foreground=[("disabled", "#dddddd")])
    st.configure("Transport.TButton", padding=(4, 2), font=(base_font, 11))
    for w in ("TEntry", "TSpinbox", "TCombobox"):
        st.configure(w, fieldbackground=P["field"], background=P["field"], foreground=P["fg"],
                     insertcolor=P["fg"], bordercolor=P["border"], lightcolor=P["field"], darkcolor=P["field"],
                     arrowcolor=P["fg"])
        st.map(w, fieldbackground=[("readonly", P["field"]), ("disabled", P["bg"])],
               foreground=[("disabled", P["muted"]), ("readonly", P["fg"])],
               bordercolor=[("focus", P["accent"])], lightcolor=[("focus", P["field"])],
               background=[("active", P["hover"]), ("pressed", P["press"])],
               selectbackground=[("readonly", P["field"])], selectforeground=[("readonly", P["fg"])],
               arrowcolor=[("disabled", P["muted"])])
    for w in ("TCheckbutton", "TRadiobutton"):
        st.configure(w, background=P["bg"], foreground=P["fg"], indicatorbackground=P["field"],
                     indicatorforeground=P["fg"], upperbordercolor=P["border"], lowerbordercolor=P["border"])
        st.map(w, background=[("active", P["bg"])],
               indicatorbackground=[("disabled", P["bg"]), ("pressed", P["hover"]), ("selected", P["field"]),
                                    ("!selected", P["field"])],
               indicatorforeground=[("disabled", P["muted"]), ("selected", P["fg"])])
    st.configure("TLabelframe", background=P["bg"], bordercolor=P["border"], lightcolor=P["bg"], darkcolor=P["bg"])
    st.configure("TLabelframe.Label", background=P["bg"], foreground=P["fg"])
    st.configure("TScale", background=P["field"], troughcolor=P["track"], bordercolor=P["border"],
                 lightcolor=P["field"], darkcolor=P["field"])
    st.map("TScale", background=[("active", P["hover"])])
    st.configure("TScrollbar", background=P["field"], troughcolor=P["bg"], bordercolor=P["bg"],
                 arrowcolor=P["fg"], lightcolor=P["field"], darkcolor=P["field"])
    st.map("TScrollbar", background=[("active", P["hover"])])
    st.configure("Horizontal.TProgressbar", background=P["accent"], troughcolor=P["track"],
                 bordercolor=P["border"], lightcolor=P["accent"], darkcolor=P["accent"])
    st.configure("TSeparator", background=P["border"])
    st.configure("Hint.TLabel", foreground=P["muted"], font=(base_font, 8))
    st.configure("Warn.TLabel", foreground=P["warn"])
    st.configure("Treeview", background=P["field"], fieldbackground=P["field"], foreground=P["fg"],
                 bordercolor=P["border"], rowheight=22)
    st.map("Treeview", background=[("selected", P["sel"])], foreground=[("selected", "#ffffff")])
    st.configure("Treeview.Heading", background=P["bg"], foreground=P["fg"], bordercolor=P["border"],
                 lightcolor=P["bg"], darkcolor=P["bg"])
    st.map("Treeview.Heading", background=[("active", P["hover"])])
    st.configure("Edit.TButton", padding=(6, 2), font=(base_font, 10))
    st.configure("Menubar.TFrame", background=P["menubar"])
    st.layout("Menubar.TMenubutton", [("Menubutton.border", {"sticky": "nswe", "children": [
        ("Menubutton.padding", {"sticky": "nswe", "children": [("Menubutton.label", {"sticky": "nswe"})]})]})])
    st.configure("Menubar.TMenubutton", background=P["menubar"], foreground=P["fg"], padding=(9, 3),
                 relief="flat", borderwidth=0, width=0)
    st.configure("Small.TButton", padding=(1, 2))
    st.map("Menubar.TMenubutton", background=[("active", P["hover"]), ("pressed", P["press"])])


# --------------------------------------------------------------------------- utilidades

def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def find_tool(name):
    exe = name + (".exe" if IS_WIN else "")
    d = app_dir()
    cands = [os.path.join(d, "ffmpeg", exe), os.path.join(d, "ffmpeg", "bin", exe), os.path.join(d, exe)]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        cands.insert(0, os.path.join(meipass, "ffmpeg", exe))
    for c in cands:
        if os.path.isfile(c):
            return c
    w = shutil.which(name)
    if w:
        return w
    la = os.environ.get("LOCALAPPDATA")
    if la:
        extra = [os.path.join(la, "Microsoft", "WinGet", "Links", exe)]
        extra += glob.glob(os.path.join(la, "Microsoft", "WinGet", "Packages", "*FFmpeg*", "*", "bin", exe))
        for c in extra:
            if os.path.isfile(c):
                return c
    return None


def fmt_time(s):
    ms = int(round(max(0.0, float(s or 0)) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    sec, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{sec:02d}.{ms:03d}"


def parse_time(txt):
    txt = txt.strip().replace(",", ".")
    parts = txt.split(":")
    if not txt or len(parts) > 3:
        raise ValueError
    t = 0.0
    for p in parts:
        t = t * 60 + float(p or 0)
    if t < 0:
        raise ValueError
    return t


def fmt_size(b):
    b = float(b)
    for u in ("B", "KB", "MB", "GB"):
        if b < 1024 or u == "GB":
            s = f"{b:.0f} {u}" if u == "B" else f"{b:.1f} {u}"
            return s.replace(".", ",")
        b /= 1024


def fmt_num(n):
    return f"{int(round(n)):,}".replace(",", ".")


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _rate(x):
    try:
        n, d = str(x).split("/")
        n, d = float(n), float(d)
        return n / d if d else 0.0
    except Exception:
        return _f(x)


def run_quiet(cmd, timeout=None):
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          stdin=subprocess.DEVNULL, creationflags=NO_WINDOW, timeout=timeout)


# --------------------------------------------------------------------------- FFmpeg

def probe(ffprobe, path):
    r = run_quiet([ffprobe, "-v", "error", "-print_format", "json",
                   "-show_format", "-show_streams", path], timeout=60)
    if r.returncode != 0:
        msg = r.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(msg or "Não consegui ler esse arquivo.")
    d = json.loads(r.stdout.decode("utf-8", "replace") or "{}")
    streams = d.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"
              and not (s.get("disposition") or {}).get("attached_pic")), None)
    if v is None:
        raise RuntimeError("Esse arquivo não tem uma faixa de vídeo.")
    a = [s for s in streams if s.get("codec_type") == "audio"]
    fmt = d.get("format", {})
    dur = _f(fmt.get("duration")) or _f(v.get("duration"))
    w, h = int(v.get("width") or 0), int(v.get("height") or 0)
    rot = 0
    try:
        rot = int(float((v.get("tags") or {}).get("rotate", 0)))
    except ValueError:
        pass
    for sd in v.get("side_data_list") or []:
        if "rotation" in sd:
            rot = int(_f(sd["rotation"]))
    if abs(rot) % 180 == 90:
        w, h = h, w
    fps = _rate(v.get("avg_frame_rate")) or _rate(v.get("r_frame_rate")) or 30.0
    if not 1 <= fps <= 240:
        fps = 30.0
    size = os.path.getsize(path)
    br = _f(fmt.get("bit_rate")) or (size * 8 / dur if dur else 0)
    abr = sum(_f(s.get("bit_rate")) for s in a)
    tracks = []
    for i, s in enumerate(a):
        tags = {k.lower(): v for k, v in (s.get("tags") or {}).items()}
        name = (tags.get("title") or "").strip()
        handler = (tags.get("handler_name") or "").strip()
        if not name and handler and handler.lower() not in GENERIC_HANDLERS and not handler.startswith("ISO Media"):
            name = handler
        ch = int(s.get("channels") or 0)
        tracks.append(dict(name=name, codec=s.get("codec_name", "?"), channels=ch,
                           bitrate=_f(s.get("bit_rate")), lang=tags.get("language", "")))
    fps_str = v.get("avg_frame_rate") if _rate(v.get("avg_frame_rate")) else None
    return dict(duration=dur, start=_f(fmt.get("start_time")), width=w, height=h, fps=fps, fps_str=fps_str,
                vcodec=v.get("codec_name", "?"), audio_tracks=len(a), audio=tracks,
                acodec=(a[0].get("codec_name") if a else None),
                size=size, bitrate=br, audio_bitrate=abr)


GENERIC_HANDLERS = {"soundhandler", "core media audio", "audiohandler", "sound media handler",
                    "stereo", "mono", "audio", "sound handler", "applehandler"}


def track_label(i, t):
    ch = {1: "mono", 2: "estéreo"}.get(t["channels"], f"{t['channels']} canais" if t["channels"] else "")
    desc = ch
    name = t["name"] or f"Faixa {i + 1}"
    return f"{i + 1}. {name}" + (f"  ({desc})" if desc else "")


def load_keyframes(ffprobe, path, start_offset):
    r = run_quiet([ffprobe, "-v", "error", "-select_streams", "v:0",
                   "-show_entries", "packet=pts_time,flags", "-of", "csv=p=0", path], timeout=900)
    ks = []
    for line in r.stdout.decode("ascii", "replace").splitlines():
        parts = line.strip().split(",")
        if len(parts) >= 2 and "K" in parts[1]:
            try:
                ks.append(max(0.0, float(parts[0]) - start_offset))
            except ValueError:
                pass
    ks.sort()
    return ks


def preview_vf(w, h, crop):
    c = f"crop={crop[0]}:{crop[1]}," if crop else ""
    return f"{c}scale={w}:{h}:flags=bilinear"


def grab_frame(ff, src, t, w, h, crop=None):
    """Retorna um quadro como imagem PPM (que o Tk exibe direto)."""
    cmd = [ff, "-hide_banner", "-nostdin", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", src,
           "-an", "-sn", "-frames:v", "1", "-vf", preview_vf(w, h, crop),
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    try:
        r = run_quiet(cmd, timeout=30)
    except subprocess.TimeoutExpired:
        return None
    n = w * h * 3
    if len(r.stdout) < n:
        return None
    return b"P6 %d %d 255\n" % (w, h) + r.stdout[:n]


def _even(x):
    return max(2, int(round(x / 2.0)) * 2)


def output_dims(info, target, aspect="keep"):
    """Dimensões finais (largura, altura) e o recorte aplicado antes (ou None)."""
    w, h = info["width"], info["height"]
    crop = None
    if aspect in ("stretch", "crop") and w and h:
        ratio = 16 / 9 if w >= h else 9 / 16
        if aspect == "crop":
            if w / h > ratio:
                cw, ch = h * ratio, h
            else:
                cw, ch = w, w / ratio
            crop = (max(2, int(min(cw, w)) // 2 * 2), max(2, int(min(ch, h)) // 2 * 2))
            if crop == (w, h):
                crop = None
            else:
                w, h = crop
        elif w >= h:
            w = h * ratio
        else:
            h = w / ratio
    short = min(w, h)
    if target and short and target < short:
        k = target / short
        w, h = w * k, h * k
    return _even(w), _even(h), crop


def video_filter(info, target, aspect):
    W, H, crop = output_dims(info, target, aspect)
    f = []
    if crop:
        f.append(f"crop={crop[0]}:{crop[1]}")
    base = crop or (info["width"], info["height"])
    if (W, H) != tuple(base):
        f.append(f"scale={W}:{H}:flags=lanczos")
    if aspect != "keep":
        f.append("setsar=1")
    return ",".join(f) or None


def audio_kbps_for(s, info):
    a, tr = s["audio"], s.get("tracks", [])
    if not info["audio"] or a == 0 or not tr:
        return 0
    if a == "copy":
        return sum((info["audio"][i]["bitrate"] / 1000) or 128 for i, _ in tr)
    return a * (1 if s.get("mix") and len(tr) > 1 else len(tr))


def audio_args(s, info):
    """Opções de áudio: seleção de faixas, volume e mixagem."""
    a, tr = s["audio"], s.get("tracks", [])
    if not info["audio"] or a == 0 or not tr:
        return ["-an"]
    if a == "copy":
        maps = []
        for i, _ in tr:
            maps += ["-map", f"0:a:{i}"]
        return maps + ["-c:a", "copy"]
    parts, labels = [], []
    for k, (i, vol) in enumerate(tr):
        if vol != 100:
            parts.append(f"[0:a:{i}]volume={vol / 100:.2f}[vol{k}]")
            labels.append(f"[vol{k}]")
        else:
            labels.append(f"[0:a:{i}]")
    maps = []
    if s.get("mix") and len(tr) > 1:
        parts.append("".join(labels) + f"amix=inputs={len(tr)}:duration=longest:normalize=0[mix]")
        maps = ["-map", "[mix]"]
    else:
        for lab, (i, _) in zip(labels, tr):
            maps += ["-map", lab if lab.startswith("[vol") else f"0:a:{i}"]
    fc = ["-filter_complex", ";".join(parts)] if parts else []
    return fc + maps + ["-c:a", "aac", "-b:a", f"{a}k"]


def compute_segments(a, b, edits):
    """Trechos que vão para o vídeo final: [(início, fim, velocidade)], dentro de A–B, sem os removidos."""
    segs = [(a, b, 1.0)]
    for e in sorted(edits, key=lambda x: x["start"]):
        out = []
        for (x0, x1, f) in segs:
            s0, s1 = max(x0, e["start"]), min(x1, e["end"])
            if s1 - s0 <= 1e-6:
                out.append((x0, x1, f))
                continue
            if s0 > x0:
                out.append((x0, s0, f))
            if e["kind"] == "speed":
                out.append((s0, s1, e["factor"]))
            if s1 < x1:
                out.append((s1, x1, f))
        segs = out
    merged = []
    for seg in segs:
        if seg[1] - seg[0] < 0.02:
            continue
        if merged and merged[-1][2] == seg[2] and abs(merged[-1][1] - seg[0]) < 1e-6:
            merged[-1] = (merged[-1][0], seg[1], seg[2])
        else:
            merged.append(seg)
    return merged


def add_edit(edits, new):
    """Insere uma edição; ela substitui o que houver por baixo dela."""
    out = []
    for e in edits:
        if e["end"] <= new["start"] or e["start"] >= new["end"]:
            out.append(e)
            continue
        if e["start"] < new["start"]:
            out.append(dict(e, end=new["start"]))
        if e["end"] > new["end"]:
            out.append(dict(e, start=new["end"]))
    out.append(new)
    return sorted([e for e in out if e["end"] - e["start"] > 0.01], key=lambda x: x["start"])


def video_budget(s, info):
    """Calcula o bitrate de vídeo para caber no tamanho pedido (modos MB e %)."""
    dur = s["out_dur"]
    if s["mode"] == "mb":
        target = s["mb"] * 1024 * 1024
    else:
        target = info["size"] * (s["src_dur"] / info["duration"]) * s["pct"] / 100.0
    total_kbps = target * 8 / dur / 1000 * 0.975  # folga para o contêiner
    akbps = audio_kbps_for(s, info)
    vk = total_kbps - akbps
    if vk < 50:
        raise ValueError("Esse tamanho é pequeno demais para um vídeo dessa duração. "
                         "Aumente o limite, encurte o vídeo ou reduza/remova o áudio.")
    capped = False
    src_vk = (info["bitrate"] - info["audio_bitrate"]) / 1000
    if src_vk > 100 and vk > src_vk:
        vk, capped = src_vk, True
    return int(vk), akbps, target, capped


def video_codec_args(s, info, is_mp4):
    vcodec, mode = s["vcodec"], s["mode"]
    v = ["-c:v", vcodec, "-preset", s["preset"], "-pix_fmt", "yuv420p"]
    if vcodec == "libx265" and is_mp4:
        v += ["-tag:v", "hvc1"]
    if mode in ("mb", "pct"):
        vk = video_budget(s, info)[0]
        v += ["-b:v", f"{vk}k"]
        if not s["two_pass"]:
            v += ["-maxrate", f"{int(vk * 1.5)}k", "-bufsize", f"{vk * 2}k"]
    elif mode == "crf":
        v += ["-crf", str(s["crf"])]
    else:
        v += ["-crf", str(s["crf"]), "-maxrate", f"{s['maxrate']}k", "-bufsize", f"{s['maxrate'] * 2}k"]
    return v


def edit_graph(s, info, with_audio):
    """filter_complex que emenda os trechos (cada um é uma entrada do FFmpeg) e aplica a velocidade."""
    segs = s["segments"]
    tracks = s["tracks"] if (with_audio and info["audio"] and s["audio"] not in (0, "copy")) else []
    mix = s.get("mix") and len(tracks) > 1
    n_aout = 1 if mix else len(tracks)
    parts, cat = [], []
    for j, (_, _, f) in enumerate(segs):
        sp = "setpts=PTS-STARTPTS" if f == 1 else f"setpts=(PTS-STARTPTS)/{f:g}"
        parts.append(f"[{j}:v:0]{sp}[v{j}]")
        cat.append(f"[v{j}]")
        labs = []
        for k, (i, vol) in enumerate(tracks):
            chain = ["asetpts=PTS-STARTPTS"]
            if vol != 100:
                chain.append(f"volume={vol / 100:.2f}")
            if f != 1:
                chain.append(f"atempo={f:g}")
            parts.append(f"[{j}:a:{i}]" + ",".join(chain) + f"[t{j}x{k}]")
            labs.append(f"[t{j}x{k}]")
        if mix:
            parts.append("".join(labs) + f"amix=inputs={len(labs)}:duration=longest:normalize=0[m{j}]")
            cat.append(f"[m{j}]")
        else:
            cat += labs
    outs = "[vc]" + "".join(f"[ac{k}]" for k in range(n_aout))
    parts.append("".join(cat) + f"concat=n={len(segs)}:v=1:a={n_aout}" + outs)
    vf = [f"fps={info.get('fps_str') or round(info['fps'], 3)}"]
    extra = video_filter(info, s["height"], s.get("aspect", "keep"))
    if extra:
        vf.append(extra)
    parts.append("[vc]" + ",".join(vf) + "[vout]")
    maps = ["-map", "[vout]"]
    for k in range(n_aout):
        maps += ["-map", f"[ac{k}]"]
    aopts = ["-c:a", "aac", "-b:a", f"{s['audio']}k"] if n_aout else ["-an"]
    return ["-filter_complex", ";".join(parts)] + maps + aopts


def build_jobs(s, info, ff):
    """Monta os comandos do FFmpeg. Retorna lista de (descrição, comando, peso no progresso)."""
    prog = ["-progress", "pipe:1", "-nostats"]
    out = s["out"]
    is_mp4 = out.lower().endswith((".mp4", ".m4v", ".mov"))
    mux = ["-movflags", "+faststart"] if is_mp4 else []
    vcodec = s["vcodec"]
    base = [ff, "-hide_banner", "-nostdin", "-loglevel", "error", "-y"]
    segs = s["segments"]
    simple = len(segs) == 1 and segs[0][2] == 1

    if simple:
        start, end, _ = segs[0]
        head = base + ["-ss", f"{start:.3f}", "-i", s["src"], "-t", f"{end - start:.3f}"]
        aargs = audio_args(s, info)
        if vcodec == "copy":
            cmd = head + ["-map", "0:v:0", "-c:v", "copy"] + aargs + \
                ["-avoid_negative_ts", "make_zero"] + mux + prog + [out]
            return [("Copiando o trecho", cmd, 1.0)]
        v = ["-map", "0:v:0"] + video_codec_args(s, info, is_mp4)
        vf = video_filter(info, s["height"], s.get("aspect", "keep"))
        if vf:
            v += ["-vf", vf]
        a_pass2, a_pass1 = aargs, ["-an"]
    else:
        if vcodec == "copy":
            raise ValueError("Remover trechos do meio ou acelerar precisa recodificar o vídeo. "
                             "Em \"Saída de vídeo\", escolha H.264 ou H.265.")
        if s["audio"] == "copy" and info["audio"] and s["tracks"]:
            raise ValueError("Remover trechos do meio ou acelerar precisa recodificar o áudio. "
                             "Em \"Saída de áudio\", escolha uma opção AAC.")
        head = list(base)
        for (a, b, _) in segs:
            head += ["-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", s["src"]]
        v = video_codec_args(s, info, is_mp4)
        a_pass2 = edit_graph(s, info, with_audio=True)
        a_pass1 = edit_graph(s, info, with_audio=False)

    def x265(extra):
        if vcodec != "libx265":
            return []
        return ["-x265-params", ":".join(["log-level=error"] + extra)]

    if s["mode"] in ("mb", "pct") and s["two_pass"]:
        # Os arquivos de log das passagens ficam na pasta temporária (cwd do processo).
        if vcodec == "libx264":
            p1 = head + a_pass1 + v + ["-pass", "1", "-passlogfile", "passlog", "-f", "null"] + prog + ["-"]
            p2 = head + a_pass2 + v + ["-pass", "2", "-passlogfile", "passlog"] + mux + prog + [out]
        else:
            p1 = head + a_pass1 + v + x265(["pass=1", "stats=x265.log"]) + ["-f", "null"] + prog + ["-"]
            p2 = head + a_pass2 + v + x265(["pass=2", "stats=x265.log"]) + mux + prog + [out]
        return [("Passagem 1 de 2 (analisando o vídeo)", p1, 0.4), ("Passagem 2 de 2 (codificando)", p2, 0.6)]
    return [("Codificando", head + a_pass2 + v + x265([]) + mux + prog + [out], 1.0)]


def join_signature(info):
    return (info["vcodec"], info["width"], info["height"], round(info["fps"], 2),
            tuple((t["codec"], t["channels"]) for t in info["audio"]))


def join_compatible(infos):
    return len({join_signature(i) for i in infos}) == 1


def fit_filter(W, H, fit):
    if fit == "stretch":
        return f"scale={W}:{H}"
    if fit == "crop":
        return f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"
    return f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black"


def build_join_job(ff, files, infos, out, fit, list_path):
    """Junta vídeos em sequência. Sem recodificar quando todos são do mesmo formato."""
    prog = ["-progress", "pipe:1", "-nostats"]
    is_mp4 = out.lower().endswith((".mp4", ".m4v", ".mov"))
    mux = ["-movflags", "+faststart"] if is_mp4 else []
    base = [ff, "-hide_banner", "-nostdin", "-loglevel", "error", "-y"]
    first = infos[0]
    if join_compatible(infos):
        with open(list_path, "w", encoding="utf-8") as f:
            for path in files:
                f.write("file '" + path.replace("\\", "/").replace("'", "'\\''") + "'\n")
        cmd = base + ["-f", "concat", "-safe", "0", "-i", list_path, "-map", "0:v:0", "-map", "0:a?",
                      "-c", "copy"] + mux + prog + [out]
        return [("Juntando (sem recodificar)", cmd, 1.0)]
    W, H = _even(first["width"]), _even(first["height"])
    fps = first.get("fps_str") or f"{first['fps']:.3f}"
    n_a = max(len(i["audio"]) for i in infos)
    cmd = list(base)
    for path in files:
        cmd += ["-i", path]
    parts, cat = [], []
    for j, info in enumerate(infos):
        parts.append(f"[{j}:v:0]{fit_filter(W, H, fit)},setsar=1,fps={fps},format=yuv420p[v{j}]")
        cat.append(f"[v{j}]")
        for k in range(n_a):
            if k < len(info["audio"]):
                parts.append(f"[{j}:a:{k}]asetpts=PTS-STARTPTS,aresample=48000,"
                             f"aformat=sample_rates=48000:channel_layouts=stereo[a{j}x{k}]")
            else:
                parts.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={info['duration']:.3f}[a{j}x{k}]")
            cat.append(f"[a{j}x{k}]")
    outs = "[vout]" + "".join(f"[ao{k}]" for k in range(n_a))
    parts.append("".join(cat) + f"concat=n={len(files)}:v=1:a={n_a}" + outs)
    cmd += ["-filter_complex", ";".join(parts), "-map", "[vout]"]
    for k in range(n_a):
        cmd += ["-map", f"[ao{k}]"]
        names = [i["audio"][k]["name"] for i in infos if k < len(i["audio"]) and i["audio"][k]["name"]]
        if names:
            cmd += [f"-metadata:s:a:{k}", f"title={names[0]}"]
            if is_mp4:
                cmd += [f"-metadata:s:a:{k}", f"handler_name={names[0]}"]
    # qualidade alta: o arquivo juntado é uma etapa intermediária; a compressão vem depois, no editor
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p"]
    if n_a:
        cmd += ["-c:a", "aac", "-b:a", "192k"]
    cmd += mux + prog + [out]
    return [("Juntando os vídeos", cmd, 1.0)]


def parse_out_time(txt):
    try:
        if txt.startswith("-"):
            return 0.0
        h, m, s = txt.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)
    except ValueError:
        return None


def run_jobs(jobs, dur, tmpdir, on_stage, on_progress, is_cancelled, set_proc):
    """Executa os comandos em sequência. Lança RuntimeError se o FFmpeg falhar."""
    done = 0.0
    for label, cmd, weight in jobs:
        if is_cancelled():
            return False
        on_stage(label)
        errpath = os.path.join(tmpdir, "ffmpeg_erro.txt")
        with open(errpath, "w", encoding="utf-8") as errf:
            p = subprocess.Popen(cmd, cwd=tmpdir, stdout=subprocess.PIPE, stderr=errf,
                                 stdin=subprocess.DEVNULL, creationflags=NO_WINDOW,
                                 text=True, encoding="utf-8", errors="replace")
            set_proc(p)
            for line in p.stdout:
                if line.startswith("out_time="):
                    t = parse_out_time(line[9:].strip())
                    if t is not None and dur > 0:
                        on_progress(min(1.0, done + weight * min(t / dur, 1.0)))
            p.wait()
            set_proc(None)
        if is_cancelled():
            return False
        if p.returncode != 0:
            with open(errpath, encoding="utf-8", errors="replace") as f:
                err = f.read().strip()
            raise RuntimeError(err[-1500:] or f"O FFmpeg terminou com erro (código {p.returncode}).")
        done += weight
        on_progress(done)
    return True


# --------------------------------------------------------------------------- reprodução

class Player:
    """Toca o vídeo (sem som) lendo quadros crus do FFmpeg."""

    def __init__(self, ff, src, t0, w, h, fps, crop=None):
        self.t0, self.w, self.h, self.fps = t0, w, h, fps
        self.q = queue.Queue(maxsize=6)
        self.stopped = False
        cmd = [ff, "-hide_banner", "-nostdin", "-loglevel", "error", "-ss", f"{t0:.3f}", "-i", src,
               "-an", "-sn", "-vf", f"fps={fps:.4f}," + preview_vf(w, h, crop),
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     stdin=subprocess.DEVNULL, creationflags=NO_WINDOW,
                                     bufsize=w * h * 3 * 2)
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        n = self.w * self.h * 3
        header = b"P6 %d %d 255\n" % (self.w, self.h)
        while not self.stopped:
            try:
                buf = self.proc.stdout.read(n)
            except (OSError, ValueError):
                buf = b""
            item = header + buf if buf and len(buf) == n else None
            while not self.stopped:
                try:
                    self.q.put(item, timeout=0.2)
                    break
                except queue.Full:
                    continue
            if item is None:
                break

    def stop(self):
        self.stopped = True
        try:
            self.proc.kill()
        except OSError:
            pass


class AudioPlayer:
    """Som da prévia: o FFmpeg decodifica e mixa as faixas escolhidas e o ffplay toca."""

    def __init__(self, ff, ffplay, src, t0, tracks):
        cmd = [ff, "-hide_banner", "-nostdin", "-loglevel", "error", "-ss", f"{t0:.3f}", "-i", src, "-vn", "-sn"]
        if len(tracks) == 1 and tracks[0][1] == 100:
            cmd += ["-map", f"0:a:{tracks[0][0]}"]
        else:
            parts, labs = [], []
            for k, (i, vol) in enumerate(tracks):
                parts.append(f"[0:a:{i}]volume={vol / 100:.2f}[p{k}]")
                labs.append(f"[p{k}]")
            if len(labs) > 1:
                parts.append("".join(labs) + f"amix=inputs={len(labs)}:duration=longest:normalize=0[pm]")
                out = "[pm]"
            else:
                out = labs[0]
            cmd += ["-filter_complex", ";".join(parts), "-map", out]
        cmd += ["-ac", "2", "-ar", "48000", "-f", "wav", "-"]
        self.dec = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
        self.play = subprocess.Popen([ffplay, "-hide_banner", "-loglevel", "quiet", "-nodisp", "-autoexit",
                                      "-fflags", "nobuffer", "-probesize", "4096", "-f", "wav", "pipe:0"],
                                     stdin=self.dec.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     creationflags=NO_WINDOW)
        self.dec.stdout.close()

    def stop(self):
        for p in (self.play, self.dec):
            try:
                p.kill()
            except OSError:
                pass


# --------------------------------------------------------------------------- widgets auxiliares

class Tooltip:
    def __init__(self, widget, text):
        self.w, self.text, self.tip, self.job = widget, text, None, None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._cancel()
        self.job = self.w.after(450, self._show)

    def _cancel(self):
        if self.job:
            self.w.after_cancel(self.job)
            self.job = None

    def _show(self):
        if self.tip:
            return
        x = self.w.winfo_rootx() + 8
        y = self.w.winfo_rooty() + self.w.winfo_height() + 4
        self.tip = tw = tk.Toplevel(self.w)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        text = self.text() if callable(self.text) else self.text
        tk.Label(tw, text=text, background=PALETTE["tip_bg"], foreground=PALETTE["tip_fg"], relief="solid",
                 borderwidth=1, padx=6, pady=3, justify="left").pack()

    def _hide(self, _e=None):
        self._cancel()
        if self.tip:
            self.tip.destroy()
            self.tip = None


class ScrollFrame(ttk.Frame):
    """Área com barra de rolagem vertical que só aparece quando o conteúdo não cabe."""

    def __init__(self, parent, bg):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0, bd=0, bg=bg, width=1, height=1)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)
        self.win = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vsb.grid(row=0, column=1, sticky="ns")
        self.vsb.grid_remove()
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.inner.bind("<Configure>", self._update)
        self.canvas.bind("<Configure>", self._update)
        self.bind("<Enter>", lambda e: self.bind_all("<MouseWheel>", self._wheel))
        self.bind("<Leave>", self._leave)
        self.bind("<Enter>", lambda e: (self.bind_all("<Button-4>", lambda ev: self._scroll(-1)),
                                        self.bind_all("<Button-5>", lambda ev: self._scroll(1))), add="+")

    def _update(self, _e=None):
        self.canvas.itemconfigure(self.win, width=self.canvas.winfo_width())
        self.canvas.configure(scrollregion=(0, 0, self.inner.winfo_reqwidth(), self.inner.winfo_reqheight()))
        need = self.inner.winfo_reqheight() > self.canvas.winfo_height() + 1
        if need and not self.vsb.winfo_ismapped():
            self.vsb.grid()
        elif not need and self.vsb.winfo_ismapped():
            self.vsb.grid_remove()
            self.canvas.yview_moveto(0)

    def _leave(self, _e=None):
        x, y = self.winfo_pointerxy()
        w = self.winfo_containing(x, y)
        if w is None or not str(w).startswith(str(self)):
            self.unbind_all("<MouseWheel>")

    def _scroll(self, n):
        if self.vsb.winfo_ismapped():
            self.canvas.yview_scroll(n, "units")

    def _wheel(self, e):
        if isinstance(e.widget, (ttk.Combobox, ttk.Spinbox, tk.Spinbox, ttk.Scale)):
            return
        self._scroll(-1 if e.delta > 0 else 1)


class ProgressDialog(tk.Toplevel):
    def __init__(self, app, jobs, dur, out, on_finish=None, cleanup=None, check=None):
        super().__init__(app)
        self.check = check
        self.app, self.jobs, self.dur, self.out = app, jobs, dur, out
        self.on_finish = on_finish or app.on_save_finished
        self.cleanup = cleanup or []
        self.title("Salvando vídeo")
        self.configure(bg=PALETTE["bg"])
        self.transient(app)
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        f = ttk.Frame(self, padding=16)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text=os.path.basename(out), font=app.font_bold).pack(anchor="w")
        self.stage = ttk.Label(f, text=jobs[0][0])
        self.stage.pack(anchor="w", pady=(8, 4))
        self.bar = ttk.Progressbar(f, length=int(440 * app.k), maximum=1000)
        self.bar.pack(fill="x")
        self.info = ttk.Label(f, text="Iniciando…")
        self.info.pack(anchor="w", pady=(6, 10))
        self.btn = ttk.Button(f, text="Cancelar", command=self.cancel)
        self.btn.pack(anchor="e")
        self.q = queue.Queue()
        self.proc = None
        self.cancelled = False
        self.t0 = time.time()
        self.update_idletasks()
        x = app.winfo_rootx() + (app.winfo_width() - self.winfo_width()) // 2
        y = app.winfo_rooty() + (app.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        set_titlebar_dark(self, app.dark)
        self.grab_set()
        threading.Thread(target=self._work, daemon=True).start()
        self.after(100, self._poll)

    def _set_proc(self, p):
        self.proc = p

    def _work(self):
        tmp = tempfile.mkdtemp(prefix="enxuto_")
        try:
            ok = run_jobs(self.jobs, self.dur, tmp,
                          on_stage=lambda s: self.q.put(("stage", s)),
                          on_progress=lambda p: self.q.put(("progress", p)),
                          is_cancelled=lambda: self.cancelled,
                          set_proc=self._set_proc)
            if ok and self.check:
                retry = self.check(self.out)
                if retry:
                    self.q.put(("progress", 0.0))
                    ok = run_jobs([(f"Ajustando para caber no limite — {lbl[0].lower()}{lbl[1:]}", c, w)
                                   for lbl, c, w in retry], self.dur, tmp,
                                  on_stage=lambda st: self.q.put(("stage", st)),
                                  on_progress=lambda p: self.q.put(("progress", p)),
                                  is_cancelled=lambda: self.cancelled,
                                  set_proc=self._set_proc)
            if not ok:
                try:
                    os.remove(self.out)
                except OSError:
                    pass
                self.q.put(("cancelled",))
            else:
                self.q.put(("done",))
        except Exception as e:  # noqa: BLE001
            try:
                os.remove(self.out)
            except OSError:
                pass
            self.q.put(("error", str(e)))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            for f in self.cleanup:
                try:
                    os.remove(f)
                except OSError:
                    pass

    def _poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "stage":
                    self.stage.config(text=msg[1])
                elif kind == "progress":
                    p = msg[1]
                    self.bar["value"] = p * 1000
                    el = time.time() - self.t0
                    txt = f"{p * 100:.0f}%   ·   Decorrido: {fmt_time(el)[:8]}"
                    if p > 0.02:
                        txt += f"   ·   Restante: ~{fmt_time(el / p * (1 - p))[:8]}"
                    self.info.config(text=txt)
                elif kind in ("done", "error", "cancelled"):
                    self.grab_release()
                    self.destroy()
                    self.on_finish(kind, self.out, msg[1] if kind == "error" else None)
                    return
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def cancel(self):
        if self.cancelled:
            return
        if not messagebox.askyesno("Cancelar", "Cancelar o salvamento?", parent=self):
            return
        self.cancelled = True
        self.btn.config(state="disabled")
        self.info.config(text="Cancelando…")
        p = self.proc
        if p:
            try:
                p.kill()
            except OSError:
                pass


class ShortcutsDialog(tk.Toplevel):
    """Lista de atalhos, com opção de trocar cada um."""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.map = dict(app.shortcuts)
        self.title("Atalhos de teclado")
        self.transient(app)
        self.configure(bg=PALETTE["bg"])
        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="Dê dois cliques numa ação (ou selecione e clique em \"Alterar\") para "
                          "escolher outra tecla.", wraplength=int(440 * app.k)).pack(anchor="w", pady=(0, 8))
        body = ttk.Frame(f)
        body.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(body, columns=("acao", "tecla"), show="headings", height=16, selectmode="browse")
        self.tree.heading("acao", text="Ação")
        self.tree.heading("tecla", text="Atalho")
        self.tree.column("acao", width=int(270 * app.k))
        self.tree.column("tecla", width=int(150 * app.k), anchor="center")
        sb = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda e: self.change())
        self.tree.bind("<Return>", lambda e: self.change())
        bt = ttk.Frame(f)
        bt.pack(fill="x", pady=(10, 0))
        ttk.Button(bt, text="Alterar…", command=self.change).pack(side="left")
        ttk.Button(bt, text="Sem atalho", command=self.clear).pack(side="left", padx=4)
        ttk.Button(bt, text="Restaurar padrões", command=self.defaults).pack(side="left")
        ttk.Button(bt, text="Fechar", command=self.destroy).pack(side="right")
        self._fill()
        set_titlebar_dark(self, app.dark)
        self.grab_set()
        self.tree.focus_set()

    def _fill(self, keep=None):
        self.tree.delete(*self.tree.get_children())
        for aid, label, _ in ACTIONS:
            self.tree.insert("", "end", iid=aid, values=(label, seq_label(self.map.get(aid))))
        sel = keep or ACTIONS[0][0]
        self.tree.selection_set(sel)
        self.tree.focus(sel)
        self.tree.see(sel)

    def _selected(self):
        sel = self.tree.selection()
        return sel[0] if sel else None

    def _commit(self, keep):
        self.app.set_shortcuts(self.map)
        self._fill(keep)

    def clear(self):
        aid = self._selected()
        if aid:
            self.map[aid] = ""
            self._commit(aid)

    def defaults(self):
        if messagebox.askyesno("Atalhos", "Voltar todos os atalhos para o padrão?", parent=self):
            self.map = dict(DEFAULT_KEYS)
            self._commit(self._selected())

    def change(self):
        aid = self._selected()
        if not aid:
            return
        label = next(lbl for a, lbl, _ in ACTIONS if a == aid)
        cap = tk.Toplevel(self)
        cap.title("Novo atalho")
        cap.transient(self)
        cap.configure(bg=PALETTE["bg"])
        fr = ttk.Frame(cap, padding=16)
        fr.pack(fill="both", expand=True)
        ttk.Label(fr, text=f"Pressione a tecla (ou combinação) para:\n\n{label}", justify="center").pack()
        ttk.Label(fr, text="Esc cancela", style="Hint.TLabel").pack(pady=(10, 0))
        cap.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - cap.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - cap.winfo_height()) // 3
        cap.geometry(f"+{max(0, x)}+{max(0, y)}")
        set_titlebar_dark(cap, self.app.dark)
        cap.grab_set()
        cap.focus_force()

        def on_key(e):
            seq = event_to_seq(e)
            if seq is None:
                return "break"
            if seq == "<Key-Escape>":
                cap.destroy()
                return "break"
            other = next((a for a, v in self.map.items() if v == seq and a != aid), None)
            if other:
                olabel = next(lbl for a, lbl, _ in ACTIONS if a == other)
                if not messagebox.askyesno("Atalho em uso", f"{seq_label(seq)} já é usado em \"{olabel}\".\n\n"
                                           f"Passar para \"{label}\"? (\"{olabel}\" fica sem atalho)", parent=cap):
                    return "break"
                self.map[other] = ""
            self.map[aid] = seq
            cap.destroy()
            self.grab_set()
            self._commit(aid)
            return "break"
        cap.bind("<KeyPress>", on_key)
        cap.protocol("WM_DELETE_WINDOW", cap.destroy)


class JoinDialog(tk.Toplevel):
    """Modo separado para emendar vários vídeos num só, que depois abre no editor."""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.files, self.infos = [], []
        self.title("Juntar vídeos")
        self.transient(app)
        self.configure(bg=PALETTE["bg"])
        k = app.k
        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="Os vídeos são emendados na ordem da lista. O resultado abre no editor, "
                          "para você cortar e comprimir como de costume.",
                  wraplength=int(520 * k), justify="left").pack(anchor="w", pady=(0, 8))
        mid = ttk.Frame(f)
        mid.pack(fill="both", expand=True)
        P = PALETTE
        self.lb = tk.Listbox(mid, height=9, width=60, activestyle="none", bg=P["field"], fg=P["fg"],
                             selectbackground=P["sel"], selectforeground="#ffffff", highlightthickness=1,
                             highlightbackground=P["border"], highlightcolor=P["accent"], relief="flat",
                             font=(app.font_base, 9))
        self.lb.pack(side="left", fill="both", expand=True)
        side = ttk.Frame(mid)
        side.pack(side="left", fill="y", padx=(8, 0))
        for txt, cmd in (("Adicionar…", self.add), ("Remover", self.remove),
                         ("▲ Subir", lambda: self.move(-1)), ("▼ Descer", lambda: self.move(1))):
            ttk.Button(side, text=txt, command=cmd).pack(fill="x", pady=2)
        self.info_lbl = ttk.Label(f, text="", wraplength=int(520 * k), justify="left")
        self.info_lbl.pack(anchor="w", pady=(10, 0))
        self.fit_row = ttk.Frame(f)
        ttk.Label(self.fit_row, text="Quando o tamanho for diferente do primeiro vídeo:").pack(side="left")
        self.fit_var = tk.StringVar(value="Barras pretas")
        ttk.Combobox(self.fit_row, textvariable=self.fit_var, state="readonly", width=16,
                     values=["Barras pretas", "Esticar", "Cortar bordas"]).pack(side="left", padx=6)
        bt = ttk.Frame(f)
        bt.pack(side="bottom", fill="x", pady=(12, 0))
        self.go = ttk.Button(bt, text="Juntar e abrir no editor", style="Accent.TButton", command=self.join)
        self.go.pack(side="right")
        ttk.Button(bt, text="Cancelar", command=self.destroy).pack(side="right", padx=6)
        if app.src:
            self._append(app.src, app.info)
        self._refresh()
        set_titlebar_dark(self, app.dark)
        self.grab_set()

    def _append(self, path, info):
        self.files.append(path)
        self.infos.append(info)

    def add(self):
        paths = filedialog.askopenfilenames(parent=self, title="Adicionar vídeos",
                                            filetypes=[("Vídeos", VIDEO_TYPES), ("Todos os arquivos", "*.*")])
        for p in paths:
            try:
                info = probe(self.app.ffprobe, p)
            except Exception as e:  # noqa: BLE001
                messagebox.showerror("Juntar vídeos", f"Não consegui abrir {os.path.basename(p)}.\n\n{e}", parent=self)
                continue
            self._append(os.path.abspath(p), info)
        self._refresh()

    def remove(self):
        sel = self.lb.curselection()
        if sel:
            del self.files[sel[0]]
            del self.infos[sel[0]]
            self._refresh(min(sel[0], len(self.files) - 1))

    def move(self, d):
        sel = self.lb.curselection()
        if not sel:
            return
        i, j = sel[0], sel[0] + d
        if 0 <= j < len(self.files):
            self.files[i], self.files[j] = self.files[j], self.files[i]
            self.infos[i], self.infos[j] = self.infos[j], self.infos[i]
            self._refresh(j)

    def _refresh(self, select=None):
        self.lb.delete(0, "end")
        for n, (p, i) in enumerate(zip(self.files, self.infos)):
            self.lb.insert("end", f"{n + 1}.  {os.path.basename(p)}   —   {i['width']}×{i['height']}  ·  "
                                  f"{fmt_time(i['duration'])[:8]}")
        if select is not None and select >= 0:
            self.lb.selection_set(select)
        total = sum(i["duration"] for i in self.infos)
        if len(self.files) < 2:
            txt = "Adicione pelo menos dois vídeos."
            self.fit_row.pack_forget()
        elif join_compatible(self.infos):
            txt = (f"Duração total: {fmt_time(total)[:8]}. Os vídeos têm o mesmo formato, então a junção é "
                   "instantânea e sem perda de qualidade.")
            self.fit_row.pack_forget()
        else:
            txt = (f"Duração total: {fmt_time(total)[:8]}. Os vídeos têm formatos diferentes, então serão "
                   "convertidos para o formato do primeiro, em alta qualidade. Pode levar alguns minutos.")
            self.fit_row.pack(anchor="w", pady=(6, 0), before=self.go.master)
        self.info_lbl.config(text=txt)
        self.go.state(["!disabled"] if len(self.files) >= 2 else ["disabled"])

    def join(self):
        if len(self.files) < 2:
            return
        first = self.files[0]
        base, ext = os.path.splitext(os.path.basename(first))
        compat = join_compatible(self.infos)
        ext = ext if compat and ext.lower() in (".mp4", ".mkv", ".mov") else ".mp4"
        out = filedialog.asksaveasfilename(parent=self, title="Salvar o vídeo juntado como",
                                           initialdir=os.path.dirname(first), initialfile=f"{base}_juntado{ext}",
                                           defaultextension=ext,
                                           filetypes=[("Vídeo", "*" + ext), ("Todos os arquivos", "*.*")])
        if not out:
            return
        out = os.path.abspath(out)
        if os.path.normcase(out) in {os.path.normcase(x) for x in self.files}:
            messagebox.showerror("Juntar vídeos", "Escolha um nome diferente dos vídeos da lista.", parent=self)
            return
        fit = {"Barras pretas": "keep", "Esticar": "stretch", "Cortar bordas": "crop"}[self.fit_var.get()]
        fd, list_path = tempfile.mkstemp(prefix="enxuto_lista_", suffix=".txt")
        os.close(fd)
        jobs = build_join_job(self.app.ff, self.files, self.infos, out, fit, list_path)
        total = sum(i["duration"] for i in self.infos)
        app = self.app
        self.grab_release()
        self.destroy()

        def done(kind, path, err):
            if kind == "done":
                app.open_file(path)
                app.status_lbl.config(text=f"Vídeos juntados em {path}. Agora é só cortar/comprimir e salvar.")
            elif kind == "error":
                messagebox.showerror("Erro ao juntar", "O FFmpeg não conseguiu juntar os vídeos:\n\n" + err,
                                     parent=app)
            else:
                app.status_lbl.config(text="Junção cancelada.")
        app.stop_play()
        ProgressDialog(app, jobs, total, out, on_finish=done, cleanup=[list_path])


# --------------------------------------------------------------------------- janela principal

class App(tk.Tk):
    def __init__(self, ff, ffprobe, ffplay=None):
        super().__init__()
        self.ff, self.ffprobe, self.ffplay = ff, ffprobe, ffplay
        self.audio_player = None
        self.k = max(1.0, self.winfo_fpixels("1i") / 96.0)
        self.title(APP_NAME)
        self.geometry(f"{int(1180 * self.k)}x{int(720 * self.k)}")
        self.minsize(int(940 * self.k), int(600 * self.k))
        ico = os.path.join(app_dir(), "icone.ico")
        if IS_WIN and os.path.isfile(ico):
            try:
                self.iconbitmap(ico)
            except tk.TclError:
                pass

        self.style = ttk.Style(self)
        base = "Segoe UI" if IS_WIN else "DejaVu Sans"
        self.font_base = base
        self.font_mono = ("Consolas" if IS_WIN else "DejaVu Sans Mono", 10)
        self.font_bold = (base, 10, "bold")
        self.font_small = (base, 8)
        self.settings = load_settings()
        self.theme_var = tk.StringVar(value=self.settings.get("theme", "system"))
        self.menus = []
        self.dark = False
        style_widgets(self.style, LIGHT, base)

        self.src = None
        self.info = None
        self.keyframes = []
        self.edits = []
        self.history = []
        self.shortcuts = dict(DEFAULT_KEYS)
        self.shortcuts.update({k: v for k, v in (self.settings.get("shortcuts") or {}).items() if k in DEFAULT_KEYS})
        self._bound = []
        self._accels = []
        self.pos = 0.0
        self.A = 0.0
        self.B = 0.0
        self.pw, self.ph, self.crop = 0, 0, None
        self.player = None
        self._play_job = None
        self._prev_req = None
        self._prev_busy = False
        self._resize_job = None
        self.q = queue.Queue()

        self._build_menu()
        self._build_ui()
        self._bind_keys()
        self.apply_theme(save=False)
        self._refresh_edits()
        self._refresh_panel()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(50, self._poll)

    # ---------------------------------------------------------------- construção
    def _menu(self, bar, label):
        mb = ttk.Menubutton(bar, text=label, style="Menubar.TMenubutton", takefocus=False)
        mb.pack(side="left")
        menu = tk.Menu(mb, tearoff=False)
        mb.configure(menu=menu)
        self.menus.append(menu)
        return menu

    def _menu_item(self, menu, label, action):
        menu.add_command(label=label, command=lambda: self._actions()[action]())
        self._accels.append((menu, menu.index("end"), action))

    def key_label(self, action):
        seq = self.shortcuts.get(action)
        return seq_label(seq) if seq else "sem atalho"

    def _tip(self, text, action):
        return lambda: f"{text} ({self.key_label(action)})"

    def _build_menu(self):
        bar = ttk.Frame(self, style="Menubar.TFrame")
        bar.pack(side="top", fill="x")
        arq = self._menu(bar, "Arquivo")
        self._menu_item(arq, "Abrir vídeo…", "open")
        self._menu_item(arq, "Salvar vídeo…", "save")
        arq.add_separator()
        arq.add_command(label="Juntar vídeos…", command=self.open_join)
        arq.add_separator()
        arq.add_command(label="Sair", command=self._on_close)
        ed = self._menu(bar, "Editar")
        self._menu_item(ed, "Desfazer", "undo")
        ed.add_separator()
        self._menu_item(ed, "Marcar início (A)", "set_a")
        self._menu_item(ed, "Marcar fim (B)", "set_b")
        self._menu_item(ed, "Limpar seleção", "clear_sel")
        self._menu_item(ed, "Ir para A", "goto_a")
        self._menu_item(ed, "Ir para B", "goto_b")
        ed.add_separator()
        self._menu_item(ed, "Remover o trecho A–B", "cut")
        self._menu_item(ed, "Acelerar o trecho A–B", "speed")
        ed.add_separator()
        ed.add_command(label="Atalhos de teclado…", command=self._show_shortcuts)
        ex = self._menu(bar, "Exibir")
        tema = tk.Menu(ex, tearoff=False)
        self.menus.append(tema)
        for label, val in THEMES.items():
            tema.add_radiobutton(label=label, value=val, variable=self.theme_var, command=self.apply_theme)
        ex.add_cascade(label="Tema", menu=tema)
        aj = self._menu(bar, "Ajuda")
        aj.add_command(label="Atalhos de teclado…", command=self._show_shortcuts)
        aj.add_command(label="Sobre", command=lambda: messagebox.showinfo(
            "Sobre", f"{APP_NAME} {VERSION}\n\nCorta e comprime vídeos com o FFmpeg.", parent=self))

    def _build_ui(self):
        k = self.k
        # barra de ferramentas
        tb = ttk.Frame(self, padding=(6, 4))
        tb.pack(side="top", fill="x")
        ttk.Button(tb, text="📂  Abrir", command=self.open_file).pack(side="left")
        ttk.Button(tb, text="💾  Salvar", command=self.save).pack(side="left", padx=4)
        self.theme_btn = ttk.Button(tb, text="🌙", width=3, command=self.toggle_theme, takefocus=False)
        self.theme_btn.pack(side="right")
        Tooltip(self.theme_btn, "Alternar entre modo claro e escuro")
        ttk.Separator(self).pack(side="top", fill="x")

        # barra de status
        sb = ttk.Frame(self, padding=(8, 3))
        sb.pack(side="bottom", fill="x")
        self.status_lbl = ttk.Label(sb, text="Abra um vídeo para começar (Ctrl+O).")
        self.status_lbl.pack(side="left")
        ttk.Separator(self).pack(side="bottom", fill="x")

        body = ttk.Frame(self)
        body.pack(side="top", fill="both", expand=True)

        # painel esquerdo -------------------------------------------------
        left = ttk.Frame(body, padding=(8, 8, 4, 8), width=int(285 * k))
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        self._build_left(left)

        # área do vídeo ---------------------------------------------------
        right = ttk.Frame(body, padding=(4, 8, 8, 4))
        right.pack(side="left", fill="both", expand=True)
        self.video = tk.Canvas(right, bg="#000000", highlightthickness=0)
        self.video.pack(fill="both", expand=True)
        self.photo = tk.PhotoImage()
        self.img_item = self.video.create_image(0, 0, image=self.photo, anchor="center")
        self.txt_item = self.video.create_text(0, 0, fill="#8a8a8a", font=(self.font_bold[0], 12),
                                               text="Arquivo → Abrir vídeo (Ctrl+O)", anchor="center")
        self.video.bind("<Configure>", self._on_video_resize)
        self.video.bind("<Button-1>", lambda e: self.video.focus_set())

        # linha do tempo
        self.tl = tk.Canvas(right, height=int(30 * k), highlightthickness=0, bg=self._bg())
        self.tl.pack(fill="x", pady=(6, 2))
        self.tl.bind("<Configure>", lambda e: self._draw_timeline())
        self.tl.bind("<Button-1>", self._tl_click)
        self.tl.bind("<B1-Motion>", self._tl_click)

        # controles
        ctl = ttk.Frame(right)
        ctl.pack(fill="x")
        btns = [
            ("▶", self.toggle_play, "Tocar / pausar", "play"),
            ("⏮", lambda: self.seek(0), "Primeiro quadro", "first"),
            ("⏪", self.prev_key, "Keyframe anterior", "prev_key"),
            ("◀", lambda: self.step_frames(-1), "Quadro anterior", "prev_frame"),
            ("▶|", lambda: self.step_frames(1), "Próximo quadro", "next_frame"),
            ("⏩", self.next_key, "Próximo keyframe", "next_key"),
            ("⏭", lambda: self.seek(self.max_t()), "Último quadro", "last"),
        ]
        for i, (txt, cmd, tip, act) in enumerate(btns):
            b = ttk.Button(ctl, text=txt, width=3, command=cmd, style="Transport.TButton", takefocus=False)
            b.pack(side="left", padx=1)
            Tooltip(b, self._tip(tip, act))
            if i == 0:
                self.play_btn = b
                self.mute = bool(self.settings.get("mute", False))
                self.mute_btn = ttk.Button(ctl, text="🔇" if self.mute else "🔊", width=3, command=self.toggle_mute,
                                           style="Transport.TButton", takefocus=False)
                self.mute_btn.pack(side="left", padx=1)
                Tooltip(self.mute_btn, self._tip("Ligar / desligar o som da prévia", "mute"))
        ttk.Separator(ctl, orient="vertical").pack(side="left", fill="y", padx=6)
        ba = ttk.Button(ctl, text="A", width=3, command=self.set_a, style="Transport.TButton", takefocus=False)
        ba.pack(side="left", padx=1)
        Tooltip(ba, self._tip("Marcar início da seleção", "set_a"))
        bb = ttk.Button(ctl, text="B", width=3, command=self.set_b, style="Transport.TButton", takefocus=False)
        bb.pack(side="left", padx=1)
        Tooltip(bb, self._tip("Marcar fim da seleção", "set_b"))
        bc = ttk.Button(ctl, text="✕", width=3, command=self.clear_selection, style="Transport.TButton",
                        takefocus=False)
        bc.pack(side="left", padx=1)
        Tooltip(bc, self._tip("Limpar seleção (vídeo inteiro)", "clear_sel"))
        ttk.Separator(ctl, orient="vertical").pack(side="left", fill="y", padx=6)
        bcut = ttk.Button(ctl, text="✂ Remover", command=self.cut_selection, style="Edit.TButton",
                          takefocus=False)
        bcut.pack(side="left", padx=1)
        Tooltip(bcut, self._tip("Tira o trecho entre A e B do vídeo; as partes de antes e depois ficam emendadas",
                                "cut"))
        bsp = ttk.Button(ctl, text="⏩ Acelerar", command=self.speed_selection, style="Edit.TButton",
                         takefocus=False)
        bsp.pack(side="left", padx=(4, 1))
        Tooltip(bsp, self._tip("Acelera o trecho entre A e B (o áudio acelera junto, sem ficar fino)", "speed"))
        self.speed_var = tk.StringVar(value="2×")
        ttk.Combobox(ctl, textvariable=self.speed_var, values=list(SPEEDS), state="readonly", width=5).pack(
            side="left", padx=(2, 0))

        # segunda linha: tempo atual (esquerda) e seleção A/B (direita)
        row2 = ttk.Frame(right)
        row2.pack(fill="x", pady=(4, 0))
        sel = ttk.Frame(row2)
        sel.pack(side="right")
        ttk.Label(sel, text="Seleção").pack(side="left", padx=(0, 4))
        self.a_btn = ttk.Button(sel, text="A: 00:00:00.000", command=lambda: self.seek(self.A), takefocus=False)
        self.a_btn.pack(side="left", padx=2)
        Tooltip(self.a_btn, "Ir para A")
        self.b_btn = ttk.Button(sel, text="B: 00:00:00.000", command=self.goto_b, takefocus=False)
        self.b_btn.pack(side="left", padx=2)
        Tooltip(self.b_btn, "Ir para B")

        tm = ttk.Frame(row2)
        tm.pack(side="left")
        ttk.Label(tm, text="Tempo:").pack(side="left")
        self.time_var = tk.StringVar(value=fmt_time(0))
        self.time_entry = ttk.Entry(tm, textvariable=self.time_var, width=12, font=self.font_mono,
                                    justify="center")
        self.time_entry.pack(side="left", padx=3)
        self.time_entry.bind("<Return>", self._time_entered)
        self.time_entry.bind("<FocusOut>", lambda e: self.time_var.set(fmt_time(self.pos)))
        Tooltip(self.time_entry, "Digite um tempo e aperte Enter para ir até ele")
        self.total_lbl = ttk.Label(tm, text="/ 00:00:00.000", font=self.font_mono)
        self.total_lbl.pack(side="left")
        self.ftype_lbl = ttk.Label(tm, text="", width=12)
        self.ftype_lbl.pack(side="left", padx=(8, 0))

    def apply_theme(self, save=True):
        pref = self.theme_var.get()
        dark = windows_prefers_dark() if pref == "system" else pref == "dark"
        self.dark = dark
        P = DARK if dark else LIGHT
        PALETTE.clear()
        PALETTE.update(P)
        style_widgets(self.style, P, self.font_base)
        self.configure(bg=P["bg"])
        for m in self.menus:
            m.configure(bg=P["field"], fg=P["fg"], activebackground=P["sel"], activeforeground="#ffffff",
                        selectcolor=P["fg"], disabledforeground=P["muted"], relief="flat", bd=1)
        if hasattr(self, "tl"):
            self.tl.configure(bg=P["bg"])
            self._draw_timeline()
        if hasattr(self, "scroll"):
            self.scroll.canvas.configure(bg=P["bg"])
        if hasattr(self, "theme_btn"):
            self.theme_btn.configure(text="☀" if dark else "🌙")
        self._recolor_popdowns(self, P)
        set_titlebar_dark(self, dark)
        if save:
            self.settings["theme"] = pref
            save_settings(self.settings)
            # força o Windows a redesenhar a barra de título
            if IS_WIN:
                w, h = self.winfo_width(), self.winfo_height()
                self.geometry(f"{w + 1}x{h}")
                self.after(10, lambda: self.geometry(f"{w}x{h}"))

    def toggle_theme(self):
        self.theme_var.set("light" if self.dark else "dark")
        self.apply_theme()

    def _recolor_popdowns(self, widget, P):
        self.option_add("*TCombobox*Listbox.background", P["field"])
        self.option_add("*TCombobox*Listbox.foreground", P["fg"])
        self.option_add("*TCombobox*Listbox.selectBackground", P["sel"])
        self.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        stack = [widget]
        while stack:
            w = stack.pop()
            stack.extend(w.winfo_children())
            if isinstance(w, ttk.Combobox):
                try:
                    pd = self.tk.call("ttk::combobox::PopdownWindow", str(w))
                    self.tk.call(f"{pd}.f.l", "configure", "-background", P["field"], "-foreground", P["fg"],
                                 "-selectbackground", P["sel"], "-selectforeground", "#ffffff")
                except tk.TclError:
                    pass

    def _bg(self):
        try:
            return ttk.Style(self).lookup("TFrame", "background") or "#f0f0f0"
        except tk.TclError:
            return "#f0f0f0"

    def _build_left(self, left):
        k = self.k
        ttk.Button(left, text="Salvar vídeo…", style="Accent.TButton", command=self.save).pack(
            side="bottom", fill="x", pady=(8, 0))
        sf = self.scroll = ScrollFrame(left, self._bg())
        sf.pack(side="top", fill="both", expand=True)
        left = sf.inner
        # edições no meio do vídeo (escondido até existir alguma)
        self.edits_frame = ttk.LabelFrame(left, text="Edições", padding=8)
        self.edits_list = ttk.Frame(self.edits_frame)
        self.edits_list.pack(fill="x")
        eb = ttk.Frame(self.edits_frame)
        eb.pack(fill="x", pady=(6, 0))
        ttk.Button(eb, text="↶ Desfazer", command=self.undo, style="Small.TButton").pack(side="left")
        ttk.Button(eb, text="Apagar todas", command=self.clear_edits, style="Small.TButton").pack(side="right")
        # vídeo
        vf = self.vf_frame = ttk.LabelFrame(left, text="Saída de vídeo", padding=8)
        vf.pack(fill="x")
        vf.columnconfigure(1, weight=1)
        self.v_out = tk.StringVar(value=list(VIDEO_OUT)[0])
        cb = ttk.Combobox(vf, textvariable=self.v_out, values=list(VIDEO_OUT), state="readonly")
        cb.grid(row=0, column=0, columnspan=2, sticky="ew")
        cb.bind("<<ComboboxSelected>>", self._refresh_panel)

        self.copy_note = ttk.Label(vf, style="Hint.TLabel", wraplength=int(230 * k), justify="left",
                                   text="O vídeo não é recodificado: salva na hora e sem perder "
                                        "qualidade, mas o corte começa no keyframe mais próximo de A.")
        self.copy_note.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))

        self.enc = ttk.Frame(vf)
        self.enc.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        self.enc.columnconfigure(1, weight=1)
        ttk.Label(self.enc, text="Modo").grid(row=0, column=0, sticky="w", pady=2)
        self.mode_var = tk.StringVar(value=list(MODES)[0])
        mcb = ttk.Combobox(self.enc, textvariable=self.mode_var, values=list(MODES), state="readonly")
        mcb.grid(row=0, column=1, sticky="ew", padx=(6, 0), pady=2)
        mcb.bind("<<ComboboxSelected>>", self._refresh_panel)

        # linha: limite em MB
        self.row_mb = ttk.Frame(self.enc)
        self.row_mb.grid(row=1, column=0, columnspan=2, sticky="ew", pady=2)
        ttk.Label(self.row_mb, text="Limite").pack(side="left")
        self.mb_var = tk.StringVar(value="25")
        sp = ttk.Spinbox(self.row_mb, from_=1, to=100000, increment=1, textvariable=self.mb_var,
                         width=8, justify="right")
        sp.pack(side="left", padx=6)
        ttk.Label(self.row_mb, text="MB").pack(side="left")
        self.row_mbq = ttk.Frame(self.enc)
        self.row_mbq.grid(row=2, column=0, columnspan=2, sticky="w", pady=(0, 2))
        for val in ("10", "25", "50", "100", "500"):
            ttk.Button(self.row_mbq, text=val, width=4, takefocus=False, style="Small.TButton",
                       command=lambda v=val: self.mb_var.set(v)).pack(side="left", padx=(0, 2))

        # linha: porcentagem
        self.row_pct = ttk.Frame(self.enc)
        self.row_pct.grid(row=3, column=0, columnspan=2, sticky="ew", pady=2)
        self.pct_var = tk.DoubleVar(value=40)
        self.pct_lbl = ttk.Label(self.row_pct, text="40%", width=5)
        self.pct_lbl.pack(side="right")
        ttk.Scale(self.row_pct, from_=1, to=100, variable=self.pct_var,
                  command=lambda v: self.pct_var.set(round(float(v)))).pack(side="left", fill="x", expand=True)

        # linha: CRF
        self.row_crf = ttk.Frame(self.enc)
        self.row_crf.grid(row=4, column=0, columnspan=2, sticky="ew", pady=2)
        top = ttk.Frame(self.row_crf)
        top.pack(fill="x")
        ttk.Label(top, text="CRF").pack(side="left")
        self.crf_var = tk.DoubleVar(value=23)
        self.crf_lbl = ttk.Label(top, text="23", width=3)
        self.crf_lbl.pack(side="right")
        ttk.Scale(top, from_=0, to=51, variable=self.crf_var,
                  command=lambda v: self.crf_var.set(round(float(v)))).pack(side="left", fill="x",
                                                                            expand=True, padx=6)
        self.crf_hint = ttk.Label(self.row_crf, style="Hint.TLabel", wraplength=int(230 * k), justify="left")
        self.crf_hint.pack(anchor="w")

        # linha: bitrate máximo
        self.row_max = ttk.Frame(self.enc)
        self.row_max.grid(row=5, column=0, columnspan=2, sticky="ew", pady=2)
        ttk.Label(self.row_max, text="Máximo").pack(side="left")
        self.max_var = tk.StringVar(value="4000")
        ttk.Spinbox(self.row_max, from_=100, to=200000, increment=250, textvariable=self.max_var,
                    width=8, justify="right").pack(side="left", padx=6)
        ttk.Label(self.row_max, text="kbps").pack(side="left")

        self.two_pass = tk.BooleanVar(value=True)
        self.chk_2p = ttk.Checkbutton(self.enc, text="Duas passagens (mais exato)",
                                      variable=self.two_pass)
        self.chk_2p.grid(row=6, column=0, columnspan=2, sticky="w", pady=(4, 2))

        ttk.Label(self.enc, text="Resolução").grid(row=7, column=0, sticky="w", pady=2)
        self.res_var = tk.StringVar(value="Original")
        rcb = ttk.Combobox(self.enc, textvariable=self.res_var, values=list(RESOLUTIONS), state="readonly")
        rcb.grid(row=7, column=1, sticky="ew", padx=(6, 0), pady=2)
        rcb.bind("<<ComboboxSelected>>", self._refresh_panel)
        ttk.Label(self.enc, text="Proporção").grid(row=8, column=0, sticky="w", pady=2)
        self.aspect_var = tk.StringVar(value=list(ASPECTS)[0])
        xcb = ttk.Combobox(self.enc, textvariable=self.aspect_var, values=list(ASPECTS), state="readonly")
        xcb.grid(row=8, column=1, sticky="ew", padx=(6, 0), pady=2)
        xcb.bind("<<ComboboxSelected>>", self._refresh_panel)
        Tooltip(xcb, "Manter: não distorce a imagem.\n"
                     "Esticar: estica a imagem até 16:9, como num jogo em resolução esticada.\n"
                     "Cortar bordas: preenche 16:9 sem distorcer, cortando o que sobrar.")
        self.aspect_info = ttk.Label(self.enc, style="Hint.TLabel", wraplength=int(230 * k), justify="left")
        self.aspect_info.grid(row=9, column=0, columnspan=2, sticky="w")
        ttk.Label(self.enc, text="Velocidade").grid(row=10, column=0, sticky="w", pady=2)
        self.preset_var = tk.StringVar(value="Equilibrada")
        pcb = ttk.Combobox(self.enc, textvariable=self.preset_var, values=list(PRESETS), state="readonly")
        pcb.grid(row=10, column=1, sticky="ew", padx=(6, 0), pady=2)

        # áudio
        af = ttk.LabelFrame(left, text="Saída de áudio", padding=8)
        af.pack(fill="x", pady=(8, 0))
        self.a_out = tk.StringVar(value=list(AUDIO_OUT)[0])
        acb = ttk.Combobox(af, textvariable=self.a_out, values=list(AUDIO_OUT), state="readonly")
        acb.pack(fill="x")
        acb.bind("<<ComboboxSelected>>", self._refresh_panel)
        self.tracks_frame = ttk.Frame(af)
        self.tracks_frame.pack(fill="x", pady=(6, 0))
        self.track_vars = []
        self.mix_var = tk.BooleanVar(value=True)
        self.mix_chk = ttk.Checkbutton(af, text="Juntar as faixas marcadas numa só", variable=self.mix_var,
                                       command=self._refresh_panel)
        self.audio_hint = ttk.Label(af, style="Hint.TLabel", wraplength=int(230 * k), justify="left")
        self._build_tracks()

        # formato
        ff = ttk.LabelFrame(left, text="Formato de saída", padding=8)
        ff.pack(fill="x", pady=(8, 0))
        self.fmt_var = tk.StringVar(value="MP4")
        ttk.Combobox(ff, textvariable=self.fmt_var, values=list(FORMATS), state="readonly").pack(fill="x")

        # resultado + salvar
        rf = ttk.LabelFrame(left, text="Resultado", padding=8)
        rf.pack(fill="x", pady=(8, 0))
        self.est_lbl = ttk.Label(rf, text="—", wraplength=int(230 * k), justify="left")
        self.est_lbl.pack(anchor="w", fill="x")
        self.warn_lbl = ttk.Label(rf, text="", style="Warn.TLabel", wraplength=int(230 * k), justify="left")
        self.warn_lbl.pack(anchor="w", fill="x")

        for var in (self.mb_var, self.pct_var, self.crf_var, self.max_var, self.two_pass):
            var.trace_add("write", lambda *a: self._refresh_panel())

    def _actions(self):
        return {
            "play": self.toggle_play,
            "mute": self.toggle_mute,
            "prev_frame": lambda: self.step_frames(-1),
            "next_frame": lambda: self.step_frames(1),
            "back_1s": lambda: self.step_secs(-1),
            "fwd_1s": lambda: self.step_secs(1),
            "back_10s": lambda: self.step_secs(-10),
            "fwd_10s": lambda: self.step_secs(10),
            "prev_key": self.prev_key,
            "next_key": self.next_key,
            "first": lambda: self.seek(0),
            "last": lambda: self.seek(self.max_t()),
            "set_a": self.set_a,
            "set_b": self.set_b,
            "goto_a": lambda: self.seek(self.A),
            "goto_b": self.goto_b,
            "clear_sel": self.clear_selection,
            "cut": self.cut_selection,
            "speed": self.speed_selection,
            "undo": self.undo,
            "open": self.open_file,
            "save": self.save,
        }

    def _bind_keys(self):
        typing = (tk.Entry, ttk.Entry, tk.Spinbox, ttk.Spinbox, ttk.Combobox, ttk.Scale, tk.Listbox,
                  ttk.Treeview)
        for seq in self._bound:
            try:
                self.unbind(seq)
            except tk.TclError:
                pass
        self._bound = []
        actions = self._actions()

        def make(func, seq):
            mods, _ = seq_parts(seq)
            needs_guard = not ({"Control", "Alt"} & set(mods))

            def handler(_e):
                if needs_guard and isinstance(self.focus_get(), typing):
                    return None
                func()
                return "break"
            return handler

        for aid, _, _ in ACTIONS:
            seq = self.shortcuts.get(aid)
            if not seq:
                continue
            for v in seq_variants(seq):
                try:
                    self.bind(v, make(actions[aid], seq))
                    self._bound.append(v)
                except tk.TclError:
                    pass
        for menu, idx, aid in self._accels:
            seq = self.shortcuts.get(aid)
            menu.entryconfigure(idx, accelerator=seq_label(seq) if seq else "")

    def set_shortcuts(self, mapping):
        self.shortcuts = dict(mapping)
        self.settings["shortcuts"] = {k: v for k, v in self.shortcuts.items() if v != DEFAULT_KEYS.get(k)}
        save_settings(self.settings)
        self._bind_keys()

    def _show_shortcuts(self):
        ShortcutsDialog(self)

    def open_join(self):
        JoinDialog(self)

    # ---------------------------------------------------------------- painel
    def _refresh_panel(self, *_):
        copy = VIDEO_OUT[self.v_out.get()] == "copy"
        mode = MODES[self.mode_var.get()]
        if copy:
            self.enc.grid_remove()
            self.copy_note.grid()
        else:
            self.copy_note.grid_remove()
            self.enc.grid()
        rows = {self.row_mb: mode == "mb", self.row_mbq: mode == "mb", self.row_pct: mode == "pct",
                self.row_crf: mode in ("crf", "maxrate"), self.row_max: mode == "maxrate",
                self.chk_2p: mode in ("mb", "pct")}
        for w, show in rows.items():
            if show:
                w.grid()
            else:
                w.grid_remove()
        try:
            self.pct_lbl.config(text=f"{int(self.pct_var.get())}%")
            crf = int(self.crf_var.get())
            self.crf_lbl.config(text=str(crf))
        except (tk.TclError, ValueError):
            crf = 23
        hint = "Menor = mais qualidade e arquivo maior. 18 quase sem perda visível, 23 padrão, 28+ comprime bastante."
        if VIDEO_OUT[self.v_out.get()] == "libx265":
            hint += " No H.265, use uns 5 a mais para qualidade parecida (28 ≈ 23 do H.264)."
        self.crf_hint.config(text=hint)
        self._refresh_audio()
        self._refresh_aspect()
        self._update_estimate()

    # faixas de áudio
    def _build_tracks(self):
        for w in self.tracks_frame.winfo_children():
            w.destroy()
        self.track_vars = []
        tracks = self.info["audio"] if self.info else []
        if not self.info:
            ttk.Label(self.tracks_frame, text="As faixas aparecem aqui ao abrir um vídeo.",
                      style="Hint.TLabel").pack(anchor="w")
            return
        if not tracks:
            ttk.Label(self.tracks_frame, text="Este vídeo não tem áudio.", style="Hint.TLabel").pack(anchor="w")
            return
        ttk.Label(self.tracks_frame, text="Faixas que vão no vídeo:").grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(self.tracks_frame, text="Volume", style="Hint.TLabel").grid(row=0, column=1, columnspan=2,
                                                                              sticky="e")
        self.tracks_frame.columnconfigure(0, weight=1)
        for i, t in enumerate(tracks):
            on = tk.BooleanVar(value=True)
            vol = tk.StringVar(value="100")
            cb = ttk.Checkbutton(self.tracks_frame, text=track_label(i, t), variable=on,
                                 command=self._refresh_panel)
            cb.grid(row=i + 1, column=0, sticky="w", pady=1)
            sp = ttk.Spinbox(self.tracks_frame, from_=0, to=400, increment=10, textvariable=vol, width=4,
                             justify="right")
            sp.grid(row=i + 1, column=1, sticky="e", padx=(4, 0))
            ttk.Label(self.tracks_frame, text="%").grid(row=i + 1, column=2, sticky="w")
            vol.trace_add("write", lambda *a: self._update_estimate())
            self.track_vars.append((on, vol, sp))

    def _refresh_audio(self):
        a = AUDIO_OUT[self.a_out.get()]
        n_tracks = len(self.track_vars)
        n_sel = sum(1 for on, _, _ in self.track_vars if on.get())
        if a == 0 or not n_tracks:
            self.tracks_frame.pack_forget() if a == 0 else None
            self.mix_chk.pack_forget()
            self.audio_hint.pack_forget()
            if a != 0:
                self.tracks_frame.pack(fill="x", pady=(6, 0))
            return
        if not self.tracks_frame.winfo_ismapped():
            self.tracks_frame.pack(fill="x", pady=(6, 0))
        for _, _, sp in self.track_vars:
            sp.state(["disabled"] if a == "copy" else ["!disabled"])
        hint = ""
        if n_tracks > 1:
            self.mix_chk.pack(anchor="w", pady=(4, 0))
            self.mix_chk.state(["disabled"] if a == "copy" else ["!disabled"])
            if a == "copy":
                hint = "Com \"Copiar\", as faixas vão separadas e sem mudar volume. Para juntar, escolha AAC."
            elif self.mix_var.get() and n_sel > 1:
                hint = "As faixas marcadas viram uma só (ex.: jogo + microfone), que toca em qualquer lugar."
            elif n_sel > 1:
                hint = ("Vão como faixas separadas. Atenção: WhatsApp, Discord e navegadores "
                        "costumam tocar só a primeira.")
        else:
            self.mix_chk.pack_forget()
            if a == "copy":
                hint = "Com \"Copiar\" o volume não pode ser alterado. Para isso, escolha AAC."
        self.audio_hint.config(text=hint)
        if hint:
            self.audio_hint.pack(anchor="w", pady=(4, 0))
        else:
            self.audio_hint.pack_forget()

    def _refresh_aspect(self):
        if not hasattr(self, "aspect_info"):
            return
        txt = ""
        if self.info and VIDEO_OUT[self.v_out.get()] != "copy":
            W, H, _ = output_dims(self.info, RESOLUTIONS[self.res_var.get()], ASPECTS[self.aspect_var.get()])
            txt = f"Vídeo final: {W}×{H}  (original {self.info['width']}×{self.info['height']})"
        self.aspect_info.config(text=txt)
        if txt:
            self.aspect_info.grid()
        else:
            self.aspect_info.grid_remove()
        if hasattr(self, "video"):
            self._apply_size()

    def _view_geom(self):
        """Proporção e recorte que a prévia deve mostrar (reflete Esticar/Cortar)."""
        info = self.info
        if VIDEO_OUT[self.v_out.get()] != "copy" and ASPECTS[self.aspect_var.get()] != "keep":
            W, H, crop = output_dims(info, None, ASPECTS[self.aspect_var.get()])
            return W, H, crop
        return info["width"] or 16, info["height"] or 9, None

    def collect(self, need_out=False):
        if not self.info:
            raise ValueError("Abra um vídeo primeiro.")
        if self.B - self.A < 0.05:
            raise ValueError("A seleção (A → B) está curta demais.")
        segs = compute_segments(self.A, self.B, self.edits)
        if not segs:
            raise ValueError("Não sobrou nada do vídeo: os trechos removidos cobrem toda a seleção A–B.")
        s = dict(src=self.src, start=self.A, end=self.B, segments=segs,
                 out_dur=sum((b - a) / f for a, b, f in segs), src_dur=sum(b - a for a, b, _ in segs),
                 vcodec=VIDEO_OUT[self.v_out.get()], mode=MODES[self.mode_var.get()],
                 audio=AUDIO_OUT[self.a_out.get()], height=RESOLUTIONS[self.res_var.get()],
                 preset=PRESETS[self.preset_var.get()], two_pass=self.two_pass.get(),
                 aspect=ASPECTS[self.aspect_var.get()],
                 ext=FORMATS[self.fmt_var.get()], mb=25.0, pct=40, crf=23, maxrate=4000)
        tracks = []
        for i, (on, vol, _) in enumerate(self.track_vars):
            if on.get():
                try:
                    v = int(float(vol.get().replace(",", ".")))
                except ValueError:
                    raise ValueError(f"Volume inválido na faixa {i + 1}.")
                tracks.append((i, max(0, min(v, 1000))))
        s["tracks"] = tracks
        s["mix"] = self.mix_var.get() and len(tracks) > 1 and s["audio"] != "copy"
        if self.info["audio"] and s["audio"] != 0 and not tracks:
            raise ValueError("Marque pelo menos uma faixa de áudio (ou escolha \"Sem áudio\").")
        if s["vcodec"] != "copy":
            try:
                s["pct"] = int(self.pct_var.get())
                s["crf"] = int(self.crf_var.get())
            except (tk.TclError, ValueError):
                pass
            if s["mode"] == "mb":
                try:
                    s["mb"] = float(self.mb_var.get().replace(",", "."))
                    if s["mb"] <= 0:
                        raise ValueError
                except ValueError:
                    raise ValueError("Digite um limite válido em MB.")
            if s["mode"] == "maxrate":
                try:
                    s["maxrate"] = int(float(self.max_var.get().replace(",", ".")))
                    if s["maxrate"] < 100:
                        raise ValueError
                except ValueError:
                    raise ValueError("Digite um bitrate máximo válido (mínimo 100 kbps).")
        return s

    def _update_estimate(self):
        if not self.info:
            self.est_lbl.config(text="Abra um vídeo para ver a estimativa.")
            self.warn_lbl.config(text="")
            return
        dur = self.B - self.A
        lines = [f"Trecho: {fmt_time(dur)}"]
        warn = ""
        try:
            s = self.collect()
            info = self.info
            part = info["size"] * s["src_dur"] / info["duration"]
            edited = not (len(s["segments"]) == 1 and s["segments"][0][2] == 1)
            if edited:
                lines = [f"Duração final: {fmt_time(s['out_dur'])}  (antes: {fmt_time(dur)})"]
            if edited and (s["vcodec"] == "copy" or (s["audio"] == "copy" and info["audio"] and s["tracks"])):
                raise ValueError("Com trechos removidos ou acelerados, o vídeo precisa ser recodificado: "
                                 "escolha H.264/H.265 no vídeo e AAC no áudio.")
            if s["vcodec"] == "copy":
                lines.append(f"Tamanho: ~{fmt_size(part)} (sem perda)")
                kf = self._prev_keyframe(self.A)
                if kf is not None and self.A - kf > 0.02:
                    warn = (f"A não está num keyframe: o vídeo salvo vai começar em {fmt_time(kf)}. "
                            "Use ↑/↓ para pular entre keyframes, ou escolha H.264 para corte exato.")
            elif s["mode"] in ("mb", "pct"):
                vk, ak, target, capped = video_budget(s, info)
                if capped:
                    lines.append(f"Tamanho final: ~{fmt_size(part)} ou menos")
                    warn = "O trecho já cabe nesse limite, então vai sair menor do que ele."
                else:
                    lines.append(f"Tamanho final: até {fmt_size(target)}")
                lines.append(f"Vídeo: {fmt_num(vk)} kbps" + (f"  ·  Áudio: {fmt_num(ak)} kbps" if ak else ""))
                w, h, _ = output_dims(info, s["height"], s["aspect"])
                bpp = vk * 1000 / (w * h * min(info["fps"], 60))
                if not capped and bpp < 0.035:
                    warn = ("A qualidade pode ficar baixa com esse limite. Se ficar ruim, "
                            "reduza a resolução ou use H.265.")
            elif s["mode"] == "crf":
                lines.append("Tamanho final: depende do vídeo (o CRF fixa a qualidade, não o tamanho).")
            else:
                ak = audio_kbps_for(s, info)
                mx = (s["maxrate"] + ak) * 1000 * s["out_dur"] / 8
                lines.append(f"Tamanho final: no máximo ~{fmt_size(mx)}")
        except ValueError as e:
            warn = str(e)
        self.est_lbl.config(text="\n".join(lines))
        self.warn_lbl.config(text=warn)

    # ---------------------------------------------------------------- arquivo
    def open_file(self, path=None):
        if path is None:
            path = filedialog.askopenfilename(parent=self, title="Abrir vídeo",
                                              filetypes=[("Vídeos", VIDEO_TYPES), ("Todos os arquivos", "*.*")])
        if not path:
            return
        self.stop_play()
        self.config(cursor="watch")
        self.update_idletasks()
        try:
            info = probe(self.ffprobe, path)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Erro ao abrir", f"Não consegui abrir o vídeo.\n\n{e}", parent=self)
            return
        finally:
            self.config(cursor="")
        if info["duration"] <= 0:
            messagebox.showerror("Erro ao abrir", "Não consegui descobrir a duração desse vídeo.", parent=self)
            return
        self.src = os.path.abspath(path)
        self.info = info
        self.keyframes = []
        self.edits, self.history = [], []
        self._refresh_edits()
        self.pos, self.A, self.B = 0.0, 0.0, info["duration"]
        self.title(f"{os.path.basename(path)} — {APP_NAME}")
        self.fmt_var.set("MKV" if path.lower().endswith(".mkv") else "MP4")
        n = info["audio_tracks"]
        audio = f"{n} faixas" if n > 1 else (info["acodec"] if n else "sem áudio")
        self.status_lbl.config(text=(
            f"{info['width']}×{info['height']}  ·  {info['vcodec']}  ·  {info['fps']:.2f} fps  ·  "
            f"áudio: {audio}  ·  {fmt_time(info['duration'])}  ·  {fmt_size(info['size'])}").replace(".00 fps", " fps"))
        self.video.itemconfigure(self.txt_item, text="")
        src = self.src
        threading.Thread(target=lambda: self.q.put(("keys", src, load_keyframes(
            self.ffprobe, src, info["start"]))), daemon=True).start()
        self._build_tracks()
        self._apply_size()
        self._update_time_ui()
        self._refresh_panel()

    def save(self):
        if self.player:
            self.stop_play()
        try:
            s = self.collect()
        except ValueError as e:
            messagebox.showwarning(APP_NAME, str(e), parent=self)
            return
        base = os.path.splitext(os.path.basename(self.src))[0]
        out = filedialog.asksaveasfilename(
            parent=self, title="Salvar vídeo como", initialdir=os.path.dirname(self.src),
            initialfile=f"{base}_editado{s['ext']}", defaultextension=s["ext"],
            filetypes=[(self.fmt_var.get(), "*" + s["ext"]), ("Todos os arquivos", "*.*")])
        if not out:
            return
        if os.path.normcase(os.path.abspath(out)) == os.path.normcase(self.src):
            messagebox.showerror(APP_NAME, "Escolha um nome diferente do vídeo original.", parent=self)
            return
        s["out"] = os.path.abspath(out)
        try:
            jobs = build_jobs(s, self.info, self.ff)
        except ValueError as e:
            messagebox.showwarning(APP_NAME, str(e), parent=self)
            return
        self._last_settings = s
        info, ff = self.info, self.ff

        def check(out):
            """Se o vídeo passou do limite em MB, refaz uma vez com um bitrate proporcionalmente menor."""
            if s["mode"] != "mb" or s["vcodec"] == "copy" or not os.path.exists(out):
                return None
            size, limit = os.path.getsize(out), s["mb"] * 1024 * 1024
            if size <= limit:
                return None
            try:
                return build_jobs(dict(s, mb=s["mb"] * limit / size * 0.97), info, ff)
            except ValueError:
                return None
        ProgressDialog(self, jobs, s["out_dur"], s["out"], check=check)

    def on_save_finished(self, kind, out, err):
        if kind == "cancelled":
            self.status_lbl.config(text="Salvamento cancelado.")
            return
        if kind == "error":
            messagebox.showerror("Erro ao salvar", "O FFmpeg não conseguiu salvar o vídeo:\n\n" + err +
                                 "\n\nDica: se estiver usando \"Copiar\" no áudio ou no vídeo, "
                                 "tente AAC/H.264 ou o formato MKV.", parent=self)
            return
        size = os.path.getsize(out) if os.path.exists(out) else 0
        s = self._last_settings
        part = self.info["size"] * s["src_dur"] / self.info["duration"]
        msg = f"Vídeo salvo!\n\n{os.path.basename(out)}\nTamanho: {fmt_size(size)}"
        if s["vcodec"] != "copy" and part > 0:
            msg += f"  ({size / part * 100:.0f}% do original, que tinha ~{fmt_size(part)})"
        self.status_lbl.config(text=f"Salvo: {out}  ({fmt_size(size)})")
        if messagebox.askyesno("Pronto", msg + "\n\nAbrir a pasta do arquivo?", parent=self):
            open_folder(out)

    # ---------------------------------------------------------------- navegação
    def max_t(self):
        if not self.info:
            return 0.0
        return max(0.0, self.info["duration"] - 1.0 / self.info["fps"])

    def seek(self, t):
        if not self.info:
            return
        if self.player:
            self.stop_play()
        self.pos = min(max(0.0, t), self.max_t())
        self._update_time_ui()
        self.request_preview(self.pos)

    def step_frames(self, n):
        if self.info:
            self.seek(self.pos + n / self.info["fps"])

    def step_secs(self, s):
        if self.info:
            self.seek(self.pos + s)

    def _prev_keyframe(self, t):
        ks = self.keyframes
        if not ks:
            return None
        i = bisect.bisect_right(ks, t + 1e-3) - 1
        return ks[i] if i >= 0 else 0.0

    def next_key(self):
        if not self.info:
            return
        ks = self.keyframes
        if not ks:
            self.status_lbl.config(text="Ainda carregando a lista de keyframes… pulando 1 s.")
            return self.step_secs(1)
        i = bisect.bisect_right(ks, self.pos + 1e-3)
        if i < len(ks):
            self.seek(ks[i])

    def prev_key(self):
        if not self.info:
            return
        ks = self.keyframes
        if not ks:
            self.status_lbl.config(text="Ainda carregando a lista de keyframes… voltando 1 s.")
            return self.step_secs(-1)
        i = bisect.bisect_left(ks, self.pos - 1e-3) - 1
        self.seek(ks[i] if i >= 0 else 0.0)

    def set_a(self):
        if not self.info:
            return
        self.A = self.pos
        if self.B <= self.A + 0.01:
            self.B = self.info["duration"]
        self._update_time_ui()
        self._update_estimate()

    def set_b(self):
        if not self.info:
            return
        self.B = min(self.info["duration"], self.pos + 1.0 / self.info["fps"])
        if self.B <= self.A + 0.01:
            self.A = 0.0
        self._update_time_ui()
        self._update_estimate()

    def clear_selection(self):
        if not self.info:
            return
        self.A, self.B = 0.0, self.info["duration"]
        self._update_time_ui()
        self._update_estimate()

    # ---------------------------------------------------------------- edições
    def _push_history(self):
        self.history.append((list(self.edits), self.A, self.B))
        del self.history[:-50]

    def _selection_for_edit(self, verb):
        if not self.info:
            return None
        whole = self.A <= 0.01 and self.B >= self.info["duration"] - 0.01
        if whole or self.B - self.A < 0.05:
            messagebox.showinfo(APP_NAME, f"Primeiro marque o trecho que quer {verb}: vá até o início dele e "
                                          f"aperte A ({self.key_label('set_a')}), depois vá até o fim e aperte B "
                                          f"({self.key_label('set_b')}).", parent=self)
            return None
        return self.A, self.B

    def cut_selection(self):
        sel = self._selection_for_edit("remover")
        if not sel:
            return
        self._push_history()
        self.edits = add_edit(self.edits, dict(kind="cut", start=sel[0], end=sel[1]))
        self._after_edit(f"Trecho {fmt_time(sel[0])} → {fmt_time(sel[1])} removido.")

    def speed_selection(self):
        sel = self._selection_for_edit("acelerar")
        if not sel:
            return
        f = SPEEDS.get(self.speed_var.get(), 2.0)
        self._push_history()
        self.edits = add_edit(self.edits, dict(kind="speed", start=sel[0], end=sel[1], factor=f))
        self._after_edit(f"Trecho {fmt_time(sel[0])} → {fmt_time(sel[1])} acelerado ({self.speed_var.get()}).")

    def _after_edit(self, msg):
        # a seleção volta a ser o vídeo inteiro para não confundir com o trecho exportado
        self.A, self.B = 0.0, self.info["duration"]
        self._refresh_edits()
        self._update_time_ui()
        self._update_estimate()
        self.status_lbl.config(text=msg + f"  (desfazer: {self.key_label('undo')})")

    def remove_edit(self, idx):
        self._push_history()
        del self.edits[idx]
        self._refresh_edits()
        self._update_time_ui()
        self._update_estimate()

    def clear_edits(self):
        if not self.edits:
            return
        self._push_history()
        self.edits = []
        self._refresh_edits()
        self._update_time_ui()
        self._update_estimate()

    def undo(self):
        if not self.history:
            self.status_lbl.config(text="Nada para desfazer.")
            return
        self.edits, self.A, self.B = self.history.pop()
        self._refresh_edits()
        self._update_time_ui()
        self._update_estimate()
        self.status_lbl.config(text="Desfeito.")

    def _refresh_edits(self):
        if not hasattr(self, "edits_frame"):
            return
        for w in self.edits_list.winfo_children():
            w.destroy()
        if not self.edits:
            self.edits_frame.pack_forget()
            return
        if not self.edits_frame.winfo_ismapped():
            self.edits_frame.pack(fill="x", pady=(0, 8), before=self.vf_frame)
        self.edits_list.columnconfigure(0, weight=1)
        for i, e in enumerate(self.edits):
            if e["kind"] == "cut":
                txt = f"✂  {fmt_time(e['start'])[3:]} → {fmt_time(e['end'])[3:]}"
            else:
                fac = next((k for k, v in SPEEDS.items() if v == e["factor"]), f"{e['factor']:g}×")
                txt = f"⏩ {fac}  {fmt_time(e['start'])[3:]} → {fmt_time(e['end'])[3:]}"
            lb = ttk.Label(self.edits_list, text=txt, cursor="hand2")
            lb.grid(row=i, column=0, sticky="w", pady=1)
            lb.bind("<Button-1>", lambda _e, t=e["start"]: self.seek(t))
            Tooltip(lb, "Clique para ir até o trecho")
            ttk.Button(self.edits_list, text="✕", width=2, style="Small.TButton", takefocus=False,
                       command=lambda i=i: self.remove_edit(i)).grid(row=i, column=1, sticky="e")

    def goto_b(self):
        if self.info:
            self.seek(self.B - 1.0 / self.info["fps"])

    def _time_entered(self, _e=None):
        try:
            t = parse_time(self.time_var.get())
        except ValueError:
            self.bell()
            self.time_var.set(fmt_time(self.pos))
            return "break"
        self.seek(t)
        self.video.focus_set()
        return "break"

    def _update_time_ui(self):
        if self.focus_get() is not self.time_entry:
            self.time_var.set(fmt_time(self.pos))
        if self.info:
            self.total_lbl.config(text=f"/ {fmt_time(self.info['duration'])}")
            self.a_btn.config(text=f"A: {fmt_time(self.A)}")
            self.b_btn.config(text=f"B: {fmt_time(self.B)}")
            if self.keyframes:
                kf = self._prev_keyframe(self.pos)
                is_key = kf is not None and abs(self.pos - kf) < 0.5 / self.info["fps"]
                self.ftype_lbl.config(text="Keyframe (I)" if is_key else "Quadro P/B")
            else:
                self.ftype_lbl.config(text="")
        self._draw_timeline()

    def _draw_timeline(self):
        c = self.tl
        c.delete("all")
        W, H = c.winfo_width(), c.winfo_height()
        if W < 20:
            return
        P = PALETTE
        pad = 8 * self.k
        x0, x1 = pad, W - pad
        y = H * 0.45
        P = PALETTE
        c.create_rectangle(x0, y - 3, x1, y + 3, fill=P["track"], outline=P["track_line"])
        if not self.info:
            return
        d = self.info["duration"]

        def X(t):
            return x0 + (x1 - x0) * min(max(t, 0), d) / d

        xa, xb = X(self.A), X(self.B)
        c.create_rectangle(xa, y - 6, xb, y + 6, fill=P["range"], outline=P["range_line"])
        small = (self.font_bold[0], 7, "bold")
        for e in self.edits:
            ea, eb = X(e["start"]), max(X(e["end"]), X(e["start"]) + 2)
            if e["kind"] == "cut":
                c.create_rectangle(ea, y - 8, eb, y + 8, fill=P["cut"], outline=P["cut"])
                c.create_line(ea, y - 8, eb, y + 8, fill=P["bg"])
                c.create_line(ea, y + 8, eb, y - 8, fill=P["bg"])
            else:
                c.create_rectangle(ea, y - 8, eb, y + 8, fill=P["fast"], outline=P["fast"])
                if eb - ea > 24:
                    c.create_text((ea + eb) / 2, y, text=f"{e['factor']:g}×", fill="#000000", font=small)
        fnt = (self.font_bold[0], 7, "bold")
        c.create_text(xa, H - 1, text="A", anchor="s", fill=P["range_line"], font=fnt)
        c.create_text(xb, H - 1, text="B", anchor="s", fill=P["range_line"], font=fnt)
        px = X(self.pos)
        c.create_line(px, 1, px, y + 9, fill=P["head"], width=2)
        c.create_polygon(px - 5, 0, px + 5, 0, px, 6, fill=P["head"], outline="")

    def _tl_click(self, e):
        if not self.info:
            return
        pad = 8 * self.k
        W = self.tl.winfo_width()
        frac = (e.x - pad) / max(1, (W - 2 * pad))
        self.seek(frac * self.info["duration"])
        self.video.focus_set()

    # ---------------------------------------------------------------- prévia
    def _on_video_resize(self, _e):
        if self._resize_job:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(120, self._apply_size)

    def _apply_size(self):
        self._resize_job = None
        cw, ch = self.video.winfo_width(), self.video.winfo_height()
        self.video.coords(self.img_item, cw / 2, ch / 2)
        self.video.coords(self.txt_item, cw / 2, ch / 2)
        if not self.info or cw < 10 or ch < 10:
            return
        w, h, crop = self._view_geom()
        sc = min(cw / w, ch / h)
        pw, ph = max(2, int(w * sc) // 2 * 2), max(2, int(h * sc) // 2 * 2)
        if (pw, ph, crop) != (self.pw, self.ph, self.crop):
            self.pw, self.ph, self.crop = pw, ph, crop
            if self.player:
                self.stop_play()
            self.request_preview(self.pos)

    def request_preview(self, t):
        if not self.info or self.pw < 4:
            return
        self._prev_req = (self.src, t, self.pw, self.ph, self.crop)
        if not self._prev_busy:
            self._start_preview()

    def _start_preview(self):
        req, self._prev_req = self._prev_req, None
        if req is None:
            return
        self._prev_busy = True
        src, t, w, h, crop = req
        back = 0.3 / self.info["fps"]

        def work():
            self.q.put(("frame", req, grab_frame(self.ff, src, max(0.0, t - back), w, h, crop)))
        threading.Thread(target=work, daemon=True).start()

    def _show_ppm(self, ppm):
        try:
            self.photo.configure(data=ppm, format="ppm")
        except tk.TclError:
            pass

    # ---------------------------------------------------------------- reprodução
    def toggle_play(self):
        if not self.info:
            return
        if self.player:
            self.stop_play()
            return
        if self.pos >= self.max_t() - 0.05:
            self.pos = 0.0
        fps = min(self.info["fps"], 30.0)
        self.player = Player(self.ff, self.src, self.pos, self.pw, self.ph, fps, self.crop)
        self._play_first = None
        self._play_idx = 0
        self.play_btn.config(text="⏸")
        self._start_audio(self.pos)
        if not self.ffplay:
            self.status_lbl.config(text="Reproduzindo (sem som: o ffplay não foi encontrado)…")
        elif self.mute:
            self.status_lbl.config(text="Reproduzindo (som desligado)…")
        else:
            self.status_lbl.config(text="Reproduzindo…")
        self._play_tick()

    def _preview_tracks(self):
        """Faixas que a prévia toca: as mesmas que vão para o vídeo salvo."""
        if not self.info or not self.info["audio"] or AUDIO_OUT[self.a_out.get()] == 0:
            return []
        copy = AUDIO_OUT[self.a_out.get()] == "copy"
        out = []
        for i, (on, vol, _) in enumerate(self.track_vars):
            if on.get():
                try:
                    v = 100 if copy else max(0, min(int(float(vol.get().replace(",", "."))), 1000))
                except ValueError:
                    v = 100
                out.append((i, v))
        return out

    def _start_audio(self, t):
        self._stop_audio()
        tracks = self._preview_tracks()
        if self.mute or not self.ffplay or not tracks:
            return
        try:
            self.audio_player = AudioPlayer(self.ff, self.ffplay, self.src, t, tracks)
        except OSError:
            self.audio_player = None

    def _stop_audio(self):
        if self.audio_player:
            self.audio_player.stop()
            self.audio_player = None

    def toggle_mute(self):
        self.mute = not self.mute
        self.mute_btn.config(text="🔇" if self.mute else "🔊")
        self.settings["mute"] = self.mute
        save_settings(self.settings)
        if self.player:
            if self.mute:
                self._stop_audio()
            else:
                self._start_audio(self.pos)

    def stop_play(self):
        self._stop_audio()
        if self._play_job:
            self.after_cancel(self._play_job)
            self._play_job = None
        if self.player:
            self.player.stop()
            self.player = None
            self.play_btn.config(text="▶")

    def _play_tick(self):
        self._play_job = None
        p = self.player
        if p is None:
            return
        now = time.perf_counter()
        frame, ended = None, False
        if self._play_first is None:
            try:
                f = p.q.get_nowait()
                if f is None:
                    ended = True
                else:
                    frame, self._play_first, self._play_idx = f, now, 1
            except queue.Empty:
                pass
        else:
            due = int((now - self._play_first) * p.fps) + 1
            while self._play_idx < due:
                try:
                    f = p.q.get_nowait()
                except queue.Empty:
                    break
                if f is None:
                    ended = True
                    break
                frame = f
                self._play_idx += 1
        if frame:
            self._show_ppm(frame)
            self.pos = min(p.t0 + (self._play_idx - 1) / p.fps, self.max_t())
            self._update_time_ui()
        if ended:
            self.stop_play()
            return
        self._play_job = self.after(8, self._play_tick)

    # ---------------------------------------------------------------- loop
    def _poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                if msg[0] == "frame":
                    _, req, ppm = msg
                    self._prev_busy = False
                    if ppm and req[0] == self.src and not self.player:
                        self._show_ppm(ppm)
                    if self._prev_req:
                        self._start_preview()
                elif msg[0] == "keys":
                    _, src, ks = msg
                    if src == self.src:
                        self.keyframes = ks
                        self._update_time_ui()
                        self._update_estimate()
        except queue.Empty:
            pass
        self.after(30, self._poll)

    def _on_close(self):
        self.stop_play()
        self._stop_audio()
        self.destroy()


def open_folder(path):
    try:
        if IS_WIN:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(path)])
    except OSError:
        pass


def main():
    if IS_WIN:
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:  # noqa: BLE001
            pass
    ff, fp = find_tool("ffmpeg"), find_tool("ffprobe")
    if not ff or not fp:
        r = tk.Tk()
        r.withdraw()
        messagebox.showerror(APP_NAME, "Não encontrei o FFmpeg, que o programa usa para processar os vídeos.\n\n"
                                       "Reinstale o programa, ou instale o FFmpeg pelo PowerShell com:\n"
                                       "winget install Gyan.FFmpeg")
        r.destroy()
        return
    app = App(ff, fp, find_tool("ffplay"))
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        app.after(300, lambda: app.open_file(sys.argv[1]))
    app.mainloop()


if __name__ == "__main__":
    main()
