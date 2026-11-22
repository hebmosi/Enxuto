"""Monta a pasta do programa para Windows a partir do ambiente conda-forge win-64 e confere as DLLs."""
import glob
import os
import shutil
import sys

import pefile

AQUI = os.path.dirname(os.path.abspath(__file__))
ENV = os.path.join(AQUI, "winenv")
OUT = os.path.join(AQUI, "dist", "Enxuto")
LIBBIN = os.path.join(ENV, "Library", "bin")

# DLLs que o próprio Windows 10/11 fornece
SYSTEM = {
    "kernel32.dll", "user32.dll", "gdi32.dll", "advapi32.dll", "shell32.dll", "ole32.dll", "oleaut32.dll",
    "comdlg32.dll", "comctl32.dll", "ws2_32.dll", "winmm.dll", "imm32.dll", "version.dll", "shlwapi.dll",
    "crypt32.dll", "bcrypt.dll", "secur32.dll", "iphlpapi.dll", "rpcrt4.dll", "setupapi.dll", "cfgmgr32.dll",
    "dbghelp.dll", "psapi.dll", "userenv.dll", "uxtheme.dll", "dwmapi.dll", "mfplat.dll", "mf.dll",
    "mfreadwrite.dll", "mfuuid.dll", "strmiids.dll", "d3d11.dll", "dxgi.dll", "d3d9.dll", "d3d12.dll",
    "dxva2.dll", "ntdll.dll", "msvcrt.dll", "netapi32.dll", "wldap32.dll", "normaliz.dll", "avrt.dll",
    "ksuser.dll", "propsys.dll", "hid.dll", "opengl32.dll", "glu32.dll", "mpr.dll", "wtsapi32.dll",
    "powrprof.dll", "winspool.drv", "dnsapi.dll", "msimg32.dll", "usp10.dll", "windowscodecs.dll",
    "evr.dll", "d2d1.dll", "dwrite.dll", "cabinet.dll", "msi.dll", "rstrtmgr.dll", "ncrypt.dll",
    "api-ms-win-core-synch-l1-2-0.dll", "wintrust.dll", "imagehlp.dll", "mscoree.dll", "oleacc.dll",
    "avicap32.dll", "bcryptprimitives.dll", "winhttp.dll", "wininet.dll", "urlmon.dll", "credui.dll", "dinput8.dll", "xinput1_4.dll", "setupapi.dll",
}


def imports_of(path):
    pe = pefile.PE(path, fast_load=True)
    pe.parse_data_directories(directories=[
        pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
        pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"]])
    names = set()
    for attr in ("DIRECTORY_ENTRY_IMPORT", "DIRECTORY_ENTRY_DELAY_IMPORT"):
        for e in getattr(pe, attr, []):
            names.add(e.dll.decode().lower())
    pe.close()
    return names


def index(dirs):
    idx = {}
    for d in dirs:
        for f in os.listdir(d):
            if f.lower().endswith(".dll"):
                idx.setdefault(f.lower(), os.path.join(d, f))
    return idx


def closure(roots, avail):
    """Devolve as DLLs (do ambiente) necessárias para os executáveis/módulos dados."""
    need, missing, todo, seen = {}, set(), list(roots), set()
    while todo:
        p = todo.pop()
        if p in seen:
            continue
        seen.add(p)
        for dll in imports_of(p):
            if dll.startswith(("api-ms-win-", "ext-ms-")) or dll in SYSTEM:
                continue
            if dll in avail:
                if dll not in need:
                    need[dll] = avail[dll]
                    todo.append(avail[dll])
            else:
                missing.add((dll, os.path.basename(p)))
    return need, missing


def copytree(src, dst, ignore=None):
    shutil.copytree(src, dst, ignore=ignore, dirs_exist_ok=True)


def main():
    if os.path.exists(OUT):
        shutil.rmtree(OUT)
    rt = os.path.join(OUT, "runtime")
    os.makedirs(os.path.join(rt, "DLLs"))
    avail = index([ENV, LIBBIN])

    # --- Python
    for f in ("python.exe", "pythonw.exe", "python3.dll", "python312.dll", "LICENSE_PYTHON.txt"):
        shutil.copy2(os.path.join(ENV, f), rt)
    skip_pyd = ("_test", "xxlimited", "_ctypes_test", "_msi", "winsound")
    pyds = [f for f in os.listdir(os.path.join(ENV, "DLLs"))
            if f.endswith(".pyd") and not f.startswith(skip_pyd)]
    for f in pyds:
        shutil.copy2(os.path.join(ENV, "DLLs", f), os.path.join(rt, "DLLs"))
    for f in ("tcl86t.dll", "tk86t.dll"):
        shutil.copy2(os.path.join(LIBBIN, f), os.path.join(rt, "DLLs"))
    roots = [os.path.join(rt, x) for x in ("python.exe", "pythonw.exe", "python312.dll")]
    roots += [os.path.join(rt, "DLLs", f) for f in os.listdir(os.path.join(rt, "DLLs"))]
    need, missing = closure(roots, avail)
    for dll, path in need.items():
        dst_dir = os.path.join(rt, "DLLs") if path.startswith(LIBBIN) else rt
        if not os.path.exists(os.path.join(rt, dll)) and not os.path.exists(os.path.join(rt, "DLLs", dll)):
            shutil.copy2(path, dst_dir)
    print("runtime: DLLs extras:", sorted(need))
    if missing:
        print("runtime: FALTANDO:", sorted(missing))

    # stdlib enxuta
    drop = {"test", "idlelib", "turtledemo", "ensurepip", "lib2to3", "pydoc_data", "site-packages",
            "venv", "unittest", "distutils"}

    def ign(d, names):
        out = [n for n in names if n in drop and os.path.samefile(d, os.path.join(ENV, "Lib"))]
        out += [n for n in names if n in ("test", "tests", "idle_test")]
        return out
    copytree(os.path.join(ENV, "Lib"), os.path.join(rt, "Lib"), ignore=ign)
    os.makedirs(os.path.join(rt, "Lib", "site-packages"), exist_ok=True)

    # Tcl/Tk
    for sub in ("tcl8.6", "tk8.6"):
        copytree(os.path.join(ENV, "Library", "lib", sub), os.path.join(rt, "tcl", sub),
                 ignore=shutil.ignore_patterns("demos", "*.lib"))
    tcl8 = os.path.join(ENV, "Library", "lib", "tcl8")
    if os.path.isdir(tcl8):
        copytree(tcl8, os.path.join(rt, "tcl", "tcl8"))

    # --- FFmpeg
    fdir = os.path.join(OUT, "ffmpeg")
    os.makedirs(fdir)
    exes = ("ffmpeg.exe", "ffprobe.exe", "ffplay.exe")  # ffplay: som da prévia
    for f in exes:
        shutil.copy2(os.path.join(LIBBIN, f), fdir)
    need, missing = closure([os.path.join(fdir, f) for f in exes], avail)
    for dll, path in need.items():
        shutil.copy2(path, fdir)
    print("ffmpeg: DLLs:", len(need))
    if missing:
        print("ffmpeg: FALTANDO:", sorted(missing))

    # --- programa
    shutil.copy2(os.path.join(AQUI, "..", "enxuto.pyw"), OUT)
    if os.path.exists(os.path.join(AQUI, "icone.ico")):
        shutil.copy2(os.path.join(AQUI, "icone.ico"), OUT)

    # verificação final: tudo que cada binário importa está presente ao lado dele ou no Windows
    problems = 0
    for root, _, files in os.walk(OUT):
        for f in files:
            if not f.lower().endswith((".exe", ".dll", ".pyd")):
                continue
            p = os.path.join(root, f)
            here = {x.lower() for x in os.listdir(root)}
            if root.startswith(rt):
                here |= {x.lower() for x in os.listdir(rt)} | {x.lower() for x in os.listdir(os.path.join(rt, "DLLs"))}
            try:
                imps = imports_of(p)
            except pefile.PEFormatError:
                continue
            for dll in imps:
                if dll.startswith(("api-ms-win-", "ext-ms-")) or dll in SYSTEM or dll in here:
                    continue
                print("  falta", dll, "para", os.path.relpath(p, OUT))
                problems += 1
    lic = os.path.join(fdir, "licencas")
    os.makedirs(lic, exist_ok=True)
    for f in glob.glob(os.path.join(ENV, "Library", "share", "licenses", "ffmpeg*", "*")) + \
            glob.glob(os.path.join(os.path.expanduser("~"), ".cache", "rattler", "cache", "pkgs", "ffmpeg-*gpl*", "info", "licenses", "*")):
        shutil.copy2(f, lic)
    print("verificação:", "OK" if not problems else f"{problems} problema(s)")
    return problems


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
