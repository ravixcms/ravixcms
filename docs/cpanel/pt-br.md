# Instalar o Ravix em hospedagem compartilhada com cPanel

![](../img/cpanel-flow.svg)

1. cPanel > Setup Python App > Create Application: Python 3.11 ou superior, Application root = ravix, arquivo de inicialização passenger_wsgi.py, entry point application.
2. Envie ravix-<versão>-cpanel.zip pelo File Manager para a pasta ravix e use Extract.
3. Clique em Run Pip Install (requirements-cpanel.txt) e depois em Restart.
4. Crie um banco e um usuário em MySQL® Databases (All privileges).
5. Abra o site e informe o código de instalação de ravix-data/install-key.txt; siga as etapas.
6. Adicione a linha de Cron exibida na última página (a cada 5 minutos).

Em caso de erro 503, leia ravix-data/logs/startup-error.txt; confira Python 3.11+ e os pacotes instalados.

Full guide: [English](../cpanel.md) · [فارسی](../cpanel.fa.md)
