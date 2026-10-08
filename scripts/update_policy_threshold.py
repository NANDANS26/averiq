import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

async def update_policy():
    engine = create_async_engine('postgresql+asyncpg://averiq:averiq@localhost:5432/averiq')
    async with engine.begin() as conn:
        await conn.execute(text("""
            UPDATE policies 
            SET configuration = jsonb_set(configuration, '{min_confidence}', '0.15'::jsonb);
        """))
    print("Policy min_confidence updated to 0.15")

if __name__ == '__main__':
    asyncio.run(update_policy())
