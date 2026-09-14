import sqlite3

db_path = "data/gestionale.db"
conn = sqlite3.connect(db_path)

new_username = "agostinoeciccia"
new_password = "teoevale230118"

conn.execute(
    "UPDATE users SET username = ?, password = ? WHERE username = 'admin'",
    (new_username, new_password)
)
conn.commit()
print("✓ Credenziali admin aggiornate:")
print(f"  Username: {new_username}")
print(f"  Password: {new_password}")
conn.close()
