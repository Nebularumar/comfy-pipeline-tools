#!/usr/bin/env python3
"""
Kling (image-to-video) via fal.ai. Misma mecanica
de subida/cola/facturacion, pero para los endpoints de Kling y con la duracion
libre (v3 acepta de 3 a 15 segundos).

Uso:
  python3 kling_video.py foto.png "prompt" [--dur 5] [--tier pro] [--nombre baile]
  python3 kling_video.py foto.png "prompt" --dur 3 --repite 5   # 5 clips de 3s

Tiers (sin parametro de resolucion: la fija el endpoint):
  standard  720p    pro  1080p    4k  2160p

OJO: generate_audio viene en True por defecto y mete una pista inventada que no
usamos; aqui va siempre en False.
"""
import argparse, json, mimetypes, sys, tempfile, time
from pathlib import Path

import requests
from PIL import Image

KEY_FILE = Path.home() / ".fal_key"
OUT_DIR = Path.home() / "ComfyUI/output/test_kling"
TMP_DIR = Path(tempfile.gettempdir()) / "kling_tmp"
# 1536 dejaba la cara de un plano general en ~113 px y Kling, al pedirle un
# primer plano, se inventaba la piel y le metia surcos de anciano (31-ago-2026).
# Subiendo el lado largo la cara le llega con detalle real.
MAX_SIDE = 2560
REST = "https://rest.alpha.fal.ai"
QUEUE = "https://queue.fal.run"

TIERS = {
    "standard": "fal-ai/kling-video/v3/standard/image-to-video",   # 720p
    "pro":      "fal-ai/kling-video/v3/pro/image-to-video",        # 1080p
    "4k":       "fal-ai/kling-video/v3/4k/image-to-video",         # 2160p
}
NEG = ("blur, distort, low quality, deformed hands, extra fingers, extra limbs, "
       "warped face, morphing body, floating feet, sliding feet, "
       "deep wrinkles, heavy nasolabial folds, creased skin, aged skin, "
       "leathery skin, crow's feet, different face")


def leer_key() -> str:
    if not KEY_FILE.exists():
        sys.exit(f"[X] Falta {KEY_FILE}.")
    k = KEY_FILE.read_text().strip()
    if not k:
        sys.exit(f"[X] {KEY_FILE} esta vacio.")
    return k


def cabeceras(key: str) -> dict:
    return {"Authorization": f"Key {key}", "Content-Type": "application/json"}


def preparar_imagen(src: Path) -> Path:
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    dst = TMP_DIR / (src.stem[:40] + "_in.jpg")
    im = Image.open(src).convert("RGB")
    orig = im.size
    im.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    im.save(dst, quality=93, subsampling=0)
    print(f"    imagen: {orig[0]}x{orig[1]} -> {im.size[0]}x{im.size[1]} "
          f"({dst.stat().st_size/1024:.0f} KB)")
    return dst


def subir(path: Path, key: str) -> str:
    ctype = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    r = requests.post(f"{REST}/storage/upload/initiate?storage_type=fal-cdn-v3",
                      headers=cabeceras(key),
                      json={"content_type": ctype, "file_name": path.name}, timeout=60)
    r.raise_for_status()
    d = r.json()
    put = requests.put(d["upload_url"], data=path.read_bytes(),
                       headers={"Content-Type": ctype}, timeout=300)
    put.raise_for_status()
    print(f"    subida OK")
    return d["file_url"]


def genera(img: Path, prompt: str, dur: str, tier: str, nombre: str, key: str,
           shots: list | None = None) -> dict:
    """shots: lista [{"prompt":..., "duration":"5"}, ...] -> multi-shot.
    Kling encadena los planos dentro de UNA sola generacion, manteniendo
    personaje e iluminacion entre cambios de camara. Maximo 15s en total."""
    endpoint = TIERS[tier]
    if shots:
        dur = str(sum(int(s.get("duration", 5)) for s in shots))
        print(f"\n=== {nombre}  [{tier} -> {endpoint}]  multi-shot "
              f"{len(shots)} planos = {dur}s")
        for i, s in enumerate(shots, 1):
            print(f"    plano {i} ({s.get('duration', 5)}s): {s['prompt'][:70]}...")
    else:
        print(f"\n=== {nombre}  [{tier} -> {endpoint}]  {dur}s")
    img_url = subir(preparar_imagen(img), key)
    payload = {
        "prompt": prompt,
        "start_image_url": img_url,
        "duration": str(dur),
        "negative_prompt": NEG,
        "generate_audio": False,
        "cfg_scale": 0.5,
    }
    if shots:
        # 'prompt' y 'multi_prompt' son EXCLUYENTES (422 si van los dos).
        payload.pop("prompt")
        payload["multi_prompt"] = shots
        payload["shot_type"] = "customize"
    t0 = time.time()
    r = requests.post(f"{QUEUE}/{endpoint}", headers=cabeceras(key), json=payload, timeout=120)
    if r.status_code >= 400:
        print(f"    [X] HTTP {r.status_code}: {r.text[:600]}")
        return {"nombre": nombre, "error": r.text[:600]}
    enc = r.json()
    req_id = enc["request_id"]
    print(f"    request_id: {req_id}")
    base = enc.get("response_url") or f"{QUEUE}/{endpoint}/requests/{req_id}"
    status_url = enc.get("status_url") or f"{base}/status"
    prev = None
    while True:
        time.sleep(3)
        st = requests.get(status_url, headers=cabeceras(key), timeout=60).json()
        if st.get("status") != prev:
            print(f"    [{time.time()-t0:6.1f}s] {st.get('status')}"
                  + (f"  (cola: {st.get('queue_position')})"
                     if st.get("queue_position") is not None else ""))
            prev = st.get("status")
        if st.get("status") == "COMPLETED":
            break
        if time.time() - t0 > 1200:
            return {"nombre": nombre, "error": "timeout 20 min"}

    res = requests.get(base, headers=cabeceras(key), timeout=120)
    if res.status_code >= 400:
        print(f"    [X] HTTP {res.status_code} al recoger: {res.text[:900]}")
        return {"nombre": nombre, "error": res.text[:900], "request_id": req_id}
    data = res.json()
    cobro = {k: v for k, v in res.headers.items() if k.lower().startswith("x-fal")}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    destino = OUT_DIR / f"{nombre}_kling_{tier}_{dur}s.mp4"
    with requests.get(data["video"]["url"], stream=True, timeout=900) as v:
        v.raise_for_status()
        with open(destino, "wb") as f:
            for c in v.iter_content(1 << 20):
                f.write(c)
    tardado = time.time() - t0
    print(f"    LISTO en {tardado:.1f}s -> {destino} "
          f"({destino.stat().st_size/1024/1024:.1f} MB)")
    if cobro:
        print(f"    facturacion: {cobro}")
    return {"nombre": nombre, "endpoint": endpoint, "dur": dur, "segundos": round(tardado, 1),
            "mp4": str(destino), "mb": round(destino.stat().st_size/1024/1024, 2),
            "facturacion": cobro, "request_id": req_id}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("imagen")
    ap.add_argument("prompt")
    ap.add_argument("--dur", default="5")
    ap.add_argument("--tier", default="pro", choices=list(TIERS))
    ap.add_argument("--nombre", default="clip")
    ap.add_argument("--repite", type=int, default=1, help="numero de clips (semillas distintas)")
    ap.add_argument("--shots", help="JSON con [{prompt,duration},...] -> multi-shot")
    a = ap.parse_args()

    key = leer_key()
    img = Path(a.imagen)
    if not img.exists():
        sys.exit(f"[X] no existe {img}")

    shots = json.loads(Path(a.shots).read_text()) if a.shots else None
    res = []
    for i in range(a.repite):
        n = a.nombre if a.repite == 1 else f"{a.nombre}_{i+1:02d}"
        res.append(genera(img, a.prompt, a.dur, a.tier, n, key, shots))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    inf = OUT_DIR / "informe.json"
    prev = json.loads(inf.read_text()) if inf.exists() else []
    inf.write_text(json.dumps(prev + res, indent=2, ensure_ascii=False))
    print(f"\ninforme -> {inf}")


if __name__ == "__main__":
    main()
