#!/bin/bash
echo "🚀 Iniciando NFS-e Automation System..."
echo "PORT=$PORT"

# Executar inicialização (certificados)
python railway_init.py

# Prefer STREAMLIT_SERVER_PORT quando PORT não estiver definida (útil para dev local). Railway continuará definindo $PORT em produção.
PORT="${PORT:-${STREAMLIT_SERVER_PORT:-8501}}"
echo "Iniciando Streamlit na porta $PORT"

# Iniciar Streamlit
exec streamlit run app_nfse_enhanced.py \
    --server.port "$PORT" \
    --server.address 0.0.0.0 \
    --server.headless true \
    --server.enableCORS false \
    --server.enableXsrfProtection false
