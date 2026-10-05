#!/usr/bin/env python3
"""
Interpolacion a 60 fps con rife-ncnn-vulkan (Vulkan, sin PyTorch: esquiva el
sm_120 de la 5060 Ti). Modelo rife-v4.6, que acepta timestep arbitrario, asi
que 24->60 va directo sin pasar por 96 y conformar.

REGLA QUE GOBIERNA ESTE SCRIPT: **nunca interpolar a traves de un corte duro.**
Si el clip tiene varios planos, RIFE inventaria frames entre el ultimo fotograma
de un plano y el primero del siguiente y sale un morphing asqueroso. Por eso
--cortes trocea primero, interpola cada segmento POR SEPARADO y concatena luego.

Un unico encode final H.264 CRF 16: nada de recodificar el mismo material varias
veces. Los intermedios son PNG, sin perdida.

Uso:
  python3 rife_60fps.py clip.mp4                    # un solo plano
  python3 rife_60fps.py clip.mp4 --cortes 5,10      # cortes duros en s5 y s10
  python3 rife_60fps.py clip.mp4 --fps 60 --crf 16
"""
import argparse, shutil, subprocess, sys, tempfile
from pathlib import Path

import av
import imageio_ffmpeg

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
RIFE_DIR = Path.home() / "tools/rife-ncnn-vulkan-20221029-ubuntu"
RIFE = RIFE_DIR / "rife-ncnn-vulkan"
MODELO = "rife-v4.6"          # los v4.x aceptan -n arbitrario (timestep libre)
TMP = Path(tempfile.gettempdir()) / "rife_tmp"


def frames_y_fps(mp4: Path):
    """Vuelca todos los frames a PNG. ffprobe no existe aqui: se lee con PyAV."""
    d = TMP / (mp4.stem + "_src")
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    with av.open(str(mp4)) as c:
        st = c.streams.video[0]
        fps = float(st.average_rate)
        n = 0
        for f in c.decode(st):
            f.to_image().save(d / f"{n:08d}.png")
            n += 1
    print(f"    {mp4.name}: {n} frames a {fps:g} fps")
    return d, fps, n


def trocea(dir_src: Path, n: int, fps: float, cortes):
    """Devuelve [(dir, n_frames)] por segmento, partiendo en los cortes duros."""
    limites = [0] + [int(round(c * fps)) for c in cortes] + [n]
    segs = []
    todos = sorted(dir_src.glob("*.png"))
    for i, (a, b) in enumerate(zip(limites, limites[1:])):
        if b <= a:
            continue
        d = dir_src.parent / f"{dir_src.name}_seg{i}"
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
        for j, f in enumerate(todos[a:b]):
            (d / f"{j:08d}.png").symlink_to(f)
        segs.append((d, b - a))
        print(f"    segmento {i}: frames {a}-{b-1} ({b-a})")
    return segs


def interpola(d_in: Path, n_in: int, fps_in: float, fps_out: int):
    objetivo = max(2, int(round(n_in * fps_out / fps_in)))
    d_out = Path(str(d_in) + "_60")
    if d_out.exists():
        shutil.rmtree(d_out)
    d_out.mkdir(parents=True)
    print(f"    RIFE {d_in.name}: {n_in} -> {objetivo} frames")
    r = subprocess.run([str(RIFE), "-m", MODELO, "-i", str(d_in),
                        "-o", str(d_out), "-n", str(objetivo)],
                       cwd=str(RIFE_DIR), capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"[X] RIFE fallo:\n{r.stderr[-1500:]}")
    return d_out, objetivo


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mp4")
    ap.add_argument("--cortes", default="",
                    help="segundos de los cortes duros, ej '5,10'. Vacio = un solo plano")
    ap.add_argument("--fps", type=int, default=60)
    ap.add_argument("--crf", type=int, default=16)
    ap.add_argument("--out")
    a = ap.parse_args()

    src = Path(a.mp4)
    if not src.exists():
        sys.exit(f"[X] no existe {src}")
    if not RIFE.exists():
        sys.exit(f"[X] no esta el binario de RIFE en {RIFE}")
    TMP.mkdir(parents=True, exist_ok=True)

    print(f"=== {src.name} -> {a.fps} fps ===")
    d_src, fps, n = frames_y_fps(src)
    cortes = [float(x) for x in a.cortes.split(",") if x.strip()]
    segs = trocea(d_src, n, fps, cortes) if cortes else [(d_src, n)]
    if cortes:
        print(f"    {len(segs)} planos: se interpolan por separado "
              f"(nunca a traves de un corte)")

    # se juntan los frames de todos los segmentos ya interpolados, en orden
    d_fin = TMP / (src.stem + "_final")
    if d_fin.exists():
        shutil.rmtree(d_fin)
    d_fin.mkdir(parents=True)
    k = 0
    for d_in, n_in in segs:
        d_out, _ = interpola(d_in, n_in, fps, a.fps)
        for f in sorted(d_out.glob("*.png")):
            (d_fin / f"{k:08d}.png").symlink_to(f.resolve())
            k += 1
    print(f"    total {k} frames")

    dst = Path(a.out) if a.out else src.with_name(src.stem + f"_{a.fps}fps.mp4")
    # UN SOLO encode: PNG sin perdida -> H.264 CRF 16
    cmd = [FFMPEG, "-y", "-framerate", str(a.fps), "-i", str(d_fin / "%08d.png"),
           "-c:v", "libx264", "-preset", "slow", "-crf", str(a.crf),
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(dst)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"[X] ffmpeg fallo:\n{r.stderr[-1500:]}")
    print(f"LISTO -> {dst} ({dst.stat().st_size/1024/1024:.1f} MB)")


if __name__ == "__main__":
    main()
