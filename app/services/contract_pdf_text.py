"""
app/services/contract_pdf_text.py

Extrai texto bruto de um PDF de contrato para alimentar a IA
(contract_extraction_service.py). Separado num módulo próprio porque é
puramente mecânico (não tem regra de negócio) e assim
ContractIntakeService não precisa saber qual biblioteca faz a extração.

LIMITAÇÃO CONHECIDA — PDF escaneado (imagem) não tem texto para extrair
-------------------------------------------------------------------------
`pypdf` só lê texto que já é texto no PDF (a maioria dos contratos
gerados digitalmente pela operadora). Um contrato ESCANEADO (foto/scan
de papel) devolve string vazia ou lixo — a abordagem híbrida de OCR
(citada como opção no briefing do produto) fica para quando isso se
provar um caso comum o suficiente para justificar a dependência extra
(tesseract/textract). Por ora, texto vazio vira erro explícito, não uma
extração silenciosamente vazia.
"""
import io

from pypdf import PdfReader


class ContractPdfTextError(Exception):
    pass


def extract_text(pdf_bytes: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        pages_text = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:  # pypdf lança tipos variados para PDF corrompido/criptografado
        raise ContractPdfTextError(f"Não foi possível ler o PDF: {exc}") from exc

    full_text = "\n".join(pages_text).strip()
    if not full_text:
        raise ContractPdfTextError(
            "Nenhum texto extraído do PDF — provavelmente um documento escaneado (imagem). "
            "Cadastre a tabela de preços manualmente para este contrato."
        )
    return full_text
