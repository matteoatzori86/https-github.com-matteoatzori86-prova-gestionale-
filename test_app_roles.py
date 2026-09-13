import io
import os
import shutil
import unittest

import app as app_module


class RolePermissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app_module.app.config['TESTING'] = True
        app_module.init_db()
        cls.client = app_module.app.test_client()

    def setUp(self):
        self.client.get('/logout')
        conn = app_module.get_db()
        conn.execute("DELETE FROM users WHERE username IN ('psicologa', 'psichiatra', 'infermiera', 'oss1', 'educatore', 'coordinatore_test')")
        conn.execute(
            "INSERT INTO users (username, fullname, role, password, is_archived) VALUES (?, ?, ?, ?, 0)",
            ('psicologa', 'Psicologa Test', 'psicologa', 'demo123'),
        )
        conn.execute(
            "INSERT INTO users (username, fullname, role, password, is_archived) VALUES (?, ?, ?, ?, 0)",
            ('infermiera', 'Infermiera Test', 'infermiere', 'demo123'),
        )
        conn.execute(
            "INSERT INTO users (username, fullname, role, password, is_archived) VALUES (?, ?, ?, ?, 0)",
            ('oss1', 'OSS Test', 'oss', 'demo123'),
        )
        conn.execute(
            "INSERT INTO users (username, fullname, role, password, is_archived) VALUES (?, ?, ?, ?, 0)",
            ('educatore', 'Educatore Test', 'educatore', 'demo123'),
        )
        conn.execute(
            "INSERT INTO users (username, fullname, role, password, is_archived) VALUES (?, ?, ?, ?, 0)",
            ('coordinatore_test', 'Coordinatore Test', 'coordinatore', 'demo123'),
        )
        conn.commit()
        conn.close()

    def test_login_works_for_admin(self):
        response = self.client.post('/login', data={'username': 'admin', 'password': 'admin123'}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Dashboard', response.data)

    def test_psychologist_cannot_modify_patients(self):
        self.client.post('/login', data={'username': 'psicologa', 'password': 'demo123'}, follow_redirects=True)
        response = self.client.post('/patients', data={'first_name': 'Paolo', 'last_name': 'Rossi'}, follow_redirects=True)
        self.assertEqual(response.status_code, 403)

    def test_infermiere_can_modify_appointments_but_not_users(self):
        self.client.post('/login', data={'username': 'infermiera', 'password': 'demo123'}, follow_redirects=True)
        response = self.client.post('/appointments', data={
            'patient_id': '1',
            'operator_name': 'Infermiera Test',
            'appointment_date': '2026-09-10',
            'appointment_time': '09:00',
            'appointment_type': 'Controllo',
            'status': 'prenotato',
            'notes': 'Test'
        }, follow_redirects=True)
        self.assertIn(response.status_code, (200, 302))

        response = self.client.post('/users/create', data={
            'username': 'forbidden',
            'fullname': 'Nessuno',
            'role': 'infermiere',
            'password': 'abc'
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 403)

    def test_coordinator_delete_requires_second_password(self):
        self.client.post('/login', data={'username': 'coordinatore_test', 'password': 'demo123'}, follow_redirects=True)
        user_id = app_module.get_db().execute("SELECT id FROM users WHERE username = ?", ('psicologa',)).fetchone()['id']
        response = self.client.post(f'/users/{user_id}/delete', data={'secondary_password': 'wrong'}, follow_redirects=True)
        self.assertEqual(response.status_code, 403)

        response = self.client.post(f'/users/{user_id}/delete', data={'secondary_password': app_module.SECONDARY_DELETE_PASSWORD}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(app_module.get_db().execute("SELECT id FROM users WHERE username = ?", ('psicologa',)).fetchone())

    def test_audit_page_filters_by_action(self):
        self.client.post('/login', data={'username': 'admin', 'password': 'admin123'}, follow_redirects=True)
        admin = app_module.get_db().execute("SELECT id FROM users WHERE username = ?", ('admin',)).fetchone()
        conn = app_module.get_db()
        conn.execute("DELETE FROM access_log WHERE action IN (?, ?)", ('audit_filter_visible', 'audit_filter_hidden'))
        conn.execute(
            "INSERT INTO access_log (user_id, action, details, created_at) VALUES (?, ?, ?, ?)",
            (admin['id'], 'audit_filter_visible', 'Visible entry', '2026-09-01 10:00:00'),
        )
        conn.execute(
            "INSERT INTO access_log (user_id, action, details, created_at) VALUES (?, ?, ?, ?)",
            (admin['id'], 'audit_filter_hidden', 'Hidden entry', '2026-09-01 11:00:00'),
        )
        conn.commit()
        conn.close()

        response = self.client.get('/audit?action=audit_filter_visible', follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'audit_filter_visible', response.data)
        self.assertIn(b'Visible entry', response.data)
        self.assertNotIn(b'Hidden entry', response.data)

    def test_patient_pdf_upload_and_audit_export(self):
        self.client.post('/login', data={'username': 'admin', 'password': 'admin123'}, follow_redirects=True)
        conn = app_module.get_db()
        patient = conn.execute("SELECT id FROM patients ORDER BY id LIMIT 1").fetchone()
        conn.close()
        if patient is None:
            patient = {'id': 1}

        pdf_data = b'%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF'
        response = self.client.post(
            f'/patients/{patient["id"]}/documents',
            data={'file': (io.BytesIO(pdf_data), 'esame.pdf')},
            content_type='multipart/form-data',
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'esame.pdf', response.data)

        export = self.client.get('/audit/export')
        self.assertEqual(export.status_code, 200)
        self.assertIn(b'username', export.data)
        self.assertIn(b'action', export.data)

    def test_patient_folder_browser_shows_pdfs_and_allows_upload_in_subfolder(self):
        self.client.post('/login', data={'username': 'admin', 'password': 'admin123'}, follow_redirects=True)

        conn = app_module.get_db()
        conn.execute(
            "INSERT INTO patients (first_name, last_name, birth_date, cf, phone, email, address, notes, diagnosis, identifier_code, ads, family_members, exemption, mmg, arrival_from, arrival_from_other) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                'Cartella', 'Browser', '1988-05-05', 'BRWXYZ88E05H501P', '3331112222', 'browser@test.it',
                'Via Browser 2', 'note', 'diagnosi browser', 'BROWSER-99', 'ads', 'fam', 'esenzione', 'MMG', 'Casa', ''
            ),
        )
        conn.commit()
        patient = conn.execute("SELECT id, identifier_code FROM patients WHERE identifier_code = ?", ('BROWSER-99',)).fetchone()
        conn.close()

        patient_dir = os.path.join(app_module.DATA_DIR, 'utenti', 'BROWSER-99')
        os.makedirs(os.path.join(patient_dir, 'sociosanitaria', '1. Anagrafica e trattamento dei dati'), exist_ok=True)
        existing_pdf = os.path.join(patient_dir, 'sociosanitaria', '1. Anagrafica e trattamento dei dati', 'esistente.pdf')
        with open(existing_pdf, 'wb') as f:
            f.write(b'%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF')

        response = self.client.get(f'/patients/{patient["id"]}', follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Cartella utente', response.data)
        self.assertIn(b'esistente.pdf', response.data)

        upload_response = self.client.post(
            f'/patients/{patient["id"]}/folder-upload',
            data={
                'folder_path': 'sociosanitaria/1. Anagrafica e trattamento dei dati',
                'file': (io.BytesIO(b'%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF'), 'nuovo.pdf')
            },
            content_type='multipart/form-data',
            follow_redirects=True,
        )
        self.assertEqual(upload_response.status_code, 200)
        self.assertTrue(os.path.exists(os.path.join(patient_dir, 'sociosanitaria', '1. Anagrafica e trattamento dei dati', 'nuovo.pdf')))

        conn = app_module.get_db()
        conn.execute("DELETE FROM patients WHERE identifier_code = ?", ('BROWSER-99',))
        conn.commit()
        conn.close()
        shutil.rmtree(patient_dir, ignore_errors=True)

    def test_therapy_fields_and_stock_duration_are_shown_on_patient_detail(self):
        self.client.post('/login', data={'username': 'admin', 'password': 'admin123'}, follow_redirects=True)
        conn = app_module.get_db()
        patient = conn.execute("SELECT id FROM patients ORDER BY id LIMIT 1").fetchone()
        if patient is None:
            conn.execute(
                "INSERT INTO patients (first_name, last_name, birth_date, cf, phone, email, address, notes, diagnosis, identifier_code, ads, family_members, exemption, mmg, arrival_from, arrival_from_other) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ('Test', 'Therapy', '1990-01-01', 'TRP12345', '3330000000', 'therapy@test.it', 'Via Test 1', 'note', 'diagnosi', 'THERAPY-1', 'ads', 'fam', 'esenzione', 'MMG', 'Casa', '')
            )
            conn.commit()
            patient = conn.execute("SELECT id FROM patients WHERE identifier_code = ?", ('THERAPY-1',)).fetchone()

        conn.execute(
            "INSERT INTO therapies (patient_id, drug_name, active_ingredient, dosage, schedule, notes, pack_type, units_per_box, stock_boxes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (patient['id'], 'Aspirina', 'Acido acetilsalicilico', '1 cp', '08:00,20:00', 'test', 'cp', 30, 2)
        )
        conn.commit()
        conn.close()

        response = self.client.get(f'/patients/{patient["id"]}', follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Aspirina', response.data)
        self.assertIn(b'Acido acetilsalicilico', response.data)
        self.assertIn(b'Giacenza', response.data)
        self.assertIn(b'2 scatole', response.data)

    def test_therapy_stock_summary_counts_days_from_schedule_times(self):
        therapy = {
            "pack_type": "cp",
            "units_per_box": 30,
            "stock_boxes": 1,
            "dosage": "1 cp",
            "schedule": "08:00,12:00,20:00",
            "expiry_date": "",
        }

        summary = app_module.therapy_stock_summary(therapy)
        self.assertEqual(summary["stock_label"], "1 scatole · 30 cp")
        self.assertEqual(summary["duration_label"], "10 giorni")

    def test_patient_search_by_identifier_code(self):
        self.client.post('/login', data={'username': 'admin', 'password': 'admin123'}, follow_redirects=True)
        conn = app_module.get_db()
        conn.execute(
            "INSERT INTO patients (first_name, last_name, birth_date, cf, phone, email, address, notes, diagnosis, identifier_code, ads, family_members, exemption, mmg, arrival_from, arrival_from_other) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                'Ricerca', 'Codice', '1990-01-01', 'RSSABC90A01H501U', '3330000000', 'ricerca@test.it',
                'Via Test 1', 'note', 'diagnosi test', 'FILTRO-SEARCH-123', 'ads', 'fam', 'esenzione', 'MMG', 'Casa', ''
            ),
        )
        conn.commit()
        conn.close()

        response = self.client.get('/patients?q=FILTRO-SEARCH-123', follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Ricerca', response.data)
        self.assertIn(b'FILTRO-SEARCH-123', response.data)

    def test_operator_creation_does_not_create_folder_tree(self):
        self.client.post('/login', data={'username': 'admin', 'password': 'admin123'}, follow_redirects=True)
        username = 'operatore_test_senza_cartella'
        conn = app_module.get_db()
        conn.execute("DELETE FROM users WHERE username = ?", (username,))
        conn.commit()
        conn.close()
        base_dir = os.path.join(app_module.DATA_DIR, 'utenti', username)
        if os.path.exists(base_dir):
            shutil.rmtree(base_dir)

        response = self.client.post('/users/create', data={
            'username': username,
            'fullname': 'Operatore Test Senza Cartella',
            'role': 'infermiere',
            'password': 'abc123'
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(os.path.exists(base_dir))

        app_module.get_db().execute("DELETE FROM users WHERE username = ?", (username,))
        app_module.get_db().commit()

    def test_patient_creation_creates_folder_tree(self):
        self.client.post('/login', data={'username': 'admin', 'password': 'admin123'}, follow_redirects=True)
        first_name = 'Cartella'
        last_name = 'Paziente'
        patient_code = 'PZ-01'
        patient_dir = os.path.join(app_module.DATA_DIR, 'utenti', patient_code)
        if os.path.exists(patient_dir):
            shutil.rmtree(patient_dir)

        response = self.client.post('/patients', data={
            'first_name': first_name,
            'last_name': last_name,
            'birth_date': '1990-01-01',
            'cf': 'RSSABC90A01H501U',
            'phone': '3330000000',
            'email': 'paziente@test.it',
            'address': 'Via Test 1',
            'notes': 'nota',
            'diagnosis': 'diagnosi',
            'identifier_code': patient_code,
            'ads': 'ads',
            'family_members': 'fam',
            'exemption': 'esenzione',
            'mmg': 'MMG',
            'arrival_from': 'Casa',
            'arrival_from_other': ''
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(os.path.isdir(os.path.join(patient_dir, 'sociosanitaria')))
        self.assertTrue(os.path.isdir(os.path.join(patient_dir, 'sociosanitaria', '1. Anagrafica e trattamento dei dati')))

        conn = app_module.get_db()
        patient = conn.execute("SELECT id FROM patients WHERE first_name = ? AND last_name = ? AND identifier_code = ?", (first_name, last_name, patient_code)).fetchone()
        if patient:
            conn.execute("DELETE FROM patients WHERE id = ?", (patient['id'],))
        conn.commit()
        conn.close()
        shutil.rmtree(patient_dir, ignore_errors=True)

    def test_only_coordinator_and_vicecoordinator_can_open_user_folder(self):
        self.client.get('/logout')
        conn = app_module.get_db()
        conn.execute("DELETE FROM users WHERE username IN ('vicecoordinatore_test', 'lettore_test')")
        conn.execute(
            "INSERT INTO users (username, fullname, role, password, is_archived) VALUES (?, ?, ?, ?, 0)",
            ('vicecoordinatore_test', 'Vice Coordinatore Test', 'vicecoordinatore', 'demo123'),
        )
        conn.execute(
            "INSERT INTO users (username, fullname, role, password, is_archived) VALUES (?, ?, ?, ?, 0)",
            ('lettore_test', 'Lettore Test', 'psicologa', 'demo123'),
        )
        conn.commit()
        target_user_id = conn.execute("SELECT id FROM users WHERE username = ?", ('lettore_test',)).fetchone()['id']
        conn.close()

        self.client.post('/login', data={'username': 'lettore_test', 'password': 'demo123'}, follow_redirects=True)
        response = self.client.get(f'/users/{target_user_id}/open-folder', follow_redirects=True)
        self.assertEqual(response.status_code, 403)

        self.client.get('/logout')
        self.client.post('/login', data={'username': 'vicecoordinatore_test', 'password': 'demo123'}, follow_redirects=True)
        response = self.client.get(f'/users/{target_user_id}/open-folder', follow_redirects=True)
        self.assertEqual(response.status_code, 200)

        conn = app_module.get_db()
        conn.execute("DELETE FROM users WHERE username IN ('vicecoordinatore_test', 'lettore_test')")
        conn.commit()
        conn.close()


if __name__ == '__main__':
    unittest.main()
