#!/usr/bin/env python3
"""Cliente ComfyUI: envia workflow API, espera, devuelve imagenes generadas."""
import json, urllib.request, urllib.parse, time, sys, uuid

HOST="http://127.0.0.1:8188"

def post_prompt(wf):
    cid=str(uuid.uuid4())
    data=json.dumps({"prompt":wf,"client_id":cid}).encode()
    req=urllib.request.Request(f"{HOST}/prompt",data=data,headers={"Content-Type":"application/json"})
    try:
        r=json.loads(urllib.request.urlopen(req,timeout=30).read())
    except urllib.error.HTTPError as e:
        print("ERROR /prompt:",e.read().decode()[:2000]); sys.exit(1)
    return r["prompt_id"]

def wait(pid, timeout=1800):
    t0=time.time()
    while time.time()-t0<timeout:
        try:
            h=json.loads(urllib.request.urlopen(f"{HOST}/history/{pid}",timeout=15).read())
        except Exception:
            time.sleep(2); continue
        if pid in h:
            st=h[pid]["status"]
            if st.get("completed") or st.get("status_str")=="success":
                outs=[]
                for nid,no in h[pid]["outputs"].items():
                    for im in no.get("images",[]):
                        outs.append(im)
                return True, outs, h[pid]["status"]
            if st.get("status_str")=="error":
                return False, [], h[pid]["status"]
        time.sleep(3)
    return False, [], {"status_str":"timeout"}

if __name__=="__main__":
    wf=json.load(open(sys.argv[1]))
    pid=post_prompt(wf)
    print("prompt_id:",pid,flush=True)
    ok,outs,status=wait(pid)
    print("OK" if ok else "FALLO", "| status:", status.get("status_str"))
    for o in outs: print("  IMG:", o)
    if not ok: sys.exit(2)
