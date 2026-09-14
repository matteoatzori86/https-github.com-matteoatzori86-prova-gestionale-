import csv
import math
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from functools import wraps
from io import StringIO
from uuid import uuid4

from flask import Flask, render_template, request, redirect, url_for, flash, session, abort, Response, send_from_directory

try:
    from win10toast import ToastNotifier
    TOAST_AVAILABLE = True
except ImportError:
    TOAST_AVAILABLE = False

app = Flask(__name__)
app.secret_key = "gestionale-demo-key"
SECONDARY_DELETE_PASSWORD = os.environ.get("SECONDARY_DELETE_PASSWORD", "ctr25072023")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "gestionale.db")

# Variabile globale per tracciare la data dell'ultima notifica
last_notification_date = None
notification_thread_running = False


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_user_columns():
    conn = get_db()
    columns = [row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()]
    if "is_archived" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN is_archived INTEGER NOT NULL DEFAULT 0")
    if "theme" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN theme TEXT NOT NULL DEFAULT 'cool'")
    conn.commit()
    conn.close()


def ensure_audit_tables():
    conn = get_db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS access_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            details TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )
    conn.commit()
    conn.close()


def send_windows_notifications():
    """Invia notifiche di Windows per farmaci in scadenza e carenza"""
    global last_notification_date

    if not TOAST_AVAILABLE:
        return

    try:
        toaster = ToastNotifier()
        
        # Controlla ogni 24 ore, azzerato alle 9:00 del mattino
        while True:
            now = datetime.now()
            
            # Calcola il prossimo trigger a 9:00
            if now.hour >= 9:
                # Se è già passato le 9:00 oggi, il prossimo trigger è domani a 9:00
                next_check = now.replace(hour=9, minute=0, second=0, microsecond=0) + timedelta(days=1)
            else:
                # Se non è ancora passato le 9:00, il prossimo trigger è oggi a 9:00
                next_check = now.replace(hour=9, minute=0, second=0, microsecond=0)
            
            # Calcola i secondi da aspettare
            wait_seconds = (next_check - now).total_seconds()
            
            # Se wait_seconds è negativo o zero, attendi comunque almeno 60 secondi
            if wait_seconds <= 0:
                wait_seconds = 60
            
            time.sleep(wait_seconds)
            
            # Raccogli notifiche di scadenza
            expiring = get_expiring_therapies()
            low_stock = get_low_stock_therapies()
            
            if expiring:
                title = "Avviso Farmaci Scadenza"
                message_parts = []
                for therapy in expiring[:5]:  # Limita a 5 notifiche
                    message_parts.append(f"• {therapy['drug_name']} ({therapy['patient_name']}): {therapy['message']}")
                
                message = "\n".join(message_parts)
                if len(expiring) > 5:
                    message += f"\n... e {len(expiring) - 5} altri"
                
                toaster.show_toast(title, message, duration=10, threaded=True)
                time.sleep(2)
            
            if low_stock:
                title = "Avviso Carenza Farmaci"
                message_parts = []
                for therapy in low_stock[:5]:  # Limita a 5 notifiche
                    message_parts.append(f"• {therapy['drug_name']} ({therapy['patient_name']}): {therapy['message']}")
                
                message = "\n".join(message_parts)
                if len(low_stock) > 5:
                    message += f"\n... e {len(low_stock) - 5} altri"
                
                toaster.show_toast(title, message, duration=10, threaded=True)
                time.sleep(2)

    except Exception as e:
        print(f"Errore notifiche Windows: {e}")


def record_activity(action, details="", user_id=None):
    if user_id is None:
        current_user = get_current_user()
        if current_user:
            user_id = current_user["id"]
        else:
            return

    conn = get_db()
    conn.execute(
        "INSERT INTO access_log (user_id, action, details, created_at) VALUES (?, ?, ?, ?)",
        (user_id, action, details, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    conn.commit()
    conn.close()


def pack_label(pack_type):
    mapping = {
        "cp": "cp",
        "gocce": "gocce",
        "fiale": "fiale",
        "bustine": "bustine",
    }
    return mapping.get((pack_type or "cp").lower(), "unità")


def therapy_stock_summary(therapy):
    try:
        therapy_dict = dict(therapy)
    except Exception:
        therapy_dict = therapy

    pack_type = therapy_dict.get("pack_type") or "cp"
    units_per_box = int(therapy_dict.get("units_per_box") or 0)
    stock_boxes = int(therapy_dict.get("stock_boxes") or 0)
    dosage = (therapy_dict.get("dosage") or "").strip().lower()
    schedule = (therapy_dict.get("schedule") or "").strip()
    expiry_date = (therapy_dict.get("expiry_date") or "").strip()

    if units_per_box <= 0 or stock_boxes <= 0:
        return {
            "stock_label": "Giacenza non impostata",
            "duration_label": "-",
            "expiry_label": expiry_date or "-",
        }

    stock_label = f"{stock_boxes} scatole · {units_per_box} {pack_label(pack_type)}"

    total_units = units_per_box * stock_boxes
    dose_match = re.search(r"(\d+(?:[.,]\d+)?)\s*(cp|capsule|compresse|gocce|fiale|bustine)\b", dosage)
    amount_per_dose = 1
    if dose_match:
        try:
            amount_per_dose = int(float(dose_match.group(1)))
        except Exception:
            amount_per_dose = 1

    schedule_times = re.findall(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b", schedule)
    schedule_count = len(schedule_times)

    text = dosage
    period = "day"
    if "settimana" in text or "a settimana" in text or "alla settimana" in text:
        period = "week"
    elif "mese" in text or "mensile" in text or "al mese" in text or "a mese" in text:
        period = "month"

    if period == "day":
        daily_count = schedule_count if schedule_count > 0 else 1
        daily_match = re.search(r"(\d+)\s*volte\s*al\s*giorno", text)
        if daily_match:
            daily_count = int(daily_match.group(1)) if schedule_count == 0 else schedule_count
        units_per_day = daily_count * amount_per_dose
        duration_days = max(0, total_units // units_per_day)
        duration_label = f"{duration_days} giorni"
    elif period == "week":
        weekly_match = re.search(r"(\d+)\s*volta\s*a\s*settimana|una?\s*volta\s*a\s*settimana", text)
        weekly_count = int(weekly_match.group(1)) if weekly_match and weekly_match.group(1) else 1
        units_per_week = weekly_count * amount_per_dose
        duration_weeks = max(0, total_units // units_per_week)
        duration_label = f"{duration_weeks} settimane"
    elif period == "month":
        monthly_match = re.search(r"(\d+)\s*volta\s*al\s*mese|una?\s*volta\s*al\s*mese", text)
        monthly_count = int(monthly_match.group(1)) if monthly_match and monthly_match.group(1) else 1
        units_per_month = monthly_count * amount_per_dose
        duration_months = max(0, total_units // units_per_month)
        duration_label = f"{duration_months} mesi"

    if expiry_date:
        try:
            expiry_obj = datetime.strptime(expiry_date, "%Y-%m-%d").date()
            if datetime.now().date() > expiry_obj:
                duration_label += " · scaduta"
        except Exception:
            pass

    return {
        "stock_label": stock_label,
        "duration_label": duration_label,
        "expiry_label": expiry_date or "-",
    }


def ensure_therapy_columns():
    conn = get_db()
    columns = [row[1] for row in conn.execute("PRAGMA table_info(therapies)").fetchall()]
    wanted = {
        "active_ingredient": "TEXT",
        "pack_type": "TEXT",
        "units_per_box": "INTEGER NOT NULL DEFAULT 0",
        "stock_boxes": "INTEGER NOT NULL DEFAULT 0",
        "expiry_date": "TEXT",
    }

    for column_name, column_type in wanted.items():
        if column_name not in columns:
            try:
                conn.execute(f"ALTER TABLE therapies ADD COLUMN {column_name} {column_type}")
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise

    conn.commit()
    conn.close()


def ensure_patient_columns():
    conn = get_db()
    columns = [row[1] for row in conn.execute("PRAGMA table_info(patients)").fetchall()]
    wanted = {
        "diagnosis": "TEXT",
        "identifier_code": "TEXT",
        "ads": "TEXT",
        "family_members": "TEXT",
        "exemption": "TEXT",
        "mmg": "TEXT",
        "arrival_from": "TEXT",
        "arrival_from_other": "TEXT",
        "is_archived": "INTEGER NOT NULL DEFAULT 0",
    }

    for column_name, column_type in wanted.items():
        if column_name not in columns:
            try:
                conn.execute(f"ALTER TABLE patients ADD COLUMN {column_name} {column_type}")
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS therapies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            drug_name TEXT NOT NULL,
            active_ingredient TEXT,
            dosage TEXT,
            schedule TEXT,
            notes TEXT,
            pack_type TEXT,
            units_per_box INTEGER NOT NULL DEFAULT 0,
            stock_boxes INTEGER NOT NULL DEFAULT 0,
            expiry_date TEXT,
            FOREIGN KEY (patient_id) REFERENCES patients(id)
        )
        """
    )

    conn.commit()
    conn.close()


def pack_label(pack_type):
    pack_type = (pack_type or "cp").lower()
    return {"cp": "cp", "gocce": "gocce", "fiale": "fiale", "bustine": "bustine"}.get(pack_type, "unità")


def therapy_stock_summary(therapy):
    try:
        therapy_dict = dict(therapy)
    except Exception:
        therapy_dict = therapy

    pack_type = therapy_dict.get("pack_type") or "cp"
    units_per_box = int(therapy_dict.get("units_per_box") or 0)
    stock_boxes = int(therapy_dict.get("stock_boxes") or 0)
    dosage = (therapy_dict.get("dosage") or "").strip().lower()
    schedule = (therapy_dict.get("schedule") or "").strip()
    expiry_date = (therapy_dict.get("expiry_date") or "").strip()

    if units_per_box <= 0 or stock_boxes <= 0:
        return {
            "stock_label": "Giacenza non impostata",
            "duration_label": "-",
            "expiry_label": expiry_date or "-",
        }

    stock_label = f"{stock_boxes} scatole · {units_per_box} {pack_label(pack_type)}"

    total_units = units_per_box * stock_boxes
    dose_match = re.search(r"(\d+(?:[.,]\d+)?)\s*(cp|capsule|compresse|gocce|fiale|bustine)\b", dosage)
    amount_per_dose = 1
    if dose_match:
        try:
            amount_per_dose = int(float(dose_match.group(1)))
        except Exception:
            amount_per_dose = 1

    schedule_times = re.findall(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b", schedule)
    schedule_count = len(schedule_times)

    text = dosage
    period = "day"
    if "settimana" in text or "a settimana" in text or "alla settimana" in text:
        period = "week"
    elif "mese" in text or "mensile" in text or "al mese" in text or "a mese" in text:
        period = "month"

    if period == "day":
        daily_count = schedule_count if schedule_count > 0 else 1
        daily_match = re.search(r"(\d+)\s*volte\s*al\s*giorno", text)
        if daily_match:
            daily_count = int(daily_match.group(1)) if schedule_count == 0 else schedule_count
        units_per_day = daily_count * amount_per_dose
        duration_days = max(0, total_units // units_per_day)
        duration_label = f"{duration_days} giorni"
    elif period == "week":
        weekly_match = re.search(r"(\d+)\s*volta\s*a\s*settimana|una?\s*volta\s*a\s*settimana", text)
        weekly_count = int(weekly_match.group(1)) if weekly_match and weekly_match.group(1) else 1
        units_per_week = weekly_count * amount_per_dose
        duration_weeks = max(0, total_units // units_per_week)
        duration_label = f"{duration_weeks} settimane"
    elif period == "month":
        monthly_match = re.search(r"(\d+)\s*volta\s*al\s*mese|una?\s*volta\s*al\s*mese", text)
        monthly_count = int(monthly_match.group(1)) if monthly_match and monthly_match.group(1) else 1
        units_per_month = monthly_count * amount_per_dose
        duration_months = max(0, total_units // units_per_month)
        duration_label = f"{duration_months} mesi"

    expiry_label = expiry_date or "-"

    return {
        "stock_label": stock_label,
        "duration_label": duration_label,
        "expiry_label": expiry_label,
    }


def ensure_patient_documents_table():
    conn = get_db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS patient_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            original_name TEXT NOT NULL,
            stored_name TEXT NOT NULL,
            mime_type TEXT,
            file_path TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (patient_id) REFERENCES patients(id)
        )
        """
    )
    conn.commit()
    conn.close()


def sanitize_user_folder_name(username):
    value = (username or "").strip()
    value = value.replace("\\", "_").replace("/", "_")
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value)
    value = value.strip("._ ")
    return value or "utente"


def normalize_relative_folder_path(folder_path):
    value = (folder_path or "").replace("\\", "/").strip()
    if not value:
        return ""
    cleaned = []
    for part in value.split("/"):
        if part in ("", ".", ".."):
            continue
        cleaned.append(part)
    return "/".join(cleaned)


def get_patient_folder_root(patient):
    if hasattr(patient, "keys"):
        mapping = dict(patient)
        identifier_code = (mapping.get("identifier_code") or "").strip()
        first_name = (mapping.get("first_name") or "").strip()
        last_name = (mapping.get("last_name") or "").strip()
    elif isinstance(patient, dict):
        identifier_code = (patient.get("identifier_code") or "").strip()
        first_name = (patient.get("first_name") or "").strip()
        last_name = (patient.get("last_name") or "").strip()
    else:
        identifier_code = (getattr(patient, "identifier_code", "") or "").strip()
        first_name = (getattr(patient, "first_name", "") or "").strip()
        last_name = (getattr(patient, "last_name", "") or "").strip()

    folder_name = identifier_code or f"{first_name} {last_name}".strip()
    root_path = os.path.join(DATA_DIR, "utenti", sanitize_user_folder_name(folder_name))
    os.makedirs(root_path, exist_ok=True)
    return root_path


def build_patient_folder_tree(root_path):
    root_path = os.path.abspath(root_path)
    def sort_key_name(name):
        # Ordina prima per eventuale prefisso numerico (es. '1.', '10.'), poi alfabeticamente
        m = re.match(r"^\s*(\d+)", name)
        if m:
            return (int(m.group(1)), name.lower())
        return (float('inf'), name.lower())

    def walk(directory, relative_dir=""):
        files = []
        for filename in sorted(os.listdir(directory)):
            full_path = os.path.join(directory, filename)
            if os.path.isfile(full_path) and filename.lower().endswith(".pdf"):
                relative_pdf_path = os.path.relpath(full_path, root_path).replace("\\", "/")
                files.append({
                    "name": filename,
                    "relative_path": relative_pdf_path,
                })

        children = []
        # prendi solo le directory e ordinale rispettando prefisso numerico quando presente
        dirnames = [d for d in os.listdir(directory) if os.path.isdir(os.path.join(directory, d))]
        for dirname in sorted(dirnames, key=sort_key_name):
            full_dir = os.path.join(directory, dirname)
            child_relative = os.path.join(relative_dir, dirname).replace("\\", "/") if relative_dir else dirname
            children.append(walk(full_dir, child_relative))

        return {
            "label": os.path.basename(directory) if relative_dir else "Cartella utente",
            "relative_path": relative_dir,
            "files": files,
            "children": children,
        }

    if not os.path.isdir(root_path):
        os.makedirs(root_path, exist_ok=True)
    return walk(root_path)


def create_user_folder_tree(username):
    user_code = sanitize_user_folder_name(username)
    user_root = os.path.join(DATA_DIR, "utenti", user_code)
    os.makedirs(user_root, exist_ok=True)
    os.makedirs(os.path.join(user_root, "sociosanitaria"), exist_ok=True)
    os.makedirs(os.path.join(user_root, "infermieristica"), exist_ok=True)

    sociosanitaria_root = os.path.join(user_root, "sociosanitaria")
    sociosanitaria_folders = [
        "1. Anagrafica e trattamento dei dati",
        "2. Ingresso e dimissioni, PTAI, PRI, SAFE, VF, Scheda registrazione punteggi",
        "3. Colloqui e test Psicologa",
        "4. Colloqui e relazioni psichiatra",
        "5. Verbali equipe e monitoraggi",
        "6. Centro per l'impiego",
        "7. Documenti Cassa e ADS",
    ]
    for folder_name in sociosanitaria_folders:
        os.makedirs(os.path.join(sociosanitaria_root, folder_name), exist_ok=True)

    pta_folder = os.path.join(
        sociosanitaria_root,
        "2. Ingresso e dimissioni, PTAI, PRI, SAFE, VF, Scheda registrazione punteggi",
    )
    for nested_folder in [
        "PRI",
        "PTAI + Foglio firmato e relazioni CSM",
        "Relazioni PTAI",
        "SAFE + VF + Tabella Riepilogo Scale",
    ]:
        os.makedirs(os.path.join(pta_folder, nested_folder), exist_ok=True)

    for nested_folder in [
        "Colloqui",
        "TEST",
    ]:
        os.makedirs(os.path.join(sociosanitaria_root, "3. Colloqui e test Psicologa", nested_folder), exist_ok=True)

    for nested_folder in [
        "Colloqui",
        "Relazioni",
    ]:
        os.makedirs(os.path.join(sociosanitaria_root, "4. Colloqui e relazioni psichiatra", nested_folder), exist_ok=True)

    for nested_folder in [
        "Attestazione ISEE",
        "Decreto nomina ads",
        "Invalidità",
        "Mail e password",
        "Moduli acquisti + scontrini",
    ]:
        os.makedirs(os.path.join(sociosanitaria_root, "7. Documenti Cassa e ADS", nested_folder), exist_ok=True)

    # Create specific subfolders inside 'infermieristica'
    infermieristica_root = os.path.join(user_root, "infermieristica")
    infermieristica_folders = [
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
    for folder_name in infermieristica_folders:
        os.makedirs(os.path.join(infermieristica_root, folder_name), exist_ok=True)

    return user_root


ROLE_LABELS = {
    "admin": "Amministratore",
    "coordinatore": "Coordinatore",
    "vicecoordinatore": "Vice coordinatore",
    "psicologa": "Psicologa",
    "psichiatra": "Psichiatra",
    "infermiere": "Infermiere",
    "oss": "OSS",
    "educatore": "Educatore",
}

ROLE_PERMISSIONS = {
    "admin": {
        "view_patients": True,
        "view_appointments": True,
        "view_users": True,
        "create_users": True,
        "manage_users": True,
        "edit_patients": True,
        "delete_patients": True,
        "edit_appointments": True,
        "delete_appointments": True,
        "edit_therapies": True,
        "delete_therapies": True,
        "open_user_folder": False,
    },
    "coordinatore": {
        "view_patients": True,
        "view_appointments": True,
        "view_users": True,
        "create_users": True,
        "manage_users": True,
        "edit_patients": True,
        "delete_patients": True,
        "edit_appointments": True,
        "delete_appointments": True,
        "edit_therapies": True,
        "delete_therapies": True,
        "open_user_folder": True,
    },
    "vicecoordinatore": {
        "view_patients": True,
        "view_appointments": True,
        "view_users": True,
        "create_users": True,
        "manage_users": True,
        "edit_patients": True,
        "delete_patients": True,
        "edit_appointments": True,
        "delete_appointments": True,
        "edit_therapies": True,
        "delete_therapies": True,
        "open_user_folder": True,
    },
    "psicologa": {
        "view_patients": True,
        "view_appointments": True,
        "view_users": False,
        "create_users": True,
        "manage_users": False,
        "edit_patients": False,
        "delete_patients": False,
        "edit_appointments": False,
        "delete_appointments": False,
        "edit_therapies": False,
        "delete_therapies": False,
        "open_user_folder": False,
    },
    "psichiatra": {
        "view_patients": True,
        "view_appointments": True,
        "view_users": False,
        "create_users": True,
        "manage_users": False,
        "edit_patients": False,
        "delete_patients": False,
        "edit_appointments": False,
        "delete_appointments": False,
        "edit_therapies": False,
        "delete_therapies": False,
        "open_user_folder": False,
    },
    "infermiere": {
        "view_patients": True,
        "view_appointments": True,
        "view_users": False,
        "create_users": True,
        "manage_users": False,
        "edit_patients": False,
        "delete_patients": False,
        "edit_appointments": True,
        "delete_appointments": False,
        "edit_therapies": True,
        "delete_therapies": False,
        "open_user_folder": False,
    },
    "oss": {
        "view_patients": True,
        "view_appointments": True,
        "view_users": False,
        "create_users": True,
        "manage_users": False,
        "edit_patients": False,
        "delete_patients": False,
        "edit_appointments": True,
        "delete_appointments": True,
        "edit_therapies": False,
        "delete_therapies": False,
        "open_user_folder": False,
    },
    "educatore": {
        "view_patients": False,
        "view_appointments": True,
        "view_users": False,
        "create_users": True,
        "manage_users": False,
        "edit_patients": False,
        "delete_patients": False,
        "edit_appointments": True,
        "delete_appointments": True,
        "edit_therapies": False,
        "delete_therapies": False,
        "open_user_folder": False,
    },
}


def get_current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id = ? AND is_archived = 0", (user_id,)).fetchone()
    conn.close()
    return dict(user) if user else None


def user_has_permission(action):
    user = get_current_user()
    if not user:
        return False

    if action == "open_user_folder":
        return user.get("role") in {"coordinatore", "vicecoordinatore"}

    if user.get("role") == "admin":
        return True
    if user.get("role") in {"coordinatore", "vicecoordinatore"}:
        return True
    return ROLE_PERMISSIONS.get(user.get("role"), {}).get(action, False)


def require_login(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not get_current_user():
            flash("Effettua l'accesso per continuare.", "warning")
            return redirect(url_for("login"))
        return view_func(*args, **kwargs)

    return wrapper


def require_permission(permission_name):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(*args, **kwargs):
            if not user_has_permission(permission_name):
                flash("Non hai i permessi per eseguire questa azione.", "danger")
                abort(403)
            return view_func(*args, **kwargs)

        return wrapper

    return decorator


def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = get_db()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            fullname TEXT NOT NULL,
            role TEXT NOT NULL,
            password TEXT NOT NULL,
            is_archived INTEGER NOT NULL DEFAULT 0
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS patients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            birth_date TEXT,
            cf TEXT,
            phone TEXT,
            email TEXT,
            address TEXT,
            notes TEXT,
            diagnosis TEXT,
            identifier_code TEXT,
            ads TEXT,
            family_members TEXT,
            exemption TEXT,
            mmg TEXT,
            arrival_from TEXT,
            arrival_from_other TEXT
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS appointments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            operator_name TEXT NOT NULL,
            appointment_date TEXT NOT NULL,
            appointment_time TEXT NOT NULL,
            appointment_type TEXT NOT NULL,
            status TEXT NOT NULL,
            notes TEXT,
            FOREIGN KEY (patient_id) REFERENCES patients(id)
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            appointment_id INTEGER NOT NULL,
            reminder_days INTEGER NOT NULL,
            sent_at TEXT NOT NULL,
            message TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'sent',
            UNIQUE(appointment_id, reminder_days)
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS visits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            appointment_id INTEGER,
            visit_date TEXT NOT NULL,
            visit_type TEXT NOT NULL,
            doctor_name TEXT,
            summary TEXT,
            notes TEXT,
            outcome TEXT,
            follow_up TEXT,
            FOREIGN KEY (patient_id) REFERENCES patients(id),
            FOREIGN KEY (appointment_id) REFERENCES appointments(id)
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS therapies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            drug_name TEXT NOT NULL,
            active_ingredient TEXT,
            dosage TEXT,
            schedule TEXT,
            notes TEXT,
            pack_type TEXT,
            units_per_box INTEGER NOT NULL DEFAULT 0,
            stock_boxes INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (patient_id) REFERENCES patients(id)
        )
        """
    )

    existing_user = conn.execute(
        "SELECT id FROM users WHERE username = ?",
        ("admin",),
    ).fetchone()

    conn.execute(
        "UPDATE users SET role = 'coordinatore' WHERE username = ? AND role = ?",
        ("admin", "admin"),
    )

    if not existing_user:
        conn.execute(
            "INSERT INTO users (username, fullname, role, password, is_archived) VALUES (?, ?, ?, ?, 0)",
            ("admin", "Amministratore", "coordinatore", "admin123"),
        )

    conn.commit()
    conn.close()
    ensure_user_columns()
    ensure_audit_tables()
    ensure_patient_columns()
    ensure_therapy_columns()
    ensure_patient_documents_table()


def check_due_reminders():
    today = datetime.now().date()
    conn = get_db()
    appointments = conn.execute(
        """
        SELECT a.id, a.appointment_date, a.appointment_time, a.appointment_type,
               a.status, p.first_name || ' ' || p.last_name AS patient_name
        FROM appointments a
        JOIN patients p ON p.id = a.patient_id
        WHERE a.status NOT IN ('annullato', 'completato')
        ORDER BY a.appointment_date ASC
        """
    ).fetchall()

    reminder_targets = [10, 3, 1]
    for appointment in appointments:
        try:
            appointment_date = datetime.strptime(appointment["appointment_date"], "%Y-%m-%d").date()
        except (TypeError, ValueError):
            continue

        days_left = (appointment_date - today).days
        if days_left not in reminder_targets:
            continue

        existing = conn.execute(
            "SELECT id FROM reminders WHERE appointment_id = ? AND reminder_days = ?",
            (appointment["id"], days_left),
        ).fetchone()

        if existing:
            continue

        message = (
            f"Promemoria: appuntamento {appointment['appointment_type']} per "
            f"{appointment['patient_name']} in data {appointment['appointment_date']} alle {appointment['appointment_time']} "
            f"({days_left} giorni)."
        )

        conn.execute(
            "INSERT INTO reminders (appointment_id, reminder_days, sent_at, message, status) VALUES (?, ?, ?, ?, 'sent')",
            (
                appointment["id"],
                days_left,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                message,
            ),
        )

    conn.commit()
    conn.close()


def reminder_worker():
    while True:
        try:
            check_due_reminders()
        except Exception as exc:
            print(f"Reminder error: {exc}")
        time.sleep(60)


@app.context_processor
def inject_globals():
    return {
        "current_year": datetime.now().year,
        "app_name": "strutture socio-sanitarie",
        "current_user": get_current_user(),
        "ROLE_LABELS": ROLE_LABELS,
        "user_has_permission": user_has_permission,
    }


@app.template_filter('it_date')
def it_date(value):
    if not value:
        return ''
    # Accept common ISO formats stored in DB and convert to dd/mm/YYYY
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(value, fmt)
            # If time component present, include time
            if fmt == "%Y-%m-%d %H:%M:%S":
                return dt.strftime("%d/%m/%Y %H:%M:%S")
            return dt.strftime("%d/%m/%Y")
        except Exception:
            continue
    # If value already contains '/', assume it's already in dd/mm/YYYY
    return value


@app.route("/login", methods=["GET", "POST"])
def login():
    if get_current_user():
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        conn = get_db()
        user = conn.execute(
            "SELECT * FROM users WHERE username = ? AND password = ? AND is_archived = 0",
            (username, password),
        ).fetchone()
        conn.close()

        if user is None:
            flash("Credenziali non valide.", "danger")
            return redirect(url_for("login"))

        session["user_id"] = user["id"]
        record_activity("login", f"Login eseguito da {user['fullname']}", user["id"])
        flash("Accesso eseguito correttamente.", "success")
        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Sessione terminata.", "info")
    return redirect(url_for("login"))


@app.route("/change-theme/<theme>")
def change_theme(theme):
    if not get_current_user():
        return redirect(url_for("login"))
    
    if theme not in ["cool", "warm", "gray", "purple", "ottanio", "red", "yellow", "forest", "meadow"]:
        flash("Tema non valido.", "danger")
        return redirect(url_for("dashboard"))
    
    user = get_current_user()
    conn = get_db()
    conn.execute("UPDATE users SET theme = ? WHERE id = ?", (theme, user["id"]))
    conn.commit()
    conn.close()
    
    flash(f"Tema cambiato con successo.", "success")
    return redirect(request.referrer or url_for("dashboard"))



@app.route("/")
@require_login
def dashboard():
    conn = get_db()
    patient_count = conn.execute("SELECT COUNT(*) AS total FROM patients").fetchone()["total"]
    appointment_count = conn.execute("SELECT COUNT(*) AS total FROM appointments").fetchone()["total"]
    users_count = conn.execute("SELECT COUNT(*) AS total FROM users WHERE is_archived = 0 AND role != 'admin'").fetchone()["total"]
    documents_count = conn.execute("SELECT COUNT(*) AS total FROM patient_documents").fetchone()["total"]
    upcoming_count = conn.execute(
        "SELECT COUNT(*) AS total FROM appointments WHERE appointment_date >= date('now')"
    ).fetchone()["total"]
    next_appointments = conn.execute(
        """
        SELECT a.id, p.first_name || ' ' || p.last_name AS patient_name,
               a.appointment_date, a.appointment_time, a.appointment_type, a.status
        FROM appointments a
        JOIN patients p ON p.id = a.patient_id
        ORDER BY a.appointment_date ASC, a.appointment_time ASC
        LIMIT 5
        """
    ).fetchall()
    recent_reminders = conn.execute(
        """
        SELECT r.id, r.reminder_days, r.sent_at, r.message,
               p.first_name || ' ' || p.last_name AS patient_name
        FROM reminders r
        JOIN appointments a ON a.id = r.appointment_id
        JOIN patients p ON p.id = a.patient_id
        ORDER BY r.sent_at DESC
        LIMIT 5
        """
    ).fetchall()
    conn.close()
    return render_template(
        "dashboard.html",
        patient_count=patient_count,
        appointment_count=appointment_count,
        users_count=users_count,
        documents_count=documents_count,
        upcoming_count=upcoming_count,
        next_appointments=next_appointments,
        recent_reminders=recent_reminders,
    )


@app.route("/patients", methods=["GET", "POST"])
@require_login
def patients():
    if not user_has_permission("view_patients"):
        abort(403)

    if request.method == "POST":
        if not user_has_permission("edit_patients"):
            abort(403)
        first_name = request.form.get("first_name", "").strip()
        last_name = request.form.get("last_name", "").strip()
        identifier_code = request.form.get("identifier_code", "").strip()
        if not first_name or not last_name:
            flash("Nome e cognome sono obbligatori.", "danger")
            return redirect(url_for("patients"))

        data = (
            first_name,
            last_name,
            request.form.get("birth_date"),
            request.form.get("cf"),
            request.form.get("phone"),
            request.form.get("email"),
            request.form.get("address"),
            request.form.get("notes"),
            request.form.get("diagnosis"),
            identifier_code,
            request.form.get("ads"),
            request.form.get("family_members"),
            request.form.get("exemption"),
            request.form.get("mmg"),
            request.form.get("arrival_from"),
            request.form.get("arrival_from_other"),
        )
        conn = get_db()
        conn.execute(
            """
            INSERT INTO patients (
                first_name, last_name, birth_date, cf, phone, email, address, notes,
                diagnosis, identifier_code, ads, family_members, exemption, mmg,
                arrival_from, arrival_from_other, is_archived
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            data,
        )
        conn.commit()
        patient_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        conn.close()
        folder_name = identifier_code or f"{first_name} {last_name}"
        create_user_folder_tree(folder_name)
        record_activity("create_patient", f"Creato utente: {first_name} {last_name}")
        flash("Utente creato correttamente.", "success")
        return redirect(url_for("patients"))

    search_query = request.args.get("q", "").strip()
    conn = get_db()
    if search_query:
        like_query = f"%{search_query}%"
        active_patients = conn.execute(
            """
            SELECT * FROM patients
            WHERE is_archived = 0
              AND (
                first_name LIKE ?
                OR last_name LIKE ?
                OR identifier_code LIKE ?
                OR cf LIKE ?
              )
            ORDER BY last_name ASC, first_name ASC
            """,
            (like_query, like_query, like_query, like_query),
        ).fetchall()
        archived_patients = conn.execute(
            """
            SELECT * FROM patients
            WHERE is_archived = 1
              AND (
                first_name LIKE ?
                OR last_name LIKE ?
                OR identifier_code LIKE ?
                OR cf LIKE ?
              )
            ORDER BY last_name ASC, first_name ASC
            """,
            (like_query, like_query, like_query, like_query),
        ).fetchall()
    else:
        active_patients = conn.execute(
            "SELECT * FROM patients WHERE is_archived = 0 ORDER BY last_name ASC, first_name ASC"
        ).fetchall()
        archived_patients = conn.execute(
            "SELECT * FROM patients WHERE is_archived = 1 ORDER BY last_name ASC, first_name ASC"
        ).fetchall()
    conn.close()
    return render_template("patients.html", patients=active_patients, archived_patients=archived_patients, search_query=search_query)


@app.route("/patients/<int:patient_id>")
@require_login
def patient_detail(patient_id):
    if not user_has_permission("view_patients"):
        abort(403)
    conn = get_db()
    patient = conn.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone()
    if not patient:
        conn.close()
        flash("Utente non trovato.", "danger")
        return redirect(url_for("patients"))

    folder_root = get_patient_folder_root(patient)
    folder_tree = build_patient_folder_tree(folder_root)

    appointments = conn.execute(
        """
        SELECT * FROM appointments
        WHERE patient_id = ?
        ORDER BY appointment_date DESC, appointment_time DESC
        """,
        (patient_id,),
    ).fetchall()

    visits = conn.execute(
        """
        SELECT * FROM visits
        WHERE patient_id = ?
        ORDER BY visit_date DESC
        """,
        (patient_id,),
    ).fetchall()

    therapies = conn.execute(
        "SELECT * FROM therapies WHERE patient_id = ? ORDER BY id DESC",
        (patient_id,),
    ).fetchall()

    documents = conn.execute(
        "SELECT * FROM patient_documents WHERE patient_id = ? ORDER BY uploaded_at DESC",
        (patient_id,),
    ).fetchall()
    conn.close()
    return render_template(
        "patient_detail.html",
        patient=patient,
        appointments=appointments,
        visits=visits,
        therapies=therapies,
        documents=documents,
        folder_tree=folder_tree,
        therapy_stock_summary=therapy_stock_summary,
    )


@app.route("/patients/<int:patient_id>/folder-upload", methods=["POST"])
@require_login
def upload_patient_folder_pdf(patient_id):
    if not user_has_permission("edit_patients"):
        abort(403)

    conn = get_db()
    patient = conn.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone()
    conn.close()
    if not patient:
        flash("Utente non trovato.", "danger")
        return redirect(url_for("patients"))

    folder_path = normalize_relative_folder_path(request.form.get("folder_path"))
    file = request.files.get("file")
    if not file or file.filename == "":
        flash("Seleziona un file PDF da caricare.", "danger")
        return redirect(url_for("patient_detail", patient_id=patient_id))

    if not file.filename.lower().endswith(".pdf"):
        flash("Il file deve essere un PDF.", "danger")
        return redirect(url_for("patient_detail", patient_id=patient_id))

    base_dir = get_patient_folder_root(patient)
    target_dir = os.path.join(base_dir, *folder_path.split("/")) if folder_path else base_dir
    os.makedirs(target_dir, exist_ok=True)

    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.basename(file.filename)) or "documento.pdf"
    destination = os.path.join(target_dir, safe_name)
    if os.path.exists(destination):
        stem = os.path.splitext(safe_name)[0]
        ext = os.path.splitext(safe_name)[1] or ".pdf"
        counter = 1
        while os.path.exists(destination):
            destination = os.path.join(target_dir, f"{stem}_{counter}{ext}")
            counter += 1

    file.save(destination)
    flash("PDF caricato correttamente nella cartella selezionata.", "success")
    return redirect(url_for("patient_detail", patient_id=patient_id))


@app.route("/patients/<int:patient_id>/folder-file/<path:file_path>")
@require_login
def download_patient_folder_pdf(patient_id, file_path):
    if not user_has_permission("view_patients"):
        abort(403)

    conn = get_db()
    patient = conn.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone()
    conn.close()
    if not patient:
        flash("Utente non trovato.", "danger")
        return redirect(url_for("patients"))

    root_dir = get_patient_folder_root(patient)
    normalized = os.path.normpath(file_path).replace("\\", "/")
    if normalized in (".", ""):
        flash("File non trovato.", "danger")
        return redirect(url_for("patient_detail", patient_id=patient_id))

    target_path = os.path.abspath(os.path.join(root_dir, normalized))
    if os.path.commonpath([os.path.abspath(root_dir), target_path]) != os.path.abspath(root_dir):
        abort(403)
    if not os.path.isfile(target_path):
        flash("File non trovato nella cartella dell'utente.", "danger")
        return redirect(url_for("patient_detail", patient_id=patient_id))

    return send_from_directory(os.path.dirname(target_path), os.path.basename(target_path), as_attachment=True, download_name=os.path.basename(target_path))


@app.route("/patients/<int:patient_id>/documents", methods=["POST"])
@require_login
def upload_patient_document(patient_id):
    if not user_has_permission("edit_patients"):
        abort(403)

    patient = get_db().execute("SELECT id FROM patients WHERE id = ?", (patient_id,)).fetchone()
    if patient is None:
        flash("Utente non trovato.", "danger")
        return redirect(url_for("patients"))

    file = request.files.get("file")
    if not file or file.filename == "":
        flash("Seleziona un file PDF da caricare.", "danger")
        return redirect(url_for("patient_detail", patient_id=patient_id))

    filename = file.filename.lower()
    if not filename.endswith(".pdf"):
        flash("Il documento deve essere in formato PDF.", "danger")
        return redirect(url_for("patient_detail", patient_id=patient_id))

    upload_dir = os.path.join(DATA_DIR, "uploads")
    os.makedirs(upload_dir, exist_ok=True)
    safe_file_name = f"{patient_id}_{uuid4().hex}.pdf"
    stored_path = os.path.join(upload_dir, safe_file_name)
    file.save(stored_path)

    conn = get_db()
    conn.execute(
        "INSERT INTO patient_documents (patient_id, original_name, stored_name, mime_type, file_path, file_size, uploaded_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            patient_id,
            file.filename,
            safe_file_name,
            file.mimetype or "application/pdf",
            stored_path,
            os.path.getsize(stored_path),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    conn.commit()
    conn.close()
    record_activity("upload_document", f"Caricato documento {file.filename} per utente id {patient_id}")
    flash("Documenti allegati salvati correttamente.", "success")
    return redirect(url_for("patient_detail", patient_id=patient_id))


@app.route("/patients/documents/<int:document_id>/download")
@require_login
def download_patient_document(document_id):
    if not user_has_permission("view_patients"):
        abort(403)

    conn = get_db()
    document = conn.execute("SELECT * FROM patient_documents WHERE id = ?", (document_id,)).fetchone()
    conn.close()
    if document is None:
        flash("Documento non trovato.", "danger")
        return redirect(url_for("patients"))

    if not os.path.exists(document["file_path"]):
        flash("File non disponibile sul disco.", "danger")
        return redirect(url_for("patient_detail", patient_id=document["patient_id"]))

    directory = os.path.dirname(document["file_path"])
    filename = os.path.basename(document["file_path"])
    return send_from_directory(directory, filename, as_attachment=True, download_name=document["original_name"])


@app.route("/patients/documents/<int:document_id>/delete", methods=["POST"])
@require_login
@require_permission("delete_patients")
def delete_patient_document(document_id):
    conn = get_db()
    document = conn.execute("SELECT * FROM patient_documents WHERE id = ?", (document_id,)).fetchone()
    if document:
        if os.path.exists(document["file_path"]):
            os.remove(document["file_path"])
        conn.execute("DELETE FROM patient_documents WHERE id = ?", (document_id,))
        conn.commit()
        patient_id = document["patient_id"]
        record_activity("delete_document", f"Eliminato documento {document['original_name']} per utente id {patient_id}")
        flash("Documento eliminato.", "success")
        return redirect(url_for("patient_detail", patient_id=patient_id))
    conn.close()
    flash("Documento non trovato.", "danger")
    return redirect(url_for("patients"))


@app.route("/patients/<int:patient_id>/visit", methods=["POST"])
@require_login
def add_visit(patient_id):
    if not user_has_permission("edit_appointments"):
        abort(403)
    visit_date = request.form.get("visit_date")
    visit_type = request.form.get("visit_type", "").strip()
    doctor_name = request.form.get("doctor_name", "").strip()
    summary = request.form.get("summary", "").strip()
    notes = request.form.get("notes", "").strip()
    outcome = request.form.get("outcome", "").strip()
    follow_up = request.form.get("follow_up", "").strip()

    if not visit_date or not visit_type:
        flash("Data e tipo visita sono obbligatori.", "danger")
        return redirect(url_for("patient_detail", patient_id=patient_id))

    conn = get_db()
    conn.execute(
        """
        INSERT INTO visits (patient_id, appointment_id, visit_date, visit_type, doctor_name, summary, notes, outcome, follow_up)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            patient_id,
            request.form.get("appointment_id") or None,
            visit_date,
            visit_type,
            doctor_name,
            summary,
            notes,
            outcome,
            follow_up,
        ),
    )
    conn.commit()
    conn.close()
    flash("Visita salvata correttamente.", "success")
    return redirect(url_for("patient_detail", patient_id=patient_id))


@app.route("/patients/<int:patient_id>/therapy", methods=["POST"])
@require_login
def add_therapy(patient_id):
    # Aggiunta terapia consentita a tutti gli utenti autenticati
    drug_name = request.form.get("drug_name", "").strip()
    active_ingredient = request.form.get("active_ingredient", "").strip()
    dosage = request.form.get("dosage", "").strip()
    schedule = request.form.get("schedule", "").strip()
    notes = request.form.get("notes", "").strip()
    pack_type = request.form.get("pack_type", "cp").strip() or "cp"
    units_per_box = int(request.form.get("units_per_box") or 0)
    stock_boxes = int(request.form.get("stock_boxes") or 0)
    expiry_date = request.form.get("expiry_date", "").strip()

    if not drug_name:
        flash("Il nome del farmaco è obbligatorio.", "danger")
        return redirect(url_for("patient_detail", patient_id=patient_id))

    conn = get_db()
    conn.execute(
        "INSERT INTO therapies (patient_id, drug_name, active_ingredient, dosage, schedule, notes, pack_type, units_per_box, stock_boxes, expiry_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (patient_id, drug_name, active_ingredient, dosage, schedule, notes, pack_type, units_per_box, stock_boxes, expiry_date),
    )
    conn.commit()
    conn.close()
    record_activity("add_therapy", f"Aggiunta terapia {drug_name} per utente id {patient_id}")
    flash("Terapia aggiunta.", "success")
    return redirect(url_for("patient_detail", patient_id=patient_id))


@app.route("/therapy/<int:therapy_id>/edit", methods=["GET", "POST"])
@require_login
def edit_therapy(therapy_id):
    conn = get_db()
    therapy = conn.execute("SELECT * FROM therapies WHERE id = ?", (therapy_id,)).fetchone()
    if not therapy:
        conn.close()
        flash("Terapia non trovata.", "danger")
        return redirect(url_for("dashboard"))

    patient_id = therapy["patient_id"]
    # Modifica terapia consentita a tutti gli utenti autenticati

    if request.method == "POST":
        drug_name = request.form.get("drug_name", "").strip()
        active_ingredient = request.form.get("active_ingredient", "").strip()
        dosage = request.form.get("dosage", "").strip()
        schedule = request.form.get("schedule", "").strip()
        notes = request.form.get("notes", "").strip()
        pack_type = request.form.get("pack_type", "cp").strip() or "cp"
        units_per_box = int(request.form.get("units_per_box") or 0)
        stock_boxes = int(request.form.get("stock_boxes") or 0)
        expiry_date = request.form.get("expiry_date", "").strip()

        if not drug_name:
            flash("Il nome del farmaco è obbligatorio.", "danger")
            return redirect(url_for("edit_therapy", therapy_id=therapy_id))

        conn.execute(
            "UPDATE therapies SET drug_name = ?, active_ingredient = ?, dosage = ?, schedule = ?, notes = ?, pack_type = ?, units_per_box = ?, stock_boxes = ?, expiry_date = ? WHERE id = ?",
            (drug_name, active_ingredient, dosage, schedule, notes, pack_type, units_per_box, stock_boxes, expiry_date, therapy_id),
        )
        conn.commit()
        conn.close()
        record_activity("edit_therapy", f"Modificata terapia id {therapy_id} per utente id {patient_id}")
        flash("Terapia aggiornata.", "success")
        return redirect(url_for("patient_detail", patient_id=patient_id))

    therapy = dict(therapy)
    conn.close()
    return render_template("therapy_edit.html", therapy=therapy)


@app.route("/therapy/<int:therapy_id>/delete", methods=["POST"])
@require_login
def delete_therapy(therapy_id):
    conn = get_db()
    therapy = conn.execute("SELECT * FROM therapies WHERE id = ?", (therapy_id,)).fetchone()
    if not therapy:
        conn.close()
        flash("Terapia non trovata.", "danger")
        return redirect(url_for("dashboard"))

    patient_id = therapy["patient_id"]
    # Eliminazione terapia consentita a tutti gli utenti autenticati

    conn.execute("DELETE FROM therapies WHERE id = ?", (therapy_id,))
    conn.commit()
    conn.close()
    record_activity("delete_therapy", f"Eliminata terapia id {therapy_id} per utente id {patient_id}")
    flash("Terapia eliminata.", "info")
    return redirect(url_for("patient_detail", patient_id=patient_id))


@app.route("/patients/edit/<int:patient_id>", methods=["GET", "POST"])
@require_login
def edit_patient(patient_id):
    if not user_has_permission("edit_patients"):
        abort(403)
    conn = get_db()
    patient = conn.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone()

    if request.method == "POST":
        conn.execute(
            """
            UPDATE patients
            SET first_name = ?, last_name = ?, birth_date = ?, cf = ?, phone = ?, email = ?, address = ?, notes = ?,
                diagnosis = ?, identifier_code = ?, ads = ?, family_members = ?, exemption = ?, mmg = ?, arrival_from = ?, arrival_from_other = ?
            WHERE id = ?
            """,
            (
                request.form.get("first_name", "").strip(),
                request.form.get("last_name", "").strip(),
                request.form.get("birth_date"),
                request.form.get("cf"),
                request.form.get("phone"),
                request.form.get("email"),
                request.form.get("address"),
                request.form.get("notes"),
                request.form.get("diagnosis"),
                request.form.get("identifier_code"),
                request.form.get("ads"),
                request.form.get("family_members"),
                request.form.get("exemption"),
                request.form.get("mmg"),
                request.form.get("arrival_from"),
                request.form.get("arrival_from_other"),
                patient_id,
            ),
        )
        conn.commit()
        conn.close()
        record_activity("edit_patient", f"Aggiornato utente id {patient_id}")
        flash("Utente aggiornato.", "success")
        return redirect(url_for("patients"))

    conn.close()
    return render_template("patient_form.html", patient=patient)


@app.route("/patients/delete/<int:patient_id>", methods=["POST"])
@require_login
@require_permission("delete_patients")
def delete_patient(patient_id):
    secondary_password = request.form.get("secondary_password", "")
    if secondary_password != SECONDARY_DELETE_PASSWORD:
        flash("Password secondaria non corretta.", "danger")
        abort(403)
    conn = get_db()
    conn.execute("DELETE FROM patients WHERE id = ?", (patient_id,))
    conn.commit()
    conn.close()
    record_activity("delete_patient", f"Eliminato utente id {patient_id}")
    flash("Utente eliminato.", "warning")
    return redirect(url_for("patients"))


@app.route("/appointments", methods=["GET", "POST"])
@require_login
def appointments():
    if not user_has_permission("view_appointments"):
        abort(403)

    if request.method == "POST":
        if not user_has_permission("edit_appointments"):
            abort(403)
        patient_id = request.form.get("patient_id")
        operator_name = request.form.get("operator_name", "").strip()
        appointment_date = request.form.get("appointment_date")
        appointment_time = request.form.get("appointment_time")
        appointment_type = request.form.get("appointment_type", "").strip()
        status = request.form.get("status", "prenotato")
        notes = request.form.get("notes")

        if not patient_id or not operator_name or not appointment_date or not appointment_time or not appointment_type:
            flash("Tutti i campi principali dell'appuntamento sono obbligatori.", "danger")
            return redirect(url_for("appointments"))

        conn = get_db()
        conn.execute(
            """
            INSERT INTO appointments (
                patient_id, operator_name, appointment_date, appointment_time,
                appointment_type, status, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (patient_id, operator_name, appointment_date, appointment_time, appointment_type, status, notes),
        )
        conn.commit()
        conn.close()
        record_activity("create_appointment", f"Creato appuntamento per utente id {patient_id}: {appointment_type} del {appointment_date} {appointment_time}")
        flash("Appuntamento creato correttamente.", "success")
        return redirect(url_for("appointments"))

    conn = get_db()
    all_appointments = conn.execute(
        """
        SELECT a.*, p.first_name || ' ' || p.last_name AS patient_name
        FROM appointments a
        JOIN patients p ON p.id = a.patient_id
        ORDER BY a.appointment_date DESC, a.appointment_time DESC
        """
    ).fetchall()
    patients = conn.execute("SELECT * FROM patients ORDER BY last_name ASC, first_name ASC").fetchall()
    conn.close()
    return render_template("appointments.html", appointments=all_appointments, patients=patients)


@app.route("/appointments/edit/<int:appointment_id>", methods=["GET", "POST"])
@require_login
def edit_appointment(appointment_id):
    if not user_has_permission("edit_appointments"):
        abort(403)
    conn = get_db()
    appointment = conn.execute("SELECT * FROM appointments WHERE id = ?", (appointment_id,)).fetchone()
    patients = conn.execute("SELECT * FROM patients ORDER BY last_name ASC, first_name ASC").fetchall()

    if request.method == "POST":
        conn.execute(
            """
            UPDATE appointments
            SET patient_id = ?, operator_name = ?, appointment_date = ?, appointment_time = ?,
                appointment_type = ?, status = ?, notes = ?
            WHERE id = ?
            """,
            (
                request.form.get("patient_id"),
                request.form.get("operator_name", "").strip(),
                request.form.get("appointment_date"),
                request.form.get("appointment_time"),
                request.form.get("appointment_type", "").strip(),
                request.form.get("status", "prenotato"),
                request.form.get("notes"),
                appointment_id,
            ),
        )
        conn.commit()
        conn.close()
        record_activity("edit_appointment", f"Aggiornato appuntamento id {appointment_id}")
        flash("Appuntamento aggiornato.", "success")
        return redirect(url_for("appointments"))

    conn.close()
    return render_template("appointment_form.html", appointment=appointment, patients=patients)


@app.route("/appointments/delete/<int:appointment_id>", methods=["POST"])
@require_login
def delete_appointment(appointment_id):
    if not user_has_permission("delete_appointments"):
        abort(403)
    conn = get_db()
    conn.execute("DELETE FROM appointments WHERE id = ?", (appointment_id,))
    conn.commit()
    conn.close()
    record_activity("delete_appointment", f"Eliminato appuntamento id {appointment_id}")
    flash("Appuntamento eliminato.", "warning")
    return redirect(url_for("appointments"))


def get_expiring_therapies():
    """Restituisce terapie in scadenza: warning (prossimo mese), danger (mese corrente/passato)"""
    conn = get_db()
    therapies = conn.execute(
        """
        SELECT t.id, t.drug_name, t.dosage, t.expiry_date, t.units_per_box, t.stock_boxes, t.pack_type,
               t.schedule, p.first_name || ' ' || p.last_name AS patient_name, p.id AS patient_id
        FROM therapies t
        JOIN patients p ON t.patient_id = p.id
        WHERE t.expiry_date IS NOT NULL AND t.expiry_date != ''
        ORDER BY t.expiry_date ASC
        """
    ).fetchall()
    conn.close()

    result = []
    today = datetime.now().date()
    current_month = today.month
    current_year = today.year

    for therapy in therapies:
        try:
            expiry_date = datetime.strptime(therapy["expiry_date"], "%Y-%m-%d").date()
            expiry_month = expiry_date.month
            expiry_year = expiry_date.year

            if expiry_date < today:
                # Scaduta
                status = "danger"
                message = "SCADUTA"
            elif expiry_year == current_year and expiry_month == current_month:
                # Scade nel mese corrente
                status = "danger"
                message = f"Scade il {expiry_date.strftime('%d/%m/%Y')}"
            elif expiry_year == current_year and expiry_month == current_month + 1:
                # Scade il mese prossimo
                status = "warning"
                message = f"Scade il {expiry_date.strftime('%d/%m/%Y')}"
            else:
                continue

            result.append({
                "id": therapy["id"],
                "drug_name": therapy["drug_name"],
                "patient_name": therapy["patient_name"],
                "patient_id": therapy["patient_id"],
                "expiry_date": therapy["expiry_date"],
                "status": status,
                "message": message,
                "type": "expiry"
            })
        except Exception:
            pass

    return result


def get_low_stock_therapies():
    """Restituisce terapie con giacenza che durerà meno di 11 giorni"""
    conn = get_db()
    therapies = conn.execute(
        """
        SELECT t.id, t.drug_name, t.dosage, t.schedule, t.units_per_box, t.stock_boxes, t.pack_type,
               p.first_name || ' ' || p.last_name AS patient_name, p.id AS patient_id
        FROM therapies t
        JOIN patients p ON t.patient_id = p.id
        WHERE t.units_per_box > 0 AND t.stock_boxes > 0
        """
    ).fetchall()
    conn.close()

    result = []
    for therapy in therapies:
        try:
            units_per_box = int(therapy["units_per_box"] or 0)
            stock_boxes = int(therapy["stock_boxes"] or 0)

            if units_per_box <= 0 or stock_boxes <= 0:
                continue

            total_units = units_per_box * stock_boxes
            dosage = (therapy["dosage"] or "").strip().lower()
            schedule = (therapy["schedule"] or "").strip()

            # Calcola la dose per giorno
            dose_match = re.search(r"(\d+(?:[.,]\d+)?)\s*(cp|capsule|compresse|gocce|fiale|bustine)\b", dosage)
            amount_per_dose = 1
            if dose_match:
                try:
                    amount_per_dose = int(float(dose_match.group(1)))
                except Exception:
                    amount_per_dose = 1

            schedule_times = re.findall(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b", schedule)
            schedule_count = len(schedule_times)

            text = dosage
            if "settimana" in text or "a settimana" in text or "alla settimana" in text:
                # Per le settimane: calcolo in giorni come settimane * 7
                weekly_match = re.search(r"(\d+)\s*volta\s*a\s*settimana|una?\s*volta\s*a\s*settimana", text)
                weekly_count = int(weekly_match.group(1)) if weekly_match and weekly_match.group(1) else 1
                units_per_week = weekly_count * amount_per_dose
                duration_days = (total_units // units_per_week) * 7
            elif "mese" in text or "mensile" in text or "al mese" in text or "a mese" in text:
                # Per i mesi: calcolo in giorni come mesi * 30
                monthly_match = re.search(r"(\d+)\s*volta\s*al\s*mese|una?\s*volta\s*al\s*mese", text)
                monthly_count = int(monthly_match.group(1)) if monthly_match and monthly_match.group(1) else 1
                units_per_month = monthly_count * amount_per_dose
                duration_days = (total_units // units_per_month) * 30
            else:
                # Per i giorni: calcolo standard
                daily_count = schedule_count if schedule_count > 0 else 1
                daily_match = re.search(r"(\d+)\s*volte\s*al\s*giorno", text)
                if daily_match:
                    daily_count = int(daily_match.group(1)) if schedule_count == 0 else schedule_count
                units_per_day = daily_count * amount_per_dose
                duration_days = max(0, total_units // units_per_day)

            # Se la durata è meno di 11 giorni, notifica
            if 0 < duration_days < 11:
                result.append({
                    "id": therapy["id"],
                    "drug_name": therapy["drug_name"],
                    "patient_name": therapy["patient_name"],
                    "patient_id": therapy["patient_id"],
                    "duration_days": duration_days,
                    "status": "warning",
                    "message": f"Giacenza esaurimento in {duration_days} giorni",
                    "type": "low_stock"
                })
        except Exception:
            pass

    return result


@app.route("/notifications")
@require_login
def notifications():
    if not user_has_permission("view_appointments"):
        abort(403)
    conn = get_db()
    reminders = conn.execute(
        """
        SELECT r.id, r.reminder_days, r.sent_at, r.message,
               p.first_name || ' ' || p.last_name AS patient_name,
               a.appointment_date, a.appointment_time, a.appointment_type
        FROM reminders r
        JOIN appointments a ON a.id = r.appointment_id
        JOIN patients p ON p.id = a.patient_id
        ORDER BY r.sent_at DESC
        """
    ).fetchall()
    conn.close()

    # Aggiungi notifiche di scadenza e carenza giacenza
    expiring_therapies = get_expiring_therapies()
    low_stock_therapies = get_low_stock_therapies()

    # Combina tutti gli avvisi
    all_alerts = expiring_therapies + low_stock_therapies

    return render_template("notifications.html", reminders=reminders, alerts=all_alerts)


@app.route("/calendar")
@require_login
def calendar():
    if not user_has_permission("view_appointments"):
        abort(403)
    selected_date = request.args.get("date") or datetime.now().strftime("%Y-%m-%d")
    try:
        selected = datetime.strptime(selected_date, "%Y-%m-%d").date()
    except ValueError:
        selected = datetime.now().date()

    week_start = selected - timedelta(days=selected.weekday())
    week_dates = [week_start + timedelta(days=i) for i in range(7)]

    conn = get_db()
    appointments = conn.execute(
        """
        SELECT a.*, p.first_name || ' ' || p.last_name AS patient_name
        FROM appointments a
        JOIN patients p ON p.id = a.patient_id
        WHERE a.appointment_date BETWEEN ? AND ?
        ORDER BY a.appointment_date ASC, a.appointment_time ASC
        """,
        (week_dates[0].isoformat(), week_dates[-1].isoformat()),
    ).fetchall()
    conn.close()

    appointments_by_date = {}
    for item in appointments:
        key = item["appointment_date"]
        appointments_by_date.setdefault(key, []).append(item)

    return render_template(
        "calendar.html",
        selected_date=selected,
        week_dates=week_dates,
        appointments_by_date=appointments_by_date,
    )


@app.route("/users")
@require_login
def users():
    if not user_has_permission("create_users"):
        abort(403)
    conn = get_db()
    users = conn.execute("SELECT * FROM users WHERE is_archived = 0 AND role != 'admin' ORDER BY role, fullname").fetchall()
    archived_users = conn.execute("SELECT * FROM users WHERE is_archived = 1 AND role != 'admin' ORDER BY role, fullname").fetchall()
    conn.close()
    return render_template("users.html", users=users, archived_users=archived_users)


@app.route("/patients/<int:patient_id>/open-folder", methods=["GET", "POST"])
@require_login
@require_permission("open_user_folder")
def open_patient_folder(patient_id):
    conn = get_db()
    patient_row = conn.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone()
    patient = dict(patient_row) if patient_row is not None else None
    conn.close()

    if patient is None:
        flash("Utente non trovato.", "danger")
        return redirect(url_for("patients"))

    patient_code = (patient.get("identifier_code") or "").strip() or f"{patient['first_name']} {patient['last_name']}".strip()
    folder_path = os.path.join(DATA_DIR, "utenti", sanitize_user_folder_name(patient_code))
    if not os.path.exists(folder_path):
        create_user_folder_tree(patient_code)

    try:
        if hasattr(os, "startfile"):
            os.startfile(folder_path)
            flash("Cartella utente aperta con successo.", "success")
        else:
            flash("Apertura cartella non supportata su questo sistema operativo.", "warning")
    except OSError:
        flash("Impossibile aprire la cartella dell'utente.", "danger")

    return redirect(url_for("patients"))


@app.route("/users/<int:user_id>/open-folder", methods=["GET", "POST"])
@require_login
@require_permission("open_user_folder")
def open_user_folder(user_id):
    return open_patient_folder(user_id)


@app.route("/audit")
@require_login
def audit():
    if not user_has_permission("manage_users"):
        abort(403)

    selected_user_id = request.args.get("user_id", "").strip()
    selected_action = request.args.get("action", "").strip()
    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()

    conn = get_db()
    users = conn.execute("SELECT id, username, fullname, role FROM users WHERE is_archived = 0 AND role != 'admin' ORDER BY fullname").fetchall()
    actions = [row[0] for row in conn.execute("SELECT DISTINCT action FROM access_log ORDER BY action").fetchall()]

    query = """
        SELECT a.*, u.username, u.fullname, u.role
        FROM access_log a
        JOIN users u ON u.id = a.user_id
        WHERE 1 = 1
    """
    params = []

    if selected_user_id:
        query += " AND a.user_id = ?"
        params.append(int(selected_user_id))

    if selected_action:
        query += " AND a.action = ?"
        params.append(selected_action)

    if date_from:
        query += " AND datetime(a.created_at) >= datetime(?)"
        params.append(f"{date_from} 00:00:00")

    if date_to:
        query += " AND datetime(a.created_at) <= datetime(?)"
        params.append(f"{date_to} 23:59:59")

    query += " AND datetime(a.created_at) >= datetime('now', '-1 month') ORDER BY a.created_at DESC"
    entries = conn.execute(query, params).fetchall()
    conn.close()

    return render_template(
        "audit.html",
        entries=entries,
        users=users,
        actions=actions,
        selected_user_id=selected_user_id,
        selected_action=selected_action,
        date_from=date_from,
        date_to=date_to,
        ROLE_LABELS=ROLE_LABELS,
    )


@app.route("/audit/export")
@require_login
def export_audit_csv():
    if not user_has_permission("manage_users"):
        abort(403)

    conn = get_db()
    entries = conn.execute(
        """
        SELECT u.username, u.fullname, u.role, a.action, a.details, a.created_at
        FROM access_log a
        JOIN users u ON u.id = a.user_id
        WHERE datetime(a.created_at) >= datetime('now', '-1 month')
        ORDER BY a.created_at DESC
        """
    ).fetchall()
    conn.close()

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["username", "fullname", "role", "action", "details", "created_at"])
    for row in entries:
        writer.writerow([
            row["username"],
            row["fullname"],
            row["role"],
            row["action"],
            row["details"],
            it_date(row["created_at"]),
        ])

    response = Response(output.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=audit_ultimi_30_giorni.csv"
    return response


@app.route("/users/create", methods=["POST"])
@require_login
def create_user():
    if not user_has_permission("create_users"):
        abort(403)
    username = request.form.get("username", "").strip()
    fullname = request.form.get("fullname", "").strip()
    role = request.form.get("role", "").strip()
    password = request.form.get("password", "").strip()

    if not username or not fullname or not role or not password:
        flash("Tutti i campi utente sono obbligatori.", "danger")
        return redirect(url_for("users"))

    conn = get_db()
    existing_user = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    if existing_user is not None:
        conn.close()
        flash("Username già esistente.", "danger")
        return redirect(url_for("users"))

    conn.execute(
        "INSERT INTO users (username, fullname, role, password, is_archived) VALUES (?, ?, ?, ?, 0)",
        (username, fullname, role, password),
    )
    conn.commit()
    conn.close()
    record_activity("create_user", f"Creato operatore {fullname} ({role})")
    flash("Operatore creato correttamente.", "success")
    return redirect(url_for("users"))


@app.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@require_login
def edit_user(user_id):
    if not user_has_permission("manage_users"):
        abort(403)
    
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    
    if user is None:
        conn.close()
        flash("Operatore non trovato.", "danger")
        return redirect(url_for("users"))
    
    if request.method == "POST":
        new_username = request.form.get("username", "").strip()
        new_fullname = request.form.get("fullname", "").strip()
        new_role = request.form.get("role", "").strip()
        
        if not new_username or not new_fullname or not new_role:
            flash("Tutti i campi sono obbligatori.", "danger")
            conn.close()
            return redirect(url_for("edit_user", user_id=user_id))
        
        # Controlla se il nuovo username è già in uso da un altro utente
        existing_user = conn.execute(
            "SELECT id FROM users WHERE username = ? AND id != ?", 
            (new_username, user_id)
        ).fetchone()
        if existing_user is not None:
            conn.close()
            flash("Username già utilizzato da un altro operatore.", "danger")
            return redirect(url_for("edit_user", user_id=user_id))
        
        # Aggiorna i dati
        conn.execute(
            "UPDATE users SET username = ?, fullname = ?, role = ? WHERE id = ?",
            (new_username, new_fullname, new_role, user_id)
        )
        conn.commit()
        conn.close()
        record_activity("edit_user", f"Modificato operatore id {user_id}")
        flash("Operatore modificato correttamente.", "success")
        return redirect(url_for("users"))
    
    conn.close()
    return render_template("edit_user.html", user=dict(user))


@app.route("/users/<int:user_id>/archive", methods=["POST"])
@require_login
def archive_user(user_id):
    if not user_has_permission("manage_users"):
        abort(403)
    conn = get_db()
    conn.execute("UPDATE users SET is_archived = 1 WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    record_activity("archive_user", f"Archiviato operatore id {user_id}")
    flash("Operatore archiviato.", "info")
    return redirect(url_for("users"))


@app.route("/users/<int:user_id>/restore", methods=["POST"])
@require_login
def restore_user(user_id):
    if not user_has_permission("manage_users"):
        abort(403)
    conn = get_db()
    conn.execute("UPDATE users SET is_archived = 0 WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    record_activity("restore_user", f"Ripristinato operatore id {user_id}")
    flash("Operatore ripristinato.", "success")
    return redirect(url_for("users"))


@app.route("/patients/<int:patient_id>/archive", methods=["POST"])
@require_login
def archive_patient(patient_id):
    if not user_has_permission("delete_patients"):
        abort(403)
    conn = get_db()
    conn.execute("UPDATE patients SET is_archived = 1 WHERE id = ?", (patient_id,))
    conn.commit()
    conn.close()
    record_activity("archive_patient", f"Archiviato utente id {patient_id}")
    flash("Utente archiviato.", "info")
    return redirect(url_for("patients"))


@app.route("/patients/<int:patient_id>/restore", methods=["POST"])
@require_login
def restore_patient(patient_id):
    if not user_has_permission("delete_patients"):
        abort(403)
    conn = get_db()
    conn.execute("UPDATE patients SET is_archived = 0 WHERE id = ?", (patient_id,))
    conn.commit()
    conn.close()
    record_activity("restore_patient", f"Ripristinato utente id {patient_id}")
    flash("Utente ripristinato.", "success")
    return redirect(url_for("patients"))


@app.route("/users/<int:user_id>/delete", methods=["POST"])
@require_login
def delete_user(user_id):
    if not user_has_permission("manage_users"):
        abort(403)
    secondary_password = request.form.get("secondary_password", "")
    if secondary_password != SECONDARY_DELETE_PASSWORD:
        flash("Password secondaria non corretta.", "danger")
        abort(403)

    conn = get_db()
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    record_activity("delete_user", f"Eliminato operatore id {user_id}")
    flash("Operatore eliminato definitivamente.", "warning")
    return redirect(url_for("users"))


@app.route("/users/<int:user_id>/reset_password", methods=["POST"])
@require_login
def reset_user_password(user_id):
    if not user_has_permission("manage_users"):
        abort(403)

    secondary_password = request.form.get("secondary_password", "")
    if secondary_password != SECONDARY_DELETE_PASSWORD:
        flash("Password secondaria non corretta.", "danger")
        abort(403)

    new_password = request.form.get("new_password", "").strip()
    if not new_password:
        flash("Password nuova non valida.", "danger")
        return redirect(url_for("users"))

    conn = get_db()
    conn.execute("UPDATE users SET password = ? WHERE id = ?", (new_password, user_id))
    conn.commit()
    conn.close()
    record_activity("reset_password", f"Reset password per operatore id {user_id}")
    flash("Password reimpostata correttamente.", "success")
    return redirect(url_for("users"))


if __name__ == "__main__":
    init_db()
    reminder_thread = threading.Thread(target=reminder_worker, daemon=True)
    reminder_thread.start()
    
    # Avvia il thread per le notifiche di Windows (solo su Windows)
    if TOAST_AVAILABLE:
        notification_thread = threading.Thread(target=send_windows_notifications, daemon=True)
        notification_thread.start()
    
    app.run(debug=True, host="127.0.0.1", port=5000)
