@echo off
rem Run the bot on a Windows PC that stays on. First time only, in this folder:
rem   py -m venv .venv
rem   .venv\Scripts\pip install -r requirements.txt
rem   setx FP_API_KEY "fp_test_..."      (then open a new terminal)
rem The window must stay open; closing it stops the bot. It restarts itself after a crash
rem and pulls the latest monthly selection from GitHub once per start.
:loop
git pull --ff-only
.venv\Scripts\python -m friendsbot run
echo Bot gestopt, herstart over 10 seconden...
timeout /t 10 /nobreak >nul
goto loop
