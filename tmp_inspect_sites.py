from pathlib import Path
import os
import json
import psycopg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / '.env')

conn = psycopg.connect(
    host=os.getenv('POSTGRES_HOST', 'localhost'),
    port=int(os.getenv('POSTGRES_PORT', '5432')),
    user=os.getenv('POSTGRES_USER', 'perseus'),
    password=os.getenv('POSTGRES_PASSWORD', ''),
    dbname=os.getenv('POSTGRES_DB', 'perseus'),
)

out = {}
with conn.cursor() as cur:
    cur.execute("SELECT status, count(*) FROM clients GROUP BY status ORDER BY count(*) DESC")
    out['status_counts'] = cur.fetchall()

    cur.execute("SELECT count(*) FROM clients WHERE final_site_url IS NOT NULL AND final_site_url <> ''")
    out['final_site_url_count'] = cur.fetchone()[0]

    cur.execute("SELECT count(*) FROM clients WHERE demo_site_url IS NOT NULL AND demo_site_url <> ''")
    out['demo_site_url_count'] = cur.fetchone()[0]

    cur.execute("SELECT count(*) FROM clients WHERE website_url IS NOT NULL AND website_url <> ''")
    out['website_url_count'] = cur.fetchone()[0]

    cur.execute("SELECT id,business_name,status,final_site_url,demo_site_url,website_url FROM clients WHERE (final_site_url IS NOT NULL AND final_site_url<>'') OR (demo_site_url IS NOT NULL AND demo_site_url<>'') ORDER BY id DESC LIMIT 20")
    out['sample'] = cur.fetchall()

print(json.dumps(out, indent=2, default=str))
