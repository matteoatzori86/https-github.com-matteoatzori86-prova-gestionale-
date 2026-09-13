#!/usr/bin/env python3
import os
import argparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_USERS_DIR = os.path.join(os.path.dirname(BASE_DIR), "data", "utenti")

INFERMIERISTICA_FOLDERS = [
    "1.Archivio cartella terapia",
    "2.Esami Ematici e dosaggi",
    "3.Cardiologica",
    "4.Odontoiatrica",
    "5.Pneumologica",
    "6.Uroginecologica",
    "7.Ortopedica e fisiatrica",
    "8.Endocrinologica",
    "9.Dermatologica",
    "10.Diabetologica",
    "11.Prontosoccorso",
]


def ensure_infermieristica(user_root: str) -> None:
    inf_root = os.path.join(user_root, "infermieristica")
    os.makedirs(inf_root, exist_ok=True)
    for name in INFERMIERISTICA_FOLDERS:
        path = os.path.join(inf_root, name)
        os.makedirs(path, exist_ok=True)


def main(users_dir: str, dry_run: bool = False) -> int:
    if not os.path.isdir(users_dir):
        print(f"Directory non trovata: {users_dir}")
        return 1

    updated = 0
    for entry in sorted(os.listdir(users_dir)):
        user_path = os.path.join(users_dir, entry)
        if not os.path.isdir(user_path):
            continue
        inf_path = os.path.join(user_path, "infermieristica")
        if dry_run:
            print(f"[dry] Verifico {entry}: infermieristica -> {inf_path}")
            updated += 1
            continue

        ensure_infermieristica(user_path)
        print(f"Aggiornata cartella utente: {entry}")
        updated += 1

    print(f"Completato. Cartelle utente elaborate: {updated}")
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Assicura sottocartelle infermieristica per tutte le cartelle utenti in data/utenti")
    parser.add_argument("--users-dir", default=DEFAULT_USERS_DIR, help="Percorso alla cartella data/utenti")
    parser.add_argument("--dry-run", action="store_true", help="Non creare, solo mostrare cosa verrebbe fatto")
    args = parser.parse_args()
    raise SystemExit(main(args.users_dir, args.dry_run))
