from fastapi import FastAPI

from app.config import get_settings

settings = get_settings()

# Routes are mounted at the root, not under /api. The frontend's dev server
# proxies /api here and strips that prefix before forwarding, so the backend
# only ever sees /health, /auth/login, /projects, and so on.
app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    debug=settings.debug,
)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe.

    Deliberately touches nothing external — it answers "is this process up?",
    not "are its dependencies up?". Conflating the two makes a health check that
    fails for reasons the process cannot act on.
    """
    return {"status": "ok"}
