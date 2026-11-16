from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import rankings, swings, system, wallets

app = FastAPI(title="AlphaScope API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000"], allow_methods=["GET"],
                   allow_headers=["*"])
app.include_router(rankings.router)
app.include_router(wallets.router)
app.include_router(system.router)
app.include_router(swings.router)


@app.get("/healthz")
def healthz():
    return {"ok": True}
