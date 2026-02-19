#!/usr/bin/env python3
"""Railway startup script - replaces bash to avoid CRLF issues."""
import os
import subprocess
import sys

print("🚀 Iniciando NFS-e Automation System...")

# Get PORT from environment (Railway sets PORT in production).
# If PORT is not set (local dev), allow STREAMLIT_SERVER_PORT as fallback.
port = os.environ.get("PORT") or os.environ.get("STREAMLIT_SERVER_PORT") or "8501"
print(f"PORT={port} (from PORT or STREAMLIT_SERVER_PORT)")

# Run certificate initialization
print("📜 Inicializando certificados...")
print("="*60)
result = subprocess.run([sys.executable, "railway_init.py"])
print("="*60)
print(f"✅ Inicialização de certificados concluída (exit code: {result.returncode})")
print()

# Start Streamlit
print(f"🌐 Iniciando Streamlit na porta {port}...")
os.execvp(
    sys.executable,
    [
        sys.executable, "-m", "streamlit", "run",
        "app_nfse_enhanced.py",
        "--server.port", port,
        "--server.address", "0.0.0.0",
        "--server.headless", "true",
        "--server.enableCORS", "false",
        "--server.enableXsrfProtection", "false"
    ]
)
