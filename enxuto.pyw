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
VERSION = "1.1"
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
             head="#d02020", tip_bg="#ffffe1", tip_fg="#000000", menubar="#e9e9e9")
DARK = dict(bg="#1e1f22", field="#2b2d31", fg="#e6e6e6", muted="#9aa0a6", border="#43474e",
            hover="#35383d", press="#3f4349", accent="#3d7be0", accent_hover="#5590f0", warn="#f0a35e",
            sel="#2f6fd6", track="#3a3d42", track_line="#55595f", range="#2c5596", range_line="#6ea2ff",
            head="#ff5555", tip_bg="#2b2d31", tip_fg="#e6e6e6", menubar="#26282b")
PALETTE = dict(LIGHT)
THEMES = {"Seguir o Windows": "system", "Claro": "light", "Escuro": "dark"}


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
    return dict(duration=dur, start=_f(fmt.get("start_time")), width=w, height=h, fps=fps,
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


def video_budget(s, info):
    """Calcula o bitrate de vídeo para caber no tamanho pedido (modos MB e %)."""
    dur = s["end"] - s["start"]
    if s["mode"] == "mb":
        target = s["mb"] * 1024 * 1024
    else:
        target = info["size"] * (dur / info["duration"]) * s["pct"] / 100.0
    total_kbps = target * 8 / dur / 1000 * 0.975  # folga para o contêiner
    akbps = audio_kbps_for(s, info)
    vk = total_kbps - akbps
    if vk < 50:
        raise ValueError("Esse tamanho é pequeno demais para um trecho dessa duração. "
                         "Aumente o limite, encurte o trecho ou reduza/remova o áudio.")
    capped = False
    src_vk = (info["bitrate"] - info["audio_bitrate"]) / 1000
    if src_vk > 100 and vk > src_vk:
        vk, capped = src_vk, True
    return int(vk), akbps, target, capped


def build_jobs(s, info, ff):
    """Monta os comandos do FFmpeg. Retorna lista de (descrição, comando, peso no progresso)."""
    start, dur = s["start"], s["end"] - s["start"]
    head = [ff, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-ss", f"{start:.3f}", "-i", s["src"], "-t", f"{dur:.3f}"]
    prog = ["-progress", "pipe:1", "-nostats"]
    out = s["out"]
    is_mp4 = out.lower().endswith((".mp4", ".m4v", ".mov"))
    mux = ["-movflags", "+faststart"] if is_mp4 else []
    vcodec = s["vcodec"]
    aargs = audio_args(s, info)

    if vcodec == "copy":
        cmd = head + ["-map", "0:v:0", "-c:v", "copy"] + aargs + \
            ["-avoid_negative_ts", "make_zero"] + mux + prog + [out]
        return [("Copiando o trecho", cmd, 1.0)]

    v = ["-map", "0:v:0", "-c:v", vcodec, "-preset", s["preset"], "-pix_fmt", "yuv420p"]
    if vcodec == "libx265" and is_mp4:
        v += ["-tag:v", "hvc1"]
    vf = video_filter(info, s["height"], s.get("aspect", "keep"))
    if vf:
        v += ["-vf", vf]
    mode = s["mode"]
    if mode in ("mb", "pct"):
        vk = video_budget(s, info)[0]
        v += ["-b:v", f"{vk}k"]
        if not s["two_pass"]:
            v += ["-maxrate", f"{int(vk * 1.5)}k", "-bufsize", f"{vk * 2}k"]
    elif mode == "crf":
        v += ["-crf", str(s["crf"])]
    else:
        v += ["-crf", str(s["crf"]), "-maxrate", f"{s['maxrate']}k", "-bufsize", f"{s['maxrate'] * 2}k"]

    def x265(extra):
        if vcodec != "libx265":
            return []
        return ["-x265-params", ":".join(["log-level=error"] + extra)]

    if mode in ("mb", "pct") and s["two_pass"]:
        # Os arquivos de log das passagens ficam na pasta temporária (cwd do processo).
        if vcodec == "libx264":
            p1 = head + v + ["-pass", "1", "-passlogfile", "passlog", "-an", "-f", "null"] + prog + ["-"]
            p2 = head + v + ["-pass", "2", "-passlogfile", "passlog"] + aargs + mux + prog + [out]
        else:
            p1 = head + v + x265(["pass=1", "stats=x265.log"]) + ["-an", "-f", "null"] + prog + ["-"]
            p2 = head + v + x265(["pass=2", "stats=x265.log"]) + aargs + mux + prog + [out]
        return [("Passagem 1 de 2 (analisando o vídeo)", p1, 0.4), ("Passagem 2 de 2 (codificando)", p2, 0.6)]
    return [("Codificando", head + v + x265([]) + aargs + mux + prog + [out], 1.0)]


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
        tk.Label(tw, text=self.text, background=PALETTE["tip_bg"], foreground=PALETTE["tip_fg"], relief="solid",
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
    def __init__(self, app, jobs, dur, out):
        super().__init__(app)
        self.app, self.jobs, self.dur, self.out = app, jobs, dur, out
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
                    self.app.on_save_finished(kind, self.out, msg[1] if kind == "error" else None)
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


# --------------------------------------------------------------------------- janela principal

class App(tk.Tk):
    def __init__(self, ff, ffprobe):
        super().__init__()
        self.ff, self.ffprobe = ff, ffprobe
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

    def _build_menu(self):
        bar = ttk.Frame(self, style="Menubar.TFrame")
        bar.pack(side="top", fill="x")
        arq = self._menu(bar, "Arquivo")
        arq.add_command(label="Abrir vídeo…", accelerator="Ctrl+O", command=self.open_file)
        arq.add_command(label="Salvar vídeo…", accelerator="Ctrl+S", command=self.save)
        arq.add_separator()
        arq.add_command(label="Sair", command=self._on_close)
        ed = self._menu(bar, "Editar")
        ed.add_command(label="Definir início (A)", accelerator="[", command=self.set_a)
        ed.add_command(label="Definir fim (B)", accelerator="]", command=self.set_b)
        ed.add_command(label="Limpar seleção", command=self.clear_selection)
        ed.add_separator()
        ed.add_command(label="Ir para A", command=lambda: self.seek(self.A))
        ed.add_command(label="Ir para B", command=self.goto_b)
        ex = self._menu(bar, "Exibir")
        tema = tk.Menu(ex, tearoff=False)
        self.menus.append(tema)
        for label, val in THEMES.items():
            tema.add_radiobutton(label=label, value=val, variable=self.theme_var, command=self.apply_theme)
        ex.add_cascade(label="Tema", menu=tema)
        aj = self._menu(bar, "Ajuda")
        aj.add_command(label="Atalhos de teclado", command=self._show_shortcuts)
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
            ("▶", self.toggle_play, "Tocar / pausar (Espaço)"),
            ("⏮", lambda: self.seek(0), "Primeiro quadro (Home)"),
            ("⏪", self.prev_key, "Keyframe anterior (↓)"),
            ("◀", lambda: self.step_frames(-1), "Quadro anterior (←)"),
            ("▶|", lambda: self.step_frames(1), "Próximo quadro (→)"),
            ("⏩", self.next_key, "Próximo keyframe (↑)"),
            ("⏭", lambda: self.seek(self.max_t()), "Último quadro (End)"),
        ]
        for i, (txt, cmd, tip) in enumerate(btns):
            b = ttk.Button(ctl, text=txt, width=3, command=cmd, style="Transport.TButton", takefocus=False)
            b.pack(side="left", padx=1)
            Tooltip(b, tip)
            if i == 0:
                self.play_btn = b
        ttk.Separator(ctl, orient="vertical").pack(side="left", fill="y", padx=6)
        ba = ttk.Button(ctl, text="A", width=3, command=self.set_a, style="Transport.TButton", takefocus=False)
        ba.pack(side="left", padx=1)
        Tooltip(ba, "Marcar início da seleção ( [ )")
        bb = ttk.Button(ctl, text="B", width=3, command=self.set_b, style="Transport.TButton", takefocus=False)
        bb.pack(side="left", padx=1)
        Tooltip(bb, "Marcar fim da seleção ( ] )")
        bc = ttk.Button(ctl, text="✕", width=3, command=self.clear_selection, style="Transport.TButton",
                        takefocus=False)
        bc.pack(side="left", padx=1)
        Tooltip(bc, "Limpar seleção (vídeo inteiro)")

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
        # vídeo
        vf = ttk.LabelFrame(left, text="Saída de vídeo", padding=8)
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

    def _bind_keys(self):
        typing = (tk.Entry, ttk.Entry, tk.Spinbox, ttk.Spinbox, ttk.Combobox, ttk.Scale)

        def guard(func):
            def handler(e):
                if isinstance(self.focus_get(), typing):
                    return None
                func()
                return "break"
            return handler

        keys = {
            "<space>": self.toggle_play,
            "<Left>": lambda: self.step_frames(-1),
            "<Right>": lambda: self.step_frames(1),
            "<Shift-Left>": lambda: self.step_secs(-1),
            "<Shift-Right>": lambda: self.step_secs(1),
            "<Control-Left>": lambda: self.step_secs(-10),
            "<Control-Right>": lambda: self.step_secs(10),
            "<Up>": self.next_key,
            "<Down>": self.prev_key,
            "<bracketleft>": self.set_a,
            "<bracketright>": self.set_b,
            "<Home>": lambda: self.seek(0),
            "<End>": lambda: self.seek(self.max_t()),
        }
        for seq, fn in keys.items():
            self.bind(seq, guard(fn))
        self.bind("<Control-o>", lambda e: self.open_file())
        self.bind("<Control-O>", lambda e: self.open_file())
        self.bind("<Control-s>", lambda e: self.save())
        self.bind("<Control-S>", lambda e: self.save())

    def _show_shortcuts(self):
        messagebox.showinfo("Atalhos de teclado", (
            "Espaço\tTocar / pausar\n"
            "← / →\tQuadro anterior / próximo\n"
            "Shift + ← / →\tVoltar / avançar 1 s\n"
            "Ctrl + ← / →\tVoltar / avançar 10 s\n"
            "↓ / ↑\tKeyframe anterior / próximo\n"
            "Home / End\tInício / fim do vídeo\n"
            "[  /  ]\tMarcar A (início) / B (fim)\n"
            "Ctrl + O\tAbrir vídeo\n"
            "Ctrl + S\tSalvar vídeo\n\n"
            "Clique ou arraste na linha do tempo para navegar."), parent=self)

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
        s = dict(src=self.src, start=self.A, end=self.B,
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
            part = info["size"] * dur / info["duration"]
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
                mx = (s["maxrate"] + ak) * 1000 * dur / 8
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
        ProgressDialog(self, jobs, s["end"] - s["start"], s["out"])

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
        part = self.info["size"] * (s["end"] - s["start"]) / self.info["duration"]
        msg = f"Vídeo salvo!\n\n{os.path.basename(out)}\nTamanho: {fmt_size(size)}"
        if s["vcodec"] != "copy" and part > 0:
            msg += f"  ({size / part * 100:.0f}% do trecho original, que tinha ~{fmt_size(part)})"
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
        self.status_lbl.config(text="Reproduzindo (a prévia não tem som)…")
        self._play_tick()

    def stop_play(self):
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
    app = App(ff, fp)
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        app.after(300, lambda: app.open_file(sys.argv[1]))
    app.mainloop()


if __name__ == "__main__":
    main()
