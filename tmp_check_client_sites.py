from pathlib import Path
import os
import json
from datetime import datetime, timezone

import psycopg
from dotenv import load_dotenv
import httpx

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / '.env')

conn = psycopg.connect(
    host=os.getenv('POSTGRES_HOST', 'localhost'),
    port=int(os.getenv('POSTGRES_PORT', '5432')),
    user=os.getenv('POSTGRES_USER', 'perseus'),
    password=os.getenv('POSTGRES_PASSWORD', ''),
    dbname=os.getenv('POSTGRES_DB', 'perseus'),
)

query = '''
SELECT id, business_name, status, final_site_url
FROM clients
WHERE status IN ('deployed', 'invoiced', 'paid')
  AND final_site_url IS NOT NULL
  AND final_site_url <> ''
ORDER BY id ASC;
'''

with conn.cursor() as cur:
    cur.execute(query)
    rows = cur.fetchall()

results = []
with httpx.Client(timeout=20.0, follow_redirects=True) as client:
    for client_id, business_name, status, url in rows:
        rec = {
            'client_id': client_id,
            'business_name': business_name,
            'status': status,
            'url': url,
            'checked_at': datetime.now(timezone.utc).isoformat(),
        }
        try:
            resp = client.get(url)
            rec['http_status'] = resp.status_code
            rec['ok_200'] = resp.status_code == 200
        except Exception as e:
            rec['http_status'] = None
            rec['ok_200'] = False
            rec['error'] = str(e)
        results.append(rec)

summary = {
    'total_sites': len(results),
    'non_200_count': sum(1 for r in results if not r['ok_200']),
    'non_200': [r for r in results if not r['ok_200']],
    'all_results': results,
}

print(json.dumps(summary, indent=2, ensure_ascii=False))
