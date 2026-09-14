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

## Avvio su chiavetta USB senza installare Python
Per mostrare il progetto a un cliente solo da chiavetta USB, copia il contenuto completo della cartella del progetto e il runtime Python portabile nella stessa struttura:

```text
USB:\prova-gestionale\
    app.py
    requirements.txt
    templates\
    static\
    data\
    scripts\
    python\           <-- runtime Python portabile o la cartella portable_python\
    start_app.bat
```

Il file `start_app.bat` cerca automaticamente una delle seguenti cartelle:
- `python\python.exe`
- `portable_python\python.exe`

Se il runtime non è presente, il file visualizza un messaggio chiaro e si ferma. La `python\` o `portable_python\` deve essere copiata insieme al progetto. Non usare un `python.exe` assoluto come in una installazione locale.

Per lanciare:
1. Inserisci la chiavetta.
2. Apri la cartella `prova-gestionale`.
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
