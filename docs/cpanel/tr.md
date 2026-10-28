# Ravix'i cPanel paylaşımlı hostinge kurma

![](../img/cpanel-flow.svg)

1. cPanel > Setup Python App > Create Application: Python 3.11 veya üstü, Application root = ravix, başlangıç dosyası passenger_wsgi.py, giriş noktası application.
2. ravix-<sürüm>-cpanel.zip dosyasını File Manager ile ravix klasörüne yükleyip Extract edin.
3. Run Pip Install (requirements-cpanel.txt) ve ardından Restart.
4. MySQL® Databases'te bir veritabanı ve kullanıcı oluşturun (All privileges).
5. Siteyi açın; ravix-data/install-key.txt dosyasındaki kurulum kodunu girin ve adımları izleyin.
6. Son sayfada gösterilen Cron satırını ekleyin (5 dakikada bir).

503 hatasında ravix-data/logs/startup-error.txt dosyasını okuyun; Python 3.11+ ve paketlerin kurulu olduğunu kontrol edin.

Full guide: [English](../cpanel.md) · [فارسی](../cpanel.fa.md)
