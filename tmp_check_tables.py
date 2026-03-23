import os
import psycopg
from dotenv import load_dotenv

load_dotenv('/Users/majovega/Desktop/Projects/objective-hertz/.env')

conn = psycopg.connect(
    host=os.getenv('POSTGRES_HOST'),
    port=os.getenv('POSTGRES_PORT'),
    user=os.getenv('POSTGRES_USER'),
    password=os.getenv('POSTGRES_PASSWORD'),
    dbname=os.getenv('POSTGRES_DB'),
)

with conn, conn.cursor() as cur:
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY table_name")
    tables = [r[0] for r in cur.fetchall()]
    print(f'tables {len(tables)}')
    for t in tables:
        if any(k in t for k in ['client', 'site', 'web', 'host', 'deploy']):
            cur.execute(f'SELECT count(*) FROM "{t}"')
            c = cur.fetchone()[0]
            print(t, c)
