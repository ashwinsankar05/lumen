@echo off
rem LUMEN public link: a Cloudflare quick tunnel to the verification link running on this laptop.
rem Start lumen-web.cmd first and keep it open. This prints a https://....trycloudflare.com address
rem with a valid certificate that works from any device with internet, for as long as this window
rem stays open. The address changes every time this is started. No Cloudflare account needed.
rem Needs cloudflared.exe (https://github.com/cloudflare/cloudflared/releases) in %USERPROFILE%\cloudflared\.
setlocal
set CF=%USERPROFILE%\cloudflared\cloudflared.exe
if not exist "%CF%" (
  echo cloudflared.exe not found at %CF%
  echo Download cloudflared-windows-amd64.exe from https://github.com/cloudflare/cloudflared/releases
  echo and save it there as cloudflared.exe.
  pause
  exit /b 1
)
echo Starting the public tunnel to https://localhost:8443 ...
echo Look for the line with  https://....trycloudflare.com  below: that is the public address.
echo Keep this window AND the lumen-web window open. Ctrl+C stops the public link.
"%CF%" tunnel --no-autoupdate --url https://localhost:8443 --no-tls-verify
pause
