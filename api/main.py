"""FLASHCROWD API — skeleton. Routes per CONTRACTS.md §3.5; mocks first, real later."""
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

CONTRACTS_VERSION = "1.0"
app = FastAPI(title="FLASHCROWD API")
app.add_middleware(CORSMiddleware, allow_origins=[os.getenv("FRONTEND_ORIGIN", "*")],
                   allow_methods=["*"], allow_headers=["*"])

@app.get("/api/health")
def health():
    return {"ok": True, "version": CONTRACTS_VERSION}
