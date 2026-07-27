from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base that every ORM model inherits from.

    Deliberately kept in its own module, separate from session.py. Alembic needs
    `Base.metadata` to autogenerate migrations, and importing it from a module
    that also builds the engine would open a connection pool as a side effect of
    running a migration — including the offline mode that is supposed to emit SQL
    without touching a database at all.
    """
