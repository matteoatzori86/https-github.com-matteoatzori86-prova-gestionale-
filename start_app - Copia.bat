@echo off
cd /d "%~dp0"

REM Usa sempre Python già installato sul PC dove lo stai mostrando.
REM Se vuoi farlo partire da USB, sostituisci il percorso sotto con il tuo percorso esatto.

"C:\Users\Matteo\AppData\Local\Programs\Python\Python312\python.exe" app.py
pause
