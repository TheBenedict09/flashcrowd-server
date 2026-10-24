"""Measure EC2 boot-to-serving time per region (CONTRACTS 3.3 constant B[r]).

Usage: python infra/boot_bench.py --runs 5 [--regions LDN NJ TYO] [--type t3.micro]
Each run: launch -> poll http://<ip>/wiki/healthz.html until 200 -> record seconds -> terminate (always).
"""
import argparse, statistics, sys, time, pathlib
import httpx
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from aws_common import REGIONS, ROOT, TAG, ec2, ubuntu_ami, ensure_keypair, ensure_sg

USER_DATA = (ROOT / "infra/cloud-init-nginx.yaml").read_text()


def boot_once(label, itype):
    region = REGIONS[label]
    c = ec2(region)
    t0 = time.time()
    iid = c.run_instances(ImageId=ubuntu_ami(region), InstanceType=itype, MinCount=1, MaxCount=1,
                          KeyName=ensure_keypair(region), SecurityGroupIds=[ensure_sg(region)], UserData=USER_DATA,
                          TagSpecifications=[{"ResourceType": "instance",
                                              "Tags": [TAG, {"Key": "Name", "Value": f"fc-bench-{label}"}]}]
                          )["Instances"][0]["InstanceId"]
    try:
        while time.time() - t0 < 900:
            inst = c.describe_instances(InstanceIds=[iid])["Reservations"][0]["Instances"][0]
            ip = inst.get("PublicIpAddress")
            if ip:
                try:
                    if httpx.get(f"http://{ip}/wiki/healthz.html", timeout=3).status_code == 200:
                        return time.time() - t0
                except httpx.HTTPError:
                    pass
            time.sleep(3)
        raise TimeoutError(f"{label}: not serving after 15 min")
    finally:
        c.terminate_instances(InstanceIds=[iid])   # always clean up


def save(rows):
    """Write boot measurements to Snowflake INFRA_MEASUREMENTS (best effort)."""
    try:
        sys.path.insert(0, str(ROOT))
        from pipeline.sf import connect
        with connect() as conn:
            conn.cursor().executemany(
                "INSERT INTO INFRA_MEASUREMENTS (ts, kind, region, value, unit, note) "
                "VALUES (SYSDATE(), 'boot', %s, %s, 'min', %s)", rows)
        print(f"saved {len(rows)} rows to Snowflake")
    except Exception as e:
        print("Snowflake save skipped:", e)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--regions", nargs="+", default=list(REGIONS))
    ap.add_argument("--type", default="t3.micro")
    a = ap.parse_args()
    rows = []
    for label in a.regions:
        mins = []
        for i in range(a.runs):
            m = boot_once(label, a.type) / 60
            mins.append(m); rows.append((label, round(m, 3), f"{a.type} run {i+1}"))
            print(f"{label} run {i+1}: {m:.2f} min", flush=True)
        print(f"== {label} median B = {statistics.median(mins):.2f} min")
    save(rows)
