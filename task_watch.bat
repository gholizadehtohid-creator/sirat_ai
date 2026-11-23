@echo off
rem Codal feed watch: new financial statements (ن-10) and monthly activity
rem reports (ن-30) from the whole market, fetched within two hours of
rem publication instead of whenever a per-company sweep reached the company
rem (up to a month, found 2026-11-05). One small request when nothing is new.
rem
rem Scheduled every 2 hours by: schtasks /TN SIRAT-codal-watch
rem (every 15 minutes until 2026-11-06, when the user asked for less often)
rem Battery settings live in Task Scheduler, not here (see the evening task).
cd /d C:\SIRAT-AI
set PY=C:\Users\gholizadeh-t\AppData\Local\Programs\Python\Python314\python.exe
rem Persian in the output: without this a print of a symbol crashes the run.
set PYTHONIOENCODING=utf-8
rem Bounded: past 2 MB the log becomes .log.1 (log_rotate.py).
"%PY%" log_rotate.py logs\task_watch.log
echo ===== %date% %time% ===== >> logs\task_watch.log
"%PY%" codal_watch.py >> logs\task_watch.log 2>&1
set RC=%ERRORLEVEL%
rem A run that finished ends with a "done" line and its exit code; one
rem that stops without it was killed (added 2026-11-23).
echo ===== done %date% %time%: codal-watch exit %RC% ===== >> logs\task_watch.log
exit /b %RC%
