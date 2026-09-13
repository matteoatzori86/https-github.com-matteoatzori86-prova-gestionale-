import os
import sqlite3
import urllib.request
import urllib.parse
import http.cookiejar
from urllib.parse import urljoin

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(BASE, 'data', 'gestionale.db')
SERVER = 'http://127.0.0.1:5000'

conn = sqlite3.connect(DB)
cur = conn.cursor()
cur.execute("SELECT id, birth_date FROM patients LIMIT 1")
row = cur.fetchone()
if row:
    patient_id = row[0]
    print('Stored birth_date in DB:', row[1])
else:
    cur.execute(
        "INSERT INTO patients (first_name, last_name, birth_date) VALUES (?, ?, ?)",
        ("Test", "Paziente", "1980-01-01"),
    )
    conn.commit()
    patient_id = cur.lastrowid
    print('Inserted patient id', patient_id)
conn.close()

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

login_url = urljoin(SERVER, '/login')
login_data = urllib.parse.urlencode({'username': 'admin', 'password': 'admin123'}).encode('utf-8')
req = urllib.request.Request(login_url, data=login_data, method='POST')
resp = opener.open(req)
print('Login code', resp.getcode())

detail_url = urljoin(SERVER, f'/patients/{patient_id}')
resp2 = opener.open(detail_url)
body = resp2.read().decode('utf-8', errors='replace')
print('Fetched patient detail page length', len(body))
start = body.find('Data di nascita:')
if start!=-1:
    snippet = body[start:start+100]
    print('Snippet:', snippet)
else:
    print('Data di nascita not found in page')
