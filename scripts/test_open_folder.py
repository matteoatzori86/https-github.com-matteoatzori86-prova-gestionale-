import os
import sqlite3
import urllib.request
import urllib.parse
import http.cookiejar
from urllib.parse import urljoin

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(BASE, 'data', 'gestionale.db')
SERVER = 'http://127.0.0.1:5000'

# Find or create a patient
conn = sqlite3.connect(DB)
cur = conn.cursor()
cur.execute("SELECT id FROM patients LIMIT 1")
row = cur.fetchone()
if row:
    patient_id = row[0]
else:
    cur.execute(
        "INSERT INTO patients (first_name, last_name, birth_date) VALUES (?, ?, ?)",
        ("Test", "Paziente", "1980-01-01"),
    )
    conn.commit()
    patient_id = cur.lastrowid
conn.close()

print('Using patient id:', patient_id)

# Prepare cookie jar and opener
cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

# Login as admin (default credentials from init_db)
login_url = urljoin(SERVER, '/login')
login_data = urllib.parse.urlencode({'username': 'admin', 'password': 'admin123'}).encode('utf-8')
req = urllib.request.Request(login_url, data=login_data, method='POST')
try:
    resp = opener.open(req)
    print('Login status:', resp.getcode())
except Exception as e:
    print('Login error:', e)

open_url = urljoin(SERVER, f'/patients/{patient_id}/open-folder')
req2 = urllib.request.Request(open_url, data=b'', method='POST')
try:
    resp2 = opener.open(req2)
    print('Open-folder status:', resp2.getcode())
    body = resp2.read().decode('utf-8', errors='replace')
    print('Response length:', len(body))
    print('Response snippet:', body[:500])
except Exception as e:
    print('Open-folder error:', e)
