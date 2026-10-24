import os, time, httpx
H = {"Authorization": f"Bearer {os.environ['VULTR_API_KEY']}"}
API = "https://api.vultr.com/v2"
def boot_once(region, plan, os_id, user_data_b64):
    t0 = time.time()
    r = httpx.post(f"{API}/instances", headers=H, json={"region":region,"plan":plan,"os_id":os_id,
                   "label":f"fc-bench-{region}","user_data":user_data_b64}).json()["instance"]
    iid = r["id"]
    try:
        while True:
            inst = httpx.get(f"{API}/instances/{iid}", headers=H).json()["instance"]
            ip = inst.get("main_ip")
            if inst.get("server_status") == "ok" and ip and ip != "0.0.0.0":
                try:
                    if httpx.get(f"http://{ip}/wiki/healthz.html", timeout=3).status_code == 200:
                        return time.time() - t0
                except Exception: pass
            time.sleep(5)
    finally:
        httpx.delete(f"{API}/instances/{iid}", headers=H)   # always clean up
