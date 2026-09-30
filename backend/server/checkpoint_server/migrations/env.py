from alembic import context
from sqlalchemy import create_engine

from checkpoint_server.app import Base

config = context.config
target_metadata = Base.metadata
url = config.get_main_option("sqlalchemy.url")

if context.is_offline_mode():
    # `--sql` mode: render DDL for review (e.g. by a DBA) without connecting.
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(url)
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
        conn.commit()
