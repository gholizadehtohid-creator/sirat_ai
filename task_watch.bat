@echo off
rem Codal feed watch: new financial statements (ن-10) and monthly activity
rem reports (ن-30) from the whole market, fetched within a quarter-hour of
rem publication instead of whenever a per-company sweep reached the company
rem (up to a month, found 2026-11-05). Two small requests when nothing is new.
rem
rem Scheduled every 15 minutes by: schtasks /TN SIRAT-codal-watch
rem Battery settings live in Task Scheduler, not here (see the evening task).
cd /d C:\SIRAT-AI
set PY=C:\Users\gholizadeh-t\AppData\Local\Programs\Python\Python314\python.exe
rem Persian in the output: without this a print of a symbol crashes the run.
set PYTHONIOENCODING=utf-8
echo ===== %date% %time% ===== >> logs\task_watch.log
"%PY%" codal_watch.py >> logs\task_watch.log 2>&1
