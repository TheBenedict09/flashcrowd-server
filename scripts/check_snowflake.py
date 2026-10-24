"""Connectivity check: signs in as FC_APP with the private key and lists FLASHCROWD.CORE tables."""
import os
from dotenv import load_dotenv
from cryptography.hazmat.primitives import serialization
import snowflake.connector

load_dotenv()
with open(os.environ["SNOWFLAKE_PRIVATE_KEY_PATH"], "rb") as f:
    pkey = serialization.load_pem_private_key(f.read(), password=None).private_bytes(
        serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())

conn = snowflake.connector.connect(
    account=os.environ["SNOWFLAKE_ACCOUNT"], user=os.environ["SNOWFLAKE_USER"], private_key=pkey,
    role=os.environ["SNOWFLAKE_ROLE"], warehouse=os.environ["SNOWFLAKE_WAREHOUSE"],
    database=os.environ["SNOWFLAKE_DATABASE"], schema=os.environ["SNOWFLAKE_SCHEMA"])
cur = conn.cursor()
print("Connected as:", cur.execute("SELECT CURRENT_USER(), CURRENT_ROLE(), CURRENT_WAREHOUSE()").fetchone())
tables = [r[1] for r in cur.execute("SHOW TABLES IN SCHEMA FLASHCROWD.CORE").fetchall()]
print(f"{len(tables)} tables:", ", ".join(sorted(tables)))
cur.execute("INSERT INTO INFRA_MEASUREMENTS (ts, kind, note) SELECT SYSDATE(), 'check', 'write test'")
cur.execute("DELETE FROM INFRA_MEASUREMENTS WHERE kind = 'check'")
print("Write test: OK")
conn.close()
