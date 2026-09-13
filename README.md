# Gestionale socio-sanitario MVP

Applicazione desktop-first per gestire:
- utenti/pazienti
- appuntamenti
- operatori e ruoli
- cartelle documentali dei pazienti

## Requisiti
- Python 3.12+
- pip
- Windows (per la creazione automatica delle cartelle tramite `os.startfile`)

## Avvio locale
```bash
cd "C:\Users\Matteo\Desktop\prova gestionale"
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
python app.py
```

Poi aprire nel browser:
- http://127.0.0.1:5000

## Avvio su PC aziendale senza installare Python
Se il computer non consente installazioni, usa un runtime Python portabile:
1. Scarica una versione portable di Python 3.12 da python.org.
2. Estrai la cartella in una delle posizioni:
   - nella cartella del progetto: `python\`
   - o in `portable_python\`
   - o direttamente sul USB: `X:\python\`
3. Fai doppio click su `start_app.bat`.

Il file di avvio cercherà automaticamente i runtime portatili.

## Accesso iniziale
Credenziali di default del coordinatore:
- username: admin
- password: admin123

## Ruoli presenti
- Coordinatore
- Vice coordinatore
- Psicologa
- Psichiatra
- Infermiere
- OSS
- Educatore

## Regole funzionali importanti
- Le cartelle e le sottocartelle sono create solo per i pazienti/utenti.
- I dati dell’operatore non hanno cartella personale.
- La sezione “dimessi” si trova nella gestione dei pazienti/utenti, non nel personale.
- L’apertura della cartella paziente è consentita solo a coordinatore e vice coordinatore.

## Cartelle pazienti
Le cartelle vengono create automaticamente nella cartella:
- `data\utenti\<codice utente>`

Il nome della cartella corrisponde al codice identificativo del paziente (`identifier_code`), così da evitare confusioni con i dati del personale.

Con una struttura di sotto-cartelle dedicata alla documentazione socio-sanitaria.
