"""Baixa do conda-forge o Python, o Tk e o FFmpeg para Windows (win-64) na pasta winenv/."""
import asyncio, os, rattler
from rattler import solve, install, Platform
async def main():
    recs = await solve(
        sources=["conda-forge"],
        specs=["python 3.12.*", "tk", "ffmpeg 8.* *gpl*"],
        platforms=[Platform("win-64"), Platform("noarch")],
        virtual_packages=[],
    )
    print(len(recs), "pacotes")
    for r in recs:
        if r.name.normalized in ("python","ffmpeg","tk","x264","x265"): print(" ", r.name.normalized, r.version, r.build)
    await install(recs, target_prefix=os.path.join(os.path.dirname(os.path.abspath(__file__)), "winenv"), platform=Platform("win-64"), execute_link_scripts=False)
asyncio.run(main())
