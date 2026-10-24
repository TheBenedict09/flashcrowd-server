"""Capacity C (CONTRACTS 3.3): highest req/s one edge server sustains with p95 < 200 ms.

Launches, in London: target (t3.micro, same cloud-init as the boot bench) + load generator (c6i.large, hey).
Runs hey at rising concurrency against /wiki/Mount_Fuji.html, then terminates both (always).
  python infra/capacity_test.py [--secs 30] [--target-type t3.micro]
Needs: secrets/fc_ssh (SSH to the load generator).
"""
import argparse, re, subprocess, sys, time, pathlib, json
import httpx
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from aws_common import REGIONS, ROOT, TAG, ec2, ubuntu_ami, ensure_keypair, ensure_sg

LOADGEN_INIT = "#cloud-config\npackages: [hey]\nruncmd:\n  - touch /home/ubuntu/READY\n"
LEVELS = [10, 25, 50, 100, 200, 400, 800]


def launch(c, region, itype, name, user_data):
    iid = c.run_instances(ImageId=ubuntu_ami(region), InstanceType=itype, MinCount=1, MaxCount=1,
        KeyName=ensure_keypair(region), SecurityGroupIds=[ensure_sg(region)], UserData=user_data,
        TagSpecifications=[{"ResourceType": "instance", "Tags": [TAG, {"Key": "Name", "Value": name}]}]
    )["Instances"][0]["InstanceId"]
    time.sleep(3)
    c.get_waiter("instance_running").wait(InstanceIds=[iid])
    inst = c.describe_instances(InstanceIds=[iid])["Reservations"][0]["Instances"][0]
    return iid, inst["PublicIpAddress"], inst["PrivateIpAddress"]


def ssh(ip, cmd, timeout=300):
    return subprocess.run(["ssh", "-i", str(ROOT / "secrets/fc_ssh"), "-o", "StrictHostKeyChecking=no",
                           "-o", "ConnectTimeout=10", f"ubuntu@{ip}", cmd],
                          capture_output=True, text=True, timeout=timeout).stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--secs", type=int, default=30)
    ap.add_argument("--target-type", default="t3.micro")
    a = ap.parse_args()
    region = REGIONS["LDN"]; c = ec2(region); ids = []
    try:
        tid, tip, tpriv = launch(c, region, a.target_type, "fc-cap-target", (ROOT / "infra/cloud-init-nginx.yaml").read_text()); ids.append(tid)
        lid, lip, _ = launch(c, region, "c6i.large", "fc-cap-loadgen", LOADGEN_INIT); ids.append(lid)
        for _ in range(100):
            try:
                if httpx.get(f"http://{tip}/wiki/healthz.html", timeout=3).status_code == 200: break
            except httpx.HTTPError: pass
            time.sleep(5)
        for _ in range(60):
            if "yes" in ssh(lip, "test -f /home/ubuntu/READY && which hey >/dev/null && echo yes", 30): break
            time.sleep(5)
        size = ssh(lip, f"curl -s -o /dev/null -w '%{{size_download}}' http://{tpriv}/wiki/Mount_Fuji.html", 30)
        print(f"target {a.target_type} {tip} (private {tpriv}); page size {size} bytes", flush=True)
        results = []
        for conc in LEVELS:
            out = ssh(lip, f"hey -z {a.secs}s -c {conc} http://{tpriv}/wiki/Mount_Fuji.html", a.secs + 120)
            rps = float(re.search(r"Requests/sec:\s+([\d.]+)", out).group(1))
            p95 = float(re.search(r"95%+ in ([\d.]+) secs", out).group(1)) * 1000
            codes = re.findall(r"\[(\d{3})\]\s+(\d+) responses", out)
            errs = sum(int(n) for code, n in codes if code != "200")
            results.append({"c": conc, "rps": rps, "p95_ms": p95, "non200": errs})
            print(f"c={conc:4d}  {rps:9.1f} req/s  p95 {p95:7.1f} ms  non-200 {errs}", flush=True)
            if p95 > 400: break
        ok = [r for r in results if r["p95_ms"] < 200 and r["non200"] == 0]
        C = max(r["rps"] for r in ok) if ok else 0
        print(f"== C = {C:.0f} req/s (p95 < 200 ms)")
        (ROOT / "infra/capacity_results.json").write_text(json.dumps(
            {"target": a.target_type, "page_bytes": int(size or 0), "secs": a.secs, "C": C, "levels": results}, indent=2))
        try:
            sys.path.insert(0, str(ROOT)); from pipeline.sf import connect
            with connect() as conn:
                conn.cursor().execute("INSERT INTO INFRA_MEASUREMENTS (ts, kind, region, value, unit, note) "
                    "VALUES (SYSDATE(), 'capacity', 'LDN', %s, 'req/s', %s)", (C, f"{a.target_type}, nginx static, p95<200ms, hey {a.secs}s"))
            print("saved to Snowflake")
        except Exception as e:
            print("Snowflake save skipped:", e)
    finally:
        if ids: c.terminate_instances(InstanceIds=ids); print("terminated", ids)


if __name__ == "__main__":
    main()
