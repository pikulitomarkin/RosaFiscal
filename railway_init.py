"""
Script de inicialização para Railway.
Carrega certificados de variáveis de ambiente Base64.
"""
import os
import base64
from pathlib import Path


def setup_certificates():
    """
    Configura certificados a partir de variáveis de ambiente Base64.
    Usado no Railway onde não podemos subir arquivos .pem diretamente.
    """
    cert_dir = Path("certificados")
    cert_dir.mkdir(exist_ok=True)
    print(f"📁 Pasta certificados criada/verificada: {cert_dir.absolute()}")

    # Deriva os caminhos PEM do mesmo diretório de CERTIFICATE_PATH
    # CertificateManager._try_load_pem_files() busca cert.pem/key.pem no parent do CERTIFICATE_PATH
    # Ex: "Neuroclinsenha123456.pfx" → parent="./" → "./cert.pem" e "./key.pem"
    certificate_path = os.getenv("CERTIFICATE_PATH", "certificados/cert.pem")
    pfx_parent = Path(certificate_path).parent
    pfx_parent.mkdir(parents=True, exist_ok=True)
    cert_path = pfx_parent / "cert.pem"
    key_path  = pfx_parent / "key.pem"
    
    if cert_path.exists() and key_path.exists():
        print("✅ Certificados já existem localmente")
        print(f"   - {cert_path} ({cert_path.stat().st_size} bytes)")
        print(f"   - {key_path} ({key_path.stat().st_size} bytes)")
        return True
    
    # Tentar carregar de variáveis de ambiente
    cert_b64 = os.getenv("CERTIFICATE_CERT_PEM")
    key_b64 = os.getenv("CERTIFICATE_KEY_PEM")
    
    print(f"\n🔍 Verificando variáveis de ambiente:")
    print(f"   CERTIFICATE_CERT_PEM: {'✅ Definida' if cert_b64 else '❌ NÃO DEFINIDA'} ({len(cert_b64) if cert_b64 else 0} chars)")
    print(f"   CERTIFICATE_KEY_PEM: {'✅ Definida' if key_b64 else '❌ NÃO DEFINIDA'} ({len(key_b64) if key_b64 else 0} chars)")
    
    if cert_b64 and key_b64:
        try:
            print("\n🔓 Decodificando certificados Base64...")
            
            # Decodificar cert.pem
            try:
                cert_content = base64.b64decode(cert_b64)
                print(f"   Cert decodificado: {len(cert_content)} bytes")
                
                # Validar que é um certificado PEM válido
                if not cert_content.startswith(b'-----BEGIN CERTIFICATE-----'):
                    raise ValueError("Conteúdo decodificado não é um certificado PEM válido")
                
                cert_path.write_bytes(cert_content)
                print(f"✅ Certificado salvo: {cert_path} ({len(cert_content)} bytes)")
            except Exception as e:
                print(f"❌ Erro ao processar CERTIFICATE_CERT_PEM: {e}")
                raise
            
            # Decodificar key.pem  
            try:
                key_content = base64.b64decode(key_b64)
                print(f"   Key decodificada: {len(key_content)} bytes")
                
                # Validar que é uma chave privada PEM válida
                if not key_content.startswith(b'-----BEGIN'):
                    raise ValueError("Conteúdo decodificado não é uma chave PEM válida")
                
                key_path.write_bytes(key_content)
                os.chmod(key_path, 0o600)  # Permissões restritas
                print(f"✅ Chave privada salva: {key_path} ({len(key_content)} bytes)")
            except Exception as e:
                print(f"❌ Erro ao processar CERTIFICATE_KEY_PEM: {e}")
                raise
            
            # Testar carregamento
            print("\n🔍 Testando carregamento dos certificados...")
            try:
                from cryptography import x509
                from cryptography.hazmat.backends import default_backend
                from cryptography.hazmat.primitives import serialization
                
                # Tentar carregar certificado
                with open(cert_path, 'rb') as f:
                    cert_data = f.read()
                    cert = x509.load_pem_x509_certificate(cert_data, default_backend())
                    print(f"✅ Certificado válido: {cert.subject}")
                
                # Tentar carregar chave
                with open(key_path, 'rb') as f:
                    key_data = f.read()
                    key = serialization.load_pem_private_key(key_data, None, default_backend())
                    print(f"✅ Chave privada válida")
                    
            except Exception as e:
                print(f"⚠️ Aviso ao validar certificados: {e}")
                # Não falha aqui, deixa o certificate_manager tratar
            
            return True
        except Exception as e:
            print(f"❌ Erro ao decodificar certificados: {e}")
            import traceback
            traceback.print_exc()
            return False
    else:
        print("\n❌ CERTIFICADOS NÃO CONFIGURADOS!")
        print("   Você precisa configurar no Railway:")
        print("   1. CERTIFICATE_CERT_PEM (Base64 do cert.pem)")
        print("   2. CERTIFICATE_KEY_PEM (Base64 do key.pem)")
        print("\n   Verifique o arquivo RAILWAY_VARIAVEIS.txt no repositório")
        return False


def main():
    """Ponto de entrada principal."""
    print("\n" + "="*60)
    print("🚀 NFS-e Automation System - Inicialização Railway")
    print("="*60 + "\n")
    
    # Configurar certificados
    cert_ok = setup_certificates()
    
    if cert_ok:
        # Recarrega o certificate_manager após criar os arquivos
        try:
            from src.utils.certificate import get_certificate_manager
            cert_mgr = get_certificate_manager()
            if cert_mgr._certificate is not None:
                print("✅ Certificate Manager carregado com sucesso")
                print(f"   Titular: {cert_mgr.get_subject_name()}")
            else:
                print("⚠️ Certificate Manager inicializado mas certificado não carregado")
                print("   Tentando reload...")
                if cert_mgr.reload():
                    print("✅ Certificado recarregado após retry")
                else:
                    print("❌ Falha ao recarregar certificado")
        except Exception as e:
            print(f"⚠️ Erro ao carregar Certificate Manager: {e}")
            import traceback
            traceback.print_exc()
    else:
        print("\n❌ Falha na configuração de certificados")
        print("   O sistema pode não funcionar corretamente para emissão de NFS-e")
    
    print("\n✅ Inicialização concluída!")
    print("   Iniciando Streamlit...\n")


if __name__ == "__main__":
    main()
