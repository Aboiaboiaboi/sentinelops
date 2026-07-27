from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    # Postgres and anything in front of it will drop idle connections. Without
    # pre-ping, the pool hands out a dead one and the request fails rather than
    # transparently reconnecting.
    pool_pre_ping=True,
)

SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    # The important one for async. By default SQLAlchemy expires every attribute
    # on commit, so reading `user.email` afterwards triggers a lazy refresh — and
    # a lazy refresh outside an await raises MissingGreenlet. Keeping instances
    # usable after commit is what lets a route return the object it just wrote.
    expire_on_commit=False,
    autoflush=False,
)
