"""Launch fc-core (t3.medium, London) with an Elastic IP. Idempotent: reuses a running fc-core."""
import sys, pathlib, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from aws_common import REGIONS, ROOT, TAG, ec2, ubuntu_ami, ensure_keypair, ensure_sg

region = REGIONS["LDN"]; c = ec2(region)
live = c.describe_instances(Filters=[{"Name": "tag:Name", "Values": ["fc-core"]},
                                     {"Name": "instance-state-name", "Values": ["pending", "running"]}])["Reservations"]
if live:
    iid = live[0]["Instances"][0]["InstanceId"]
else:
    iid = c.run_instances(ImageId=ubuntu_ami(region), InstanceType="t3.medium", MinCount=1, MaxCount=1,
        KeyName=ensure_keypair(region), SecurityGroupIds=[ensure_sg(region)],
        UserData=(ROOT / "infra/cloud-init-core.yaml").read_text(),
        BlockDeviceMappings=[{"DeviceName": "/dev/sda1", "Ebs": {"VolumeSize": 40, "VolumeType": "gp3"}}],
        TagSpecifications=[{"ResourceType": "instance", "Tags": [TAG, {"Key": "Name", "Value": "fc-core"}]}]
    )["Instances"][0]["InstanceId"]
c.get_waiter("instance_running").wait(InstanceIds=[iid])
addrs = c.describe_addresses(Filters=[{"Name": "tag:Name", "Values": ["fc-core"]}])["Addresses"]
if addrs:
    ip = addrs[0]["PublicIp"]
    if addrs[0].get("InstanceId") != iid:
        c.associate_address(AllocationId=addrs[0]["AllocationId"], InstanceId=iid)
else:
    a = c.allocate_address(Domain="vpc", TagSpecifications=[{"ResourceType": "elastic-ip",
                           "Tags": [TAG, {"Key": "Name", "Value": "fc-core"}]}])
    c.associate_address(AllocationId=a["AllocationId"], InstanceId=iid); ip = a["PublicIp"]
print(iid, ip)
