# Installer Ravix sur un hébergement mutualisé cPanel

![](../img/cpanel-flow.svg)

1. cPanel > Setup Python App > Create Application : Python 3.11 ou plus, Application root = ravix, fichier de démarrage passenger_wsgi.py, point d'entrée application.
2. Téléversez ravix-<version>-cpanel.zip dans le dossier ravix avec File Manager, puis Extract.
3. Cliquez sur Run Pip Install (requirements-cpanel.txt), puis Restart.
4. Créez une base et un utilisateur dans MySQL® Databases (All privileges).
5. Ouvrez votre site et saisissez le code d'installation de ravix-data/install-key.txt ; suivez les étapes.
6. Ajoutez la ligne Cron affichée à la fin (toutes les 5 minutes).

En cas d'erreur 503, lisez ravix-data/logs/startup-error.txt ; vérifiez Python 3.11+ et les paquets.

Full guide: [English](../cpanel.md) · [فارسی](../cpanel.fa.md)
