@echo off
cd /d "%~dp0"
echo Verificando e instalando dependencias...
pip install -r requirements.txt -q
echo Iniciando Ruteo GLP con Google Maps (Tiempo Real)...
start http://127.0.0.1:5001
python app.py
pause
