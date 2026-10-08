import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

async def reconcile_audit_events():
    engine = create_async_engine('postgresql+asyncpg://averiq:averiq@localhost:5432/averiq')
    async with engine.begin() as conn:
        res = await conn.execute(text("""
            SELECT column_name, is_nullable 
            FROM information_schema.columns 
            WHERE table_name = 'audit_events';
        """))
        cols = res.fetchall()
        for c in cols:
            col_name, is_nullable = c[0], c[1]
            print(f"{col_name}: nullable={is_nullable}")
            if is_nullable == 'NO' and col_name not in ('id', 'event_id', 'created_at'):
                print(f"Dropping NOT NULL on {col_name}")
                await conn.execute(text(f'ALTER TABLE audit_events ALTER COLUMN "{col_name}" DROP NOT NULL;'))
    print("reconcile audit_events completed successfully")

if __name__ == '__main__':
    asyncio.run(reconcile_audit_events())
