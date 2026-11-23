from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (alerts, binance_profiles, discover, exchange, explore, feed, holders, research, market, okx, rankings, sentinels, sims, swings, system, timing,
                     wallets, watch, zones)

# Browsers only ever read (GET); writes arrive server-side through the web app's same-origin proxy.
app = FastAPI(title="AlphaScope API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000"], allow_methods=["GET"],
                   allow_headers=["*"])
app.include_router(rankings.router)
app.include_router(wallets.router)
app.include_router(system.router)
app.include_router(swings.router)
app.include_router(timing.router)
app.include_router(zones.router)
app.include_router(holders.router)
app.include_router(watch.router)
app.include_router(alerts.router)
app.include_router(sims.router)
app.include_router(okx.router)
app.include_router(market.router)
app.include_router(research.router)
app.include_router(exchange.router)
app.include_router(binance_profiles.router)
app.include_router(discover.router)
app.include_router(explore.router)
app.include_router(sentinels.router)
app.include_router(feed.router)


@app.get("/healthz")
def healthz():
    return {"ok": True}
