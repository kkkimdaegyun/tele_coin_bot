@echo off
cd /d %~dp0
where py >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python launcher(py)가 없습니다. Python 3.11+ 설치 후 다시 실행하세요.
  pause
  exit /b 1
)

py -3 -m venv .venv
call .venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt

if not exist .env (
  copy .env.example .env >nul
  echo.
  echo .env 파일을 만들었습니다.
  echo TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, WEBHOOK_SECRET 값을 넣어주세요.
)

echo.
echo 설치 완료.
pause
