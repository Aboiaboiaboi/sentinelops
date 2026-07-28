from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, projects
from app.config import get_settings
from app.logging import configure_logging

settings = get_settings()

# Before the app is constructed, so anything FastAPI or uvicorn logs during
# startup is already going through the JSON handler.
configure_logging(settings.log_level)

# Routes are mounted at the root, not under /api. The frontend's dev server
# proxies /api here and strips that prefix before forwarding, so the backend
# only ever sees /health, /auth/login, /projects, and so on.
app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    debug=settings.debug,
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    # Required for the auth cookie to cross origins at all. Note that browsers
    # reject allow_credentials alongside a "*" origin, which is why cors_origins
    # is an explicit list rather than a wildcard.
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(projects.router)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe.

    Deliberately touches nothing external — it answers "is this process up?",
    not "are its dependencies up?". Conflating the two makes a health check that
    fails for reasons the process cannot act on.
    """
    return {"status": "ok"}
