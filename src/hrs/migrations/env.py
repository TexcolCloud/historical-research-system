from alembic import context

connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError("Use hrs migrate with explicit database configuration.")
context.configure(connection=connection)
with context.begin_transaction():
    context.run_migrations()
