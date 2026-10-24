"""Shared AWS helpers for FLASHCROWD infra (EC2 only; default VPC assumed)."""
import os, pathlib
import boto3
from dotenv import load_dotenv

load_dotenv()
REGIONS = {"LDN": "eu-west-2", "NJ": "us-east-1", "TYO": "ap-northeast-1"}   # CONTRACTS.md 3.1 (v1.1)
ROOT = pathlib.Path(__file__).resolve().parent.parent
KEY_NAME = "flashcrowd-pc1"
SG_NAME = "flashcrowd-web"
UBUNTU_OWNER = "099720109477"   # Canonical
TAG = {"Key": "project", "Value": "flashcrowd"}


def ec2(region):
    return boto3.client("ec2", region_name=region)


def ubuntu_ami(region):
    imgs = ec2(region).describe_images(Owners=[UBUNTU_OWNER], Filters=[
        {"Name": "name", "Values": ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]},
        {"Name": "state", "Values": ["available"]}])["Images"]
    return max(imgs, key=lambda i: i["CreationDate"])["ImageId"]


def ensure_keypair(region):
    c = ec2(region)
    if not c.describe_key_pairs(Filters=[{"Name": "key-name", "Values": [KEY_NAME]}])["KeyPairs"]:
        c.import_key_pair(KeyName=KEY_NAME, PublicKeyMaterial=(ROOT / "secrets/fc_ssh.pub").read_bytes(),
                          TagSpecifications=[{"ResourceType": "key-pair", "Tags": [TAG]}])
    return KEY_NAME


def ensure_sg(region, ports=(22, 80, 443)):
    c = ec2(region)
    found = c.describe_security_groups(Filters=[{"Name": "group-name", "Values": [SG_NAME]}])["SecurityGroups"]
    if found:
        return found[0]["GroupId"]
    vpc = c.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"][0]["VpcId"]
    gid = c.create_security_group(GroupName=SG_NAME, Description="FLASHCROWD web + ssh", VpcId=vpc,
                                  TagSpecifications=[{"ResourceType": "security-group", "Tags": [TAG]}])["GroupId"]
    c.authorize_security_group_ingress(GroupId=gid, IpPermissions=[
        {"IpProtocol": "tcp", "FromPort": p, "ToPort": p, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]} for p in ports])
    return gid
