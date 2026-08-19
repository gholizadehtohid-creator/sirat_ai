@echo off
REM ===================================================
REM  SIRAT AI - launcher
REM    run.bat             start the admin panel (fetches missing data itself)
REM    run.bat fetch       download whatever data is missing or stale
REM    run.bat setup       import the full instrument universe
REM    run.bat indices     import شاخص کل and شاخص هم‌وزن
REM    run.bat sectors     attach a sector to every share (slow: 1 call each)
REM    run.bat market      CLI index technical analysis
REM    run.bat industry    CLI sector fundamental + technical analysis
REM    run.bat screen      CLI two-stage market screen
REM    run.bat scan        CLI scan of the 5 demo symbols
REM    run.bat advisor     CLI full book from the DEFAULT decision engine
REM    run.bat swing       CLI full book from the v2 swing engine
REM    run.bat classic     CLI full book from the v1 combined engine
REM    run.bat abtest      V1 vs V2 vs index, one engine, one set of costs
REM    run.bat backtest    full backtest + validation suite (writes a report)
REM    run.bat quicktest   backtest only, no validation stages
REM    run.bat v2          BACKTEST suite v2 (models A/B/C, slippage, sizing).
REM                        NOT the v2 decision engine -- that is "swing" above,
REM                        and it is what the panel uses by default.
REM    run.bat backfill    download historical Codal statements (hours, resumable)
REM    run.bat coverage    what the point-in-time filing store now holds
REM    run.bat sweep       the old single-symbol threshold sweep
REM    run.bat paper       advance the paper-trading account (sends nothing)
REM                        add --engine v2 / --engine v1 to pick the engine
REM    run.bat blotter     print the paper account without trading
REM    run.bat status      what data is stored locally
REM    run.bat verify      is the cached price data real or simulated?
REM    run.bat probe       diagnose API connectivity
REM    run.bat install     (re)install dependencies
REM  --- research governance -------------------------------------------
REM    run.bat ci          THE BUILD: unit suite + Alpha Core regression
REM                        fixture. Add --full to rebuild the fixture panel
REM                        from sirat_market.db (minutes, needs the 424 MB db)
REM    run.bat fixture     the Alpha Core fixture on its own
REM    run.bat research    the research record. No argument = the platform KPI
REM                        (at which stage are bad ideas rejected). Also:
REM                        registry / show <id> / register --file / windows
REM    run.bat snapshot    freeze the market universe if one is due
REM    run.bat lifecycle   one experiment end to end, refusals and all
REM    run.bat gitinit     Week 1 item 1: put the repo under version control
REM ===================================================

setlocal
cd /d "%~dp0"

REM --- Reaching TSETMC from behind a VPN --------------------------------
REM A TUN-mode VPN owns the default route, so every request exits abroad and
REM Iranian hosts geo-block it. The app handles this itself: it binds the
REM socket to the physical NIC so tsetmc traffic goes over the local ISP, and
REM re-probes automatically if the route changes while the panel is open.
REM Nothing needs setting here. To pin one address, or to switch it off:
REM set SIRAT_BIND_SOURCE_IP=192.168.0.106
REM set SIRAT_BIND_SOURCE_IP=off
REM
REM Only if binding cannot work at all, route through a proxy instead:
REM set SIRAT_PROXY_URL=http://127.0.0.1:8080

set PY=python
%PY% --version >nul 2>&1
if errorlevel 1 (
    set PY=py
    py --version >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] Python was not found on PATH.
        echo         Install Python 3.9+ from https://www.python.org/downloads/
        echo         and tick "Add python.exe to PATH".
        pause
        exit /b 1
    )
)

if /i "%~1"=="install"  goto install
if /i "%~1"=="setup"    goto setup
if /i "%~1"=="fetch"    goto fetch
if /i "%~1"=="indices"  goto indices
if /i "%~1"=="sectors"  goto sectors
if /i "%~1"=="market"   goto market
if /i "%~1"=="industry" goto industry
if /i "%~1"=="screen"   goto screen
if /i "%~1"=="scan"     goto scan
if /i "%~1"=="advisor"  goto advisor
if /i "%~1"=="swing"    goto swing
if /i "%~1"=="classic"  goto classic
if /i "%~1"=="abtest"    goto abtest
if /i "%~1"=="backtest"  goto backtest
if /i "%~1"=="quicktest" goto quicktest
if /i "%~1"=="v2"        goto v2
if /i "%~1"=="backfill"  goto backfill
if /i "%~1"=="coverage"  goto coverage
if /i "%~1"=="sweep"     goto sweep
if /i "%~1"=="paper"     goto paper
if /i "%~1"=="blotter"   goto blotter
if /i "%~1"=="status"   goto status
if /i "%~1"=="probe"    goto probe
if /i "%~1"=="verify"   goto verify
if /i "%~1"=="ci"        goto ci
if /i "%~1"=="fixture"   goto fixture
if /i "%~1"=="research"  goto research
if /i "%~1"=="snapshot"  goto snapshot
if /i "%~1"=="lifecycle" goto lifecycle
if /i "%~1"=="gitinit"   goto gitinit
goto panel

REM ===================================================
REM  RESEARCH GOVERNANCE
REM ===================================================

:ci
echo The build: unit suite, then the Alpha Core regression fixture.
echo A red fixture means the platform can no longer rediscover its own
echo known failures, and is not entitled to judge a new idea.
%PY% ci.py %2 %3 %4
pause
exit /b 0

:fixture
echo Alpha Core regression fixture. Tolerances are TWO-SIDED: a number that
echo drifts in the favourable direction fails exactly as hard as one that
echo does not. Add --full to rebuild the panel from sirat_market.db.
%PY% -m sirat.fixtures.alpha_core %2 %3
pause
exit /b 0

:research
REM No argument prints the platform KPI: at which stage bad ideas are rejected.
%PY% -m sirat.experiments.cli %2 %3 %4 %5
pause
exit /b 0

:snapshot
echo Freezing the market universe. Survivorship is the one defect that only
echo elapsed time can fix -- a week not captured is a week unrecoverable.
%PY% -m sirat.experiments.universe --if-due
pause
exit /b 0

:lifecycle
echo One experiment end to end. Default is a hypothesis that FAILS, because
echo the point of the platform is that bad ideas die cheaply.
echo   add --passing to see one survive all six stages
%PY% examples\lifecycle_demo.py %2 %3
pause
exit /b 0

:gitinit
echo Week 1, item 1. Puts this repository under version control so results
echo can be re-derived. Refuses to run if git is not installed.
%PY% git_bootstrap.py %2
pause
exit /b 0

:verify
%PY% authenticity.py
pause
exit /b 0

:fetch
echo Downloading whatever data is missing or out of date...
%PY% bootstrap.py
pause
exit /b 0

:indices
%PY% seed_import.py --indices
pause
exit /b 0

:sectors
echo This makes one request per share and takes several minutes.
%PY% seed_import.py --sectors
pause
exit /b 0

:market
%PY% indices.py
pause
exit /b 0

:industry
%PY% sectors.py %2 %3
pause
exit /b 0

:install
echo Installing dependencies...
%PY% -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Dependency install failed.
    pause
    exit /b 1
)
echo Done.
pause
exit /b 0

:setup
echo Importing the full Tehran Stock Exchange instrument universe...
%PY% seed_import.py --universe --status
pause
exit /b 0

:screen
%PY% pipeline.py
pause
exit /b 0

:status
%PY% seed_import.py --status
pause
exit /b 0

:probe
%PY% diagnose.py
pause
exit /b 0

:scan
%PY% main.py
pause
exit /b 0

:advisor
REM Whichever engine config.DECISION_ENGINE names -- the same one the panel uses.
%PY% advisor.py %2 %3 %4 %5
pause
exit /b 0

:swing
REM The v2 swing decision engine, whatever the config default happens to be.
echo Swing decision engine: regime, factors, risk, targets, portfolio.
echo It scores the whole universe, so this takes a couple of minutes.
%PY% advisor.py --engine v2 %2 %3 %4 %5
pause
exit /b 0

:classic
REM The v1 combined engine, whatever the config default happens to be.
%PY% advisor.py --engine v1 %2 %3 %4 %5
pause
exit /b 0

:abtest
echo V1 and V2 through the SAME backtest engine: same costs, same T+1, same
echo queue model, same metrics. Only the strategy differs.
echo V2 scores the whole universe on every rebalance, so this takes a while --
echo add "--limit 120" for a fast smoke test.
%PY% backtest_v2.py %2 %3 %4 %5
pause
exit /b 0

:backtest
echo Backtest plus walk-forward, out-of-sample, Monte Carlo and sensitivity.
echo The first run scores every symbol on every rebalance date and takes
echo several minutes; later runs reuse the cached score panel.
%PY% main.py --backtest %2 %3 %4
pause
exit /b 0

:quicktest
%PY% main.py --backtest --quick %2 %3 %4
pause
exit /b 0

:v2
echo Backtest V2: models A/B/C, slippage scenarios, sizing comparison,
echo point-in-time fundamental coverage, survivorship and a recommendation.
echo Takes considerably longer than "backtest" -- it runs about a dozen
echo full backtests rather than one.
%PY% run_v2.py %2 %3 %4 %5
pause
exit /b 0

:backfill
echo Downloading historical Codal statements so the backtest has fundamentals
echo it could actually have read at the time. codal.ir throttles, so this is
echo paced and is expected to take hours; it resumes where it left off.
echo Ctrl-C is safe -- every filing is committed as it arrives.
%PY% codal_backfill.py --hours 12 %2 %3 %4
pause
exit /b 0

:coverage
%PY% codal_backfill.py --report
pause
exit /b 0

:sweep
%PY% threshold_optimizer.py
pause
exit /b 0

:paper
REM  run.bat paper --engine v2     the swing engine raises the orders
REM  run.bat paper --engine v1     the v1 chain, for comparison
REM  with no --engine, config.DECISION_ENGINE decides -- same as the panel.
%PY% main.py --paper %2 %3 %4 %5
pause
exit /b 0

:blotter
%PY% main.py --paper --report %2 %3
pause
exit /b 0

:panel
REM Verify imports; auto-install once if something is missing.
%PY% -c "import flask, pandas, requests, ta" >nul 2>&1
if errorlevel 1 (
    echo Missing dependencies detected - installing from requirements.txt...
    %PY% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Could not install dependencies. Run: run.bat install
        pause
        exit /b 1
    )
)

echo.
echo ============================================
echo   SIRAT AI ADMIN PANEL
echo   Opening http://127.0.0.1:5000
echo   Press Ctrl+C in this window to stop.
echo ============================================
echo.

REM Give Flask a moment to bind before the browser opens.
start "" /b cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:5000"

%PY% app.py
if errorlevel 1 (
    echo.
    echo [ERROR] The panel exited unexpectedly. See the message above.
    pause
    exit /b 1
)

endlocal
