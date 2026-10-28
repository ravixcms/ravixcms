# 在 cPanel 共享主机上安装 Ravix

![](../img/cpanel-flow.svg)

1. cPanel > Setup Python App > Create Application：Python 3.11 或更高，Application root = ravix，启动文件 passenger_wsgi.py，入口 application。
2. 用 File Manager 把 ravix-<版本>-cpanel.zip 上传到 ravix 文件夹并 Extract。
3. 点击 Run Pip Install（requirements-cpanel.txt），然后 Restart。
4. 在 MySQL® Databases 创建数据库和用户（All privileges）。
5. 打开网站，输入 ravix-data/install-key.txt 中的安装码，按步骤完成。
6. 添加最后一页显示的 Cron 行（每 5 分钟）。

出现 503 时请查看 ravix-data/logs/startup-error.txt；确认 Python 3.11+ 且依赖已安装。

Full guide: [English](../cpanel.md) · [فارسی](../cpanel.fa.md)
