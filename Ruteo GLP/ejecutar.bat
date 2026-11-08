@echo off
cd /d "%~dp0"
echo Verificando e instalando dependencias...
pip install -r requirements.txt -q
echo Iniciando Ruteo GLP...
start http://127.0.0.1:5000
python app.py
pause
