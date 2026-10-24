"""Snowflake connection helper (key-pair auth as FC_APP). Usage: `with connect() as conn: ...`"""
import os, pathlib
from dotenv import load_dotenv
from cryptography.hazmat.primitives import serialization
import snowflake.connector

ROOT = pathlib.Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def connect():
    kp = pathlib.Path(os.environ["SNOWFLAKE_PRIVATE_KEY_PATH"])
    kp = kp if kp.is_absolute() else ROOT / kp
    pkey = serialization.load_pem_private_key(kp.read_bytes(), password=None).private_bytes(
        serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"], user=os.environ["SNOWFLAKE_USER"], private_key=pkey,
        role=os.environ["SNOWFLAKE_ROLE"], warehouse=os.environ["SNOWFLAKE_WAREHOUSE"],
        database=os.environ["SNOWFLAKE_DATABASE"], schema=os.environ["SNOWFLAKE_SCHEMA"])
