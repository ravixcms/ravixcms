# Instalar Ravix en hosting compartido con cPanel

![](../img/cpanel-flow.svg)

1. cPanel > Setup Python App > Create Application: Python 3.11 o superior, Application root = ravix, archivo de inicio passenger_wsgi.py, entry point application.
2. Suba ravix-<versión>-cpanel.zip con File Manager a la carpeta ravix y use Extract.
3. Pulse Run Pip Install (requirements-cpanel.txt) y luego Restart.
4. Cree una base de datos y un usuario en MySQL® Databases (All privileges).
5. Abra su sitio e introduzca el código de instalación de ravix-data/install-key.txt; siga los pasos.
6. Añada la línea de Cron que muestra la última página (cada 5 minutos).

Ante un error 503 lea ravix-data/logs/startup-error.txt; compruebe Python 3.11+ y los paquetes instalados.

Full guide: [English](../cpanel.md) · [فارسی](../cpanel.fa.md)
