# Установка Ravix на виртуальный хостинг cPanel

![](../img/cpanel-flow.svg)

1. cPanel > Setup Python App > Create Application: Python 3.11 или новее, Application root = ravix, файл запуска passenger_wsgi.py, точка входа application.
2. Загрузите ravix-<версия>-cpanel.zip через File Manager в папку ravix и выберите Extract.
3. Нажмите Run Pip Install (requirements-cpanel.txt), затем Restart.
4. В MySQL® Databases создайте базу и пользователя (All privileges).
5. Откройте сайт и введите код установки из ravix-data/install-key.txt; выполните шаги.
6. Добавьте строку Cron с последней страницы (каждые 5 минут).

При ошибке 503 прочитайте ravix-data/logs/startup-error.txt; проверьте Python 3.11+ и установленные пакеты.

Full guide: [English](../cpanel.md) · [فارسی](../cpanel.fa.md)
