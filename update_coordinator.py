import sqlite3

db_path = "data/gestionale.db"
conn = sqlite3.connect(db_path)

new_password = "girasole2023"

# Aggiorna la password del coordinatore (username: admin)
conn.execute(
    "UPDATE users SET password = ? WHERE username = 'admin' AND role = 'coordinatore'",
    (new_password,)
)
conn.commit()
print("✓ Password coordinatore aggiornata:")
print(f"  Username: admin")
print(f"  Nuova password: {new_password}")
conn.close()
