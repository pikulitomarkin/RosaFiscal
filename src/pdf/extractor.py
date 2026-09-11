"""
Extrator de dados de PDFs para emissão de NFS-e.
Suporta o formato de tabela: Hash | Nome | CPF | Telefone | Email | Endereço | Data Consulta | Valor | Criação
"""
import re
from typing import List, Dict, Optional
from pathlib import Path
import pdfplumber
from io import BytesIO

from src.utils.logger import app_logger
from src.utils.validators import validator


class PDFDataExtractor:
    """Extrai dados estruturados de PDFs para emissão de NFS-e."""

    # Prefixo e formato do hash do paciente: PACIENTEBLIS + UUID (8-4-4-4-12) + "_"
    HASH_PREFIXO = 'PACIENTEBLIS'
    HASH_HEX_LEN = 32  # dígitos hexadecimais de um UUID

    # Patterns Regex para extração
    PATTERNS = {
        'cpf': r'\b\d{11}\b',
        'cnpj': r'\b\d{14}\b',
        'telefone': r'\b\d{10,11}\b',
        # O hash contém hífens (UUID) e termina em "_" — \w+ cortava no primeiro hífen
        'hash': r'PACIENTEBLIS[0-9a-fA-F]+(?:-[0-9a-fA-F]+)*-?_?',
        # Fragmento de hash no início de uma linha/célula de continuação
        # (o PDF quebra o hash em 2 ou 3 linhas: "PACIENTEBLISced9b9db-44f4-" + "4d48-b5f2-3eadcd8f0489_")
        'hash_fragmento': r'^-?[0-9a-fA-F]+(?:-[0-9a-fA-F]+)*-?_?',
        'email': r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}',
        'valor': r'R\$\s*[\d.,]+',
        'data': r'\d{2}/\d{2}/\d{4}',
    }

    # Índices esperados das colunas na tabela
    COL_HASH = 0
    COL_NOME = 1
    COL_CPF = 2
    COL_TELEFONE = 3
    COL_EMAIL = 4
    COL_ENDERECO = 5
    COL_DATA_CONSULTA = 6
    COL_VALOR = 7

    def __init__(self):
        self.errors: List[str] = []

    # ------------------------------------------------------------------
    # Helpers de hash
    # ------------------------------------------------------------------
    @classmethod
    def _hex_do_hash(cls, hash_bruto: str) -> str:
        """Retorna apenas os dígitos hexadecimais do hash (sem prefixo, hífens ou '_')."""
        corpo = hash_bruto
        if corpo.startswith(cls.HASH_PREFIXO):
            corpo = corpo[len(cls.HASH_PREFIXO):]
        return re.sub(r'[^0-9a-fA-F]', '', corpo)

    @classmethod
    def _hash_completo(cls, hash_bruto: str) -> bool:
        """Indica se o hash já tem o UUID inteiro (32 dígitos hex)."""
        return len(cls._hex_do_hash(hash_bruto)) >= cls.HASH_HEX_LEN

    @classmethod
    def _normalizar_hash(cls, hash_bruto: str) -> str:
        """
        Reconstrói o hash no formato canônico PACIENTEBLIS<uuid>_.

        O layout do relatório quebra o hash em 2 ou 3 linhas e a quebra às vezes
        engole o hífen, então o UUID é remontado (8-4-4-4-12) a partir dos dígitos
        hexadecimais lidos.
        """
        hexs = cls._hex_do_hash(hash_bruto)
        if len(hexs) < cls.HASH_HEX_LEN:
            # Hash incompleto no PDF: devolve o que foi lido, sem hífen solto no fim
            return hash_bruto.rstrip('-')
        h = hexs[:cls.HASH_HEX_LEN]
        uuid = f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"
        return f"{cls.HASH_PREFIXO}{uuid}_"

    @classmethod
    def _extrair_fragmento_hash(cls, texto: str) -> Optional[str]:
        """
        Extrai do início de uma linha/célula a continuação do hash da linha anterior.
        Exige dígito ou hífen no fragmento para não confundir com nome de paciente
        formado só por letras hexadecimais (ex.: "Abade").
        """
        m = re.match(cls.PATTERNS['hash_fragmento'], texto)
        if not m:
            return None
        frag = m.group()
        if not re.search(r'[0-9-]', frag):
            return None
        return frag

    # Sobras da coluna de e-mail que caem na linha de continuação (ex.: "...@gmail.co" + "m")
    SUFIXOS_EMAIL = ('m', 'om', 'com', 'br', 'ail', 'mail', 'net', 'me', 'edu', 'gov', 'org')

    @classmethod
    def _extrair_continuacao_nome(cls, texto: str) -> str:
        """
        Extrai a continuação do nome de uma linha de continuação.

        A linha pode trazer, depois do nome, sobras da coluna de e-mail
        ("Moraes ail.com", "Souza Santos m"); os tokens são lidos só enquanto
        forem alfabéticos e as sobras finais são descartadas.
        """
        tokens = []
        for token in texto.split():
            if not re.fullmatch(r'[A-Za-zÀ-ÿ]+', token):
                break
            tokens.append(token)

        # Descarta sobras de e-mail no fim — sempre minúsculas ("...@gmail.co" + "m"),
        # o que preserva iniciais do nome, que vêm em maiúscula ("Ana Carolina B")
        while tokens and tokens[-1].islower() and (
            len(tokens[-1]) == 1 or tokens[-1] in cls.SUFIXOS_EMAIL
        ):
            tokens.pop()

        return ' '.join(tokens)

    # ------------------------------------------------------------------
    # Entrada
    # ------------------------------------------------------------------
    def extract_from_file(self, file_path: Path) -> List[Dict[str, str]]:
        try:
            with pdfplumber.open(file_path) as pdf:
                return self._process_pdf(pdf)
        except Exception as e:
            error_msg = f"Erro ao processar arquivo {file_path}: {e}"
            app_logger.error(error_msg)
            self.errors.append(error_msg)
            return []

    def extract_from_bytes(self, file_bytes: bytes) -> List[Dict[str, str]]:
        try:
            with pdfplumber.open(BytesIO(file_bytes)) as pdf:
                return self._process_pdf(pdf)
        except Exception as e:
            error_msg = f"Erro ao processar PDF: {e}"
            app_logger.error(error_msg)
            self.errors.append(error_msg)
            return []

    def _process_pdf(self, pdf) -> List[Dict[str, str]]:
        all_records = []
        for page_num, page in enumerate(pdf.pages, start=1):
            # Tenta extração por tabela primeiro (mais confiável para PDFs com colunas)
            records = self._extract_via_table(page, page_num)
            if not records:
                # Fallback: extração por texto
                text = page.extract_text()
                if text:
                    records = self._extract_via_text(text, page_num)
            all_records.extend(records)

        app_logger.info(f"Total de {len(all_records)} registros extraídos do PDF")
        return all_records

    # ------------------------------------------------------------------
    # Extração por tabela
    # ------------------------------------------------------------------
    def _extract_via_table(self, page, page_num: int) -> List[Dict[str, str]]:
        """Extrai dados usando detecção de tabela do pdfplumber."""
        records = []
        try:
            tables = page.extract_tables()
            for table in tables:
                if not table:
                    continue
                # Mescla linhas de continuação (hash/nome quebrados em múltiplas linhas)
                merged = self._merge_continuation_rows(table)
                for row in merged:
                    record = self._parse_table_row(row, page_num)
                    if record:
                        records.append(record)
        except Exception as e:
            app_logger.debug(f"Extração por tabela falhou na página {page_num}: {e}")
        return records

    def _merge_continuation_rows(self, table: list) -> list:
        """
        Mescla linhas de continuação onde o hash e/ou o nome do paciente quebram em
        mais de uma linha. Uma linha de continuação é aquela que NÃO contém o prefixo
        do hash. O fragmento de hash é colado SEM espaço (senão o hash sai cortado).
        """
        merged = []
        for row in table:
            if not row:
                continue
            cells = [str(c or '').strip() for c in row]
            row_text = ' '.join(cells)

            if self.HASH_PREFIXO in row_text:
                # Nova linha principal com hash
                merged.append(list(cells))
                continue

            if not merged:
                continue

            # Linha de continuação: mescla célula a célula com a linha principal anterior
            prev = merged[-1]
            for i, cell in enumerate(cells):
                if not cell or i >= len(prev):
                    continue

                # Continuação do hash: cola sem espaço enquanto o UUID estiver incompleto
                if self.HASH_PREFIXO in prev[i] and not self._hash_completo(prev[i]):
                    frag = self._extrair_fragmento_hash(cell)
                    if frag:
                        prev[i] = prev[i] + frag
                        resto = cell[len(frag):].strip()
                        if resto and not re.match(r'^[\d/\s.,R$]*$', resto):
                            # Sobra da célula (ex.: nome) vai para a coluna do nome
                            alvo = self.COL_NOME if self.COL_NOME < len(prev) else i
                            prev[alvo] = (prev[alvo] + ' ' + resto).strip()
                        continue

                # Só mescla células de texto (não números/datas)
                if not re.match(r'^[\d/\s.,R$]*$', cell):
                    prev[i] = (prev[i] + ' ' + cell).strip()
        return merged

    def _parse_table_row(self, row: list, page_num: int) -> Optional[Dict]:
        """Tenta extrair um registro de uma linha de tabela."""
        if not row or len(row) < 3:
            return None

        # Junta células None como string vazia e remove quebras de linha internas.
        # Na célula do hash a quebra é removida SEM espaço para não cortar o UUID.
        cells = []
        for c in row:
            texto = str(c or '')
            if self.HASH_PREFIXO in texto:
                cells.append(re.sub(r'\s+', '', texto))
            else:
                cells.append(texto.replace('\n', ' ').strip())

        # Verifica se há hash na linha inteira ou na primeira célula
        row_text = ' '.join(cells)
        hash_match = re.search(self.PATTERNS['hash'], row_text)
        if not hash_match:
            return None

        hash_bruto = hash_match.group()
        hash_id = self._normalizar_hash(hash_bruto)

        # CPF: 11 dígitos
        cpf = None
        for cell in cells:
            m = re.search(self.PATTERNS['cpf'], cell.replace('.', '').replace('-', ''))
            if m:
                cpf = m.group()
                break
        if not cpf:
            return None

        # Nome: célula após o hash (ou a que contiver texto sem dígitos)
        nome = self._extrair_nome_das_celulas(cells, hash_bruto)

        # Telefone
        telefone = None
        for cell in cells:
            digits = re.sub(r'\D', '', cell)
            if len(digits) in (10, 11) and digits != cpf:
                telefone = digits
                break

        # Email
        email = None
        email_m = re.search(self.PATTERNS['email'], row_text)
        if email_m:
            email = email_m.group()

        # Datas
        datas = re.findall(self.PATTERNS['data'], row_text)
        data_consulta = datas[0] if datas else None

        # Valor
        valor = self._parse_valor(row_text)

        return self._build_record(hash_id, nome, cpf, telefone, email, data_consulta, valor, page_num)

    def _extrair_nome_das_celulas(self, cells: list, hash_bruto: str) -> str:
        """Extrai o nome do paciente das células da linha."""
        # Tenta célula COL_NOME primeiro
        if len(cells) > self.COL_NOME:
            nome_cell = cells[self.COL_NOME]
            # Célula do nome não deve ter só dígitos nem o hash
            if nome_cell and self.HASH_PREFIXO not in nome_cell and not nome_cell.isdigit():
                nome_limpo = re.sub(r'\d', '', nome_cell).strip()
                if len(nome_limpo) > 3:
                    return nome_limpo

        # Fallback: busca célula que parece nome (só letras e espaços, > 5 chars)
        for cell in cells:
            if self.HASH_PREFIXO in cell or hash_bruto in cell:
                continue
            if re.match(r'^[A-Za-zÀ-ÿ\s]{5,}$', cell.strip()):
                return cell.strip()

        return "Nome não encontrado"

    # ------------------------------------------------------------------
    # Extração por texto (fallback)
    # ------------------------------------------------------------------
    def _extract_via_text(self, text: str, page_num: int) -> List[Dict[str, str]]:
        """Fallback: extração linha a linha do texto bruto."""
        records = []
        lines = text.split('\n')

        # Agrupa linhas: linha principal (com hash) + continuações.
        # No layout do relatório uma continuação pode trazer o resto do hash,
        # o resto do nome, ou os dois na mesma linha:
        #   "PACIENTEBLISced9b9db-44f4- Roberto Takeo Osato 31382901836 ..."
        #   "4d48-b5f2-3eadcd8f0489_ Kondo"
        grupos = []   # lista de dicts: principal, fragmentos de hash e continuações de nome
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            if self.HASH_PREFIXO in line:
                grupos.append({'principal': line, 'hash_frags': [], 'nome_conts': []})
                continue

            if not grupos:
                continue

            grupo = grupos[-1]
            resto = stripped

            # 1) Continuação do hash, enquanto o UUID não estiver completo
            hash_atual = self._hash_parcial_do_grupo(grupo)
            if hash_atual and not self._hash_completo(hash_atual):
                frag = self._extrair_fragmento_hash(resto)
                if frag:
                    grupo['hash_frags'].append(frag)
                    resto = resto[len(frag):].strip()

            # 2) Continuação do nome: tokens alfabéticos no início da sobra
            if resto:
                nome_cont = self._extrair_continuacao_nome(resto)
                if nome_cont:
                    grupo['nome_conts'].append(nome_cont)

        for grupo in grupos:
            record = self._parse_text_group(
                grupo['principal'], grupo['hash_frags'], grupo['nome_conts'], page_num
            )
            if record:
                records.append(record)

        return records

    def _hash_parcial_do_grupo(self, grupo: dict) -> Optional[str]:
        """Hash lido até agora no grupo (linha principal + fragmentos já mesclados)."""
        m = re.search(self.PATTERNS['hash'], grupo['principal'])
        if not m:
            return None
        return m.group() + ''.join(grupo['hash_frags'])

    def _parse_text_group(self, text: str, hash_frags: list, continuacoes: list,
                          page_num: int) -> Optional[Dict]:
        """Extrai um registro de uma linha principal + continuações de hash/nome."""
        hash_match = re.search(self.PATTERNS['hash'], text)
        if not hash_match:
            return None

        # Trecho do hash presente NA LINHA (usado para localizar o início do nome)
        hash_na_linha = hash_match.group()
        # Hash completo = linha principal + fragmentos das linhas seguintes
        hash_id = self._normalizar_hash(hash_na_linha + ''.join(hash_frags))

        cpf_matches = re.findall(self.PATTERNS['cpf'], text)
        if not cpf_matches:
            return None
        cpf = cpf_matches[0]

        # Nome: texto entre o hash e o CPF
        hash_pos = text.find(hash_na_linha)
        cpf_pos = text.find(cpf)
        nome = "Nome não encontrado"
        if hash_pos >= 0 and cpf_pos > hash_pos:
            trecho = text[hash_pos + len(hash_na_linha):cpf_pos].strip()
            # Mantém iniciais de 1 letra ("Ana Carolina B Felizatti", "GERSON A LIMA"):
            # entre o hash e o CPF só existe a coluna Nome, então só sobra numérica é descartada
            nome_palavras = [p for p in trecho.split() if not p.isdigit()]
            if nome_palavras:
                nome = ' '.join(nome_palavras)

        # Adiciona continuações de nome (ex: "Kondo", "Souza Santos", "Pinheiro")
        if continuacoes:
            if nome == "Nome não encontrado":
                nome = ' '.join(continuacoes).strip()
            else:
                nome = (nome + ' ' + ' '.join(continuacoes)).strip()

        # Telefone
        telefone = None
        tel_matches = re.findall(self.PATTERNS['telefone'], text)
        for tel in tel_matches:
            if tel != cpf and len(tel) >= 10:
                telefone = tel
                break

        # Email
        email_match = re.search(self.PATTERNS['email'], text)
        email = email_match.group() if email_match else None

        # Data
        datas = re.findall(self.PATTERNS['data'], text)
        data_consulta = datas[0] if datas else None

        # Valor
        valor = self._parse_valor(text)

        return self._build_record(hash_id, nome, cpf, telefone, email, data_consulta, valor, page_num)

    # ------------------------------------------------------------------
    # Comuns
    # ------------------------------------------------------------------
    def _parse_valor(self, text: str) -> Optional[float]:
        """Extrai valor monetário do texto."""
        m = re.search(self.PATTERNS['valor'], text)
        if not m:
            return None
        valor_str = m.group().replace('R$', '').strip()
        if ',' in valor_str:
            valor_str = valor_str.replace('.', '').replace(',', '.')
        try:
            return float(valor_str)
        except ValueError:
            return None

    def _build_record(self, hash_id, nome, cpf, telefone, email, data_consulta, valor, page_num) -> Optional[Dict]:
        """Constrói e valida o dicionário de registro."""
        cpf_formatado = cpf
        try:
            cpf_formatado = validator.format_cpf(cpf)
        except Exception:
            pass

        hash_completo = self._hash_completo(hash_id)
        if not hash_completo:
            app_logger.warning(
                f"Hash incompleto no PDF (página {page_num}) para {nome}: {hash_id}"
            )

        record = {
            'hash': hash_id,
            'hash_completo': hash_completo,
            'nome': nome,
            'cpf': cpf,
            'cpf_formatado': cpf_formatado,
            'email': email,
            'telefone': telefone,
            'data_consulta': data_consulta,
            'valor': valor,
            'page': page_num,
            'valido': True,
        }
        app_logger.debug(f"Registro extraído: {nome} | CPF: {cpf} | Hash: {hash_id} | Valor: {valor}")
        return record

    def validate_extracted_data(self, records: List[Dict[str, str]]) -> Dict[str, any]:
        total = len(records)
        validos = sum(1 for r in records if r['valido'])
        invalidos = total - validos
        sem_hash = sum(1 for r in records if not r.get('hash'))
        hash_incompleto = sum(1 for r in records if not r.get('hash_completo', True))
        sem_nome = sum(1 for r in records if r.get('nome') == 'Nome não encontrado')

        stats = {
            'total_registros': total,
            'registros_validos': validos,
            'registros_invalidos': invalidos,
            'sem_hash': sem_hash,
            'hash_incompleto': hash_incompleto,
            'sem_nome': sem_nome,
            'taxa_sucesso': (validos / total * 100) if total > 0 else 0
        }
        app_logger.info(f"Validação: {validos}/{total} registros válidos ({stats['taxa_sucesso']:.1f}%)")
        if hash_incompleto:
            app_logger.warning(f"{hash_incompleto} registro(s) com hash incompleto")
        return stats

    def filter_valid_records(self, records: List[Dict[str, str]]) -> List[Dict[str, str]]:
        valid = [
            r for r in records
            if r['valido']
            and r.get('hash')
            and r.get('nome') != 'Nome não encontrado'
        ]
        app_logger.info(f"{len(valid)}/{len(records)} registros passaram no filtro de validação")
        return valid


# Instância global
pdf_extractor = PDFDataExtractor()
