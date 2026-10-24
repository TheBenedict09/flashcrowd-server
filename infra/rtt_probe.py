"""Inter-region RTT probes (roadmap §6 D4) — real numbers for the map.

  python infra/rtt_probe.py launch    # one t3.nano per region; each measures TCP connect time to the
                                      # other two every minute (≈ 1 round trip) into /var/www/html/rtt.log
  python infra/rtt_probe.py collect   # (timer on fc-core, every 5 min) new lines -> INFRA_MEASUREMENTS kind='rtt'
  python infra/rtt_probe.py summary   # median ms per pair
  python infra/rtt_probe.py teardown
"""
import json, subprocess, sys, time, pathlib
import httpx
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from aws_common import REGIONS, ROOT, TAG, ec2, ubuntu_ami, ensure_keypair, ensure_sg

BOXES = ROOT / "infra/rtt_boxes.json"
STATE = pathlib.Path("/home/ubuntu/data/rtt_offsets.json") if pathlib.Path("/home/ubuntu/data").exists() else ROOT / "secrets/rtt_offsets.json"
INIT = """#cloud-config
packages: [nginx, curl]
write_files:
  - path: /usr/local/bin/fc-rtt.sh
    permissions: "0755"
    content: |
      #!/bin/bash
      SELF=$(cat /etc/fc-self 2>/dev/null) || exit 0
      [ -f /etc/fc-peers ] || exit 0
      while read label ip; do
        t=$(curl -s -o /dev/null -w '%{time_connect}' --max-time 5 "http://$ip/ping.txt")
        echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $SELF $label $t" >> /var/www/html/rtt.log
      done < /etc/fc-peers
  - path: /etc/cron.d/fc-rtt
    content: |
      * * * * * root /usr/local/bin/fc-rtt.sh
runcmd:
  - [bash, -c, "echo pong > /var/www/html/ping.txt && touch /var/www/html/rtt.log && systemctl enable --now nginx"]
"""


def ssh(ip, cmd):
    return subprocess.run(["ssh", "-i", str(ROOT / "secrets/fc_ssh"), "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
                           f"ubuntu@{ip}", cmd], capture_output=True, text=True, timeout=60)


def launch():
    boxes = {}
    for label, region in REGIONS.items():
        c = ec2(region)
        live = c.describe_instances(Filters=[{"Name": "tag:Name", "Values": [f"fc-rtt-{label}"]},
                                             {"Name": "instance-state-name", "Values": ["pending", "running"]}])["Reservations"]
        iid = live[0]["Instances"][0]["InstanceId"] if live else c.run_instances(
            ImageId=ubuntu_ami(region), InstanceType="t3.nano", MinCount=1, MaxCount=1, KeyName=ensure_keypair(region),
            SecurityGroupIds=[ensure_sg(region)], UserData=INIT,
            TagSpecifications=[{"ResourceType": "instance", "Tags": [TAG, {"Key": "Name", "Value": f"fc-rtt-{label}"}]}]
        )["Instances"][0]["InstanceId"]
        time.sleep(3); c.get_waiter("instance_running").wait(InstanceIds=[iid])
        ip = c.describe_instances(InstanceIds=[iid])["Reservations"][0]["Instances"][0]["PublicIpAddress"]
        boxes[label] = {"region": region, "id": iid, "ip": ip}
    BOXES.write_text(json.dumps(boxes, indent=2))
    for label, b in boxes.items():          # wait for nginx, then tell each box who it is + its peers
        for _ in range(60):
            try:
                if httpx.get(f"http://{b['ip']}/ping.txt", timeout=3).status_code == 200: break
            except httpx.HTTPError: pass
            time.sleep(5)
        peers = "\n".join(f"{l} {p['ip']}" for l, p in boxes.items() if l != label)
        ssh(b["ip"], f"echo {label} | sudo tee /etc/fc-self >/dev/null && printf '{peers}\\n' | sudo tee /etc/fc-peers >/dev/null")
        print(label, b["ip"], "configured")


def collect():
    boxes = json.loads(BOXES.read_text())
    offs = json.loads(STATE.read_text()) if STATE.exists() else {}
    rows = []
    for label, b in boxes.items():
        try:
            lines = httpx.get(f"http://{b['ip']}/rtt.log", timeout=10).text.splitlines()
        except httpx.HTTPError as e:
            print(label, "unreachable:", e); continue
        for ln in lines[offs.get(label, 0):]:
            p = ln.split()
            if len(p) == 4 and p[3] not in ("", "0.000000"):
                rows.append((p[0].replace("T", " ").rstrip("Z"), p[1], p[2], round(float(p[3]) * 1000, 2)))
        offs[label] = len(lines)
    if rows:
        sys.path.insert(0, str(ROOT)); from pipeline.sf import connect
        with connect() as conn:
            conn.cursor().executemany("INSERT INTO INFRA_MEASUREMENTS (ts, kind, region, peer, value, unit, note) "
                                      "VALUES (%s, 'rtt', %s, %s, %s, 'ms', 'tcp connect')", rows)
    STATE.write_text(json.dumps(offs))
    print(f"loaded {len(rows)} rtt rows")


def summary():
    sys.path.insert(0, str(ROOT)); from pipeline.sf import connect
    with connect() as conn:
        for r in conn.cursor().execute("SELECT LEAST(region,peer) a, GREATEST(region,peer) b, MEDIAN(value), COUNT(*) "
                                       "FROM INFRA_MEASUREMENTS WHERE kind='rtt' GROUP BY 1,2 ORDER BY 1,2").fetchall():
            print(f"{r[0]}–{r[1]}: {float(r[2]):.1f} ms  (n={r[3]})")


def teardown():
    for label, b in json.loads(BOXES.read_text()).items():
        ec2(b["region"]).terminate_instances(InstanceIds=[b["id"]]); print("terminated", label)


if __name__ == "__main__":
    {"launch": launch, "collect": collect, "summary": summary, "teardown": teardown}[sys.argv[1]]()
