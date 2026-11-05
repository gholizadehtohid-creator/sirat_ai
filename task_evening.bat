@echo off
rem The evening machine. Fetches the day's data headlessly, then appends the
rem forward prediction -- the entry that makes R-FORWARD-2027 a PROSPECTIVE
rem test. An evening this does not run is an admissible forward prediction
rem lost permanently, which is why it is a scheduled task and not a habit.
rem
rem The second line is deliberate belt-and-braces: bootstrap's own ensure()
rem already appends the log after a fetch, but when everything is fresh
rem bootstrap exits before fetching and only the explicit call runs. The log
rem is append-once per session and OS-locked, so running it twice is free.
rem
rem Scheduled daily 19:00 by: schtasks /TN SIRAT-evening
cd /d C:\SIRAT-AI
set PY=C:\Users\gholizadeh-t\AppData\Local\Programs\Python\Python314\python.exe
echo ===== %date% %time% ===== >> logs\task_evening.log
"%PY%" bootstrap.py >> logs\task_evening.log 2>&1
"%PY%" examples\forward_prediction_log.py >> logs\task_evening.log 2>&1
rem Gold funds are not equities, so the history step never fetches them;
rem until 2026-11-05 only a panel button did, and they went stale from 08-29
rem while the allocation now leans on them (E28). 60 bars covers any gap the
rem evening schedule can leave, at 14 requests.
"%PY%" -c "import gold; gold.fetch_history(count=60, verbose=False)" >> logs\task_evening.log 2>&1
