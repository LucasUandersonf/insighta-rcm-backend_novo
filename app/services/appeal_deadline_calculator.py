"""
app/services/appeal_deadline_calculator.py

Função PURA (mesmo princípio de denial_risk_engine.py e
contract_extraction_service.py: a decisão de negócio isolada da
I/O) — calcula o prazo de contestação a partir da data da negativa.

Ordem de prioridade do número de dias:
  1) `company_deadline_days` — configurado pelo tenant na operadora
     específica (o número real do contrato, quando cadastrado).
  2) `settings.DEFAULT_APPEAL_DEADLINE_DAYS` — fallback genérico (ver
     DECISÃO em app/core/config.py: NÃO é uma lei federal, é só para o
     sistema não deixar o campo em branco enquanto o tenant não
     configurou o prazo real).
"""
from datetime import date, timedelta


def compute_deadline(denied_at: date, *, company_deadline_days: int | None, default_deadline_days: int) -> date:
    days = company_deadline_days if company_deadline_days is not None else default_deadline_days
    if days <= 0:
        raise ValueError("Prazo de contestação deve ser um número positivo de dias.")
    return denied_at + timedelta(days=days)
