@echo off
echo ================================================
echo   HEAD OFFICE ZL - Git Init v0
echo   Repo: https://github.com/GALUDAKOKO/App_HR
echo ================================================
echo.

cd /d "%~dp0"
echo Working in: %CD%
echo.

echo [1/5] Remove old .git if exists...
if exist ".git" rmdir /s /q ".git"
echo.

echo [2/5] git init...
git init
git branch -M main
echo.

echo [3/5] git config user...
git config user.email "galuda25923@gmail.com"
git config user.name "GALUDAKOKO"
echo.

echo [4/5] git add and commit v0...
git add .
git commit -m "v0: HEAD OFFICE ZL initial deploy"
git tag v0
echo.

echo [5/5] Push to GitHub...
git remote add origin https://github.com/GALUDAKOKO/App_HR.git
git push -u origin main --tags
echo.

echo ================================================
echo   Done! Check: https://github.com/GALUDAKOKO/App_HR
echo ================================================
pause
