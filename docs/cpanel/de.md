# Ravix auf cPanel-Shared-Hosting installieren

![](../img/cpanel-flow.svg)

1. cPanel > Setup Python App > Create Application: Python 3.11 oder neuer, Application root = ravix, Startdatei passenger_wsgi.py, Entry point application.
2. ravix-<Version>-cpanel.zip per File Manager in den Ordner ravix hochladen und Extract wählen.
3. Run Pip Install (requirements-cpanel.txt) ausführen, dann Restart.
4. In MySQL® Databases Datenbank und Benutzer anlegen (All privileges).
5. Website öffnen und den Installationscode aus ravix-data/install-key.txt eingeben; den Schritten folgen.
6. Die auf der letzten Seite angezeigte Cron-Zeile eintragen (alle 5 Minuten).

Bei Fehler 503 ravix-data/logs/startup-error.txt lesen; Python 3.11+ und installierte Pakete prüfen.

Full guide: [English](../cpanel.md) · [فارسی](../cpanel.fa.md)
