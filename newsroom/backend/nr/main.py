from fastapi import FastAPI

from nr.api import admin, articles, auth, desk, experiments, outbound, public, publish, reports, stars, studio

# Bound to 127.0.0.1: the web app's server-side proxy is the only client; every route checks the session itself.
app = FastAPI(title="AlphaScope Newsroom API", version="0.1.0")
app.include_router(auth.router)
app.include_router(desk.router)
app.include_router(articles.router)
app.include_router(admin.router)
app.include_router(studio.router)
app.include_router(public.router)
app.include_router(experiments.router)
app.include_router(publish.router)
app.include_router(outbound.router)
app.include_router(reports.router)
app.include_router(stars.router)


@app.get("/healthz")
def healthz():
    return {"ok": True}
