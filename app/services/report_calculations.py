"""
app/services/report_calculations.py

Cálculos puros do relatório semanal — sem banco, sem I/O — para ficarem
testáveis isoladamente, mesmo princípio de denial_risk_engine.py e
no_show_risk_engine.py.
"""


def compute_roi_pct(spend: float, revenue: float) -> float | None:
    """
    ROI = (receita - gasto) / gasto. Retorna None quando spend <= 0 —
    "ROI de uma campanha sem gasto" não é zero, é INDEFINIDO (divisão por
    zero disfarçada); reportar 0% ali seria uma afirmação numérica falsa
    ("essa campanha não teve retorno") quando na verdade não houve gasto
    nenhum para avaliar.
    """
    if spend <= 0:
        return None
    return (revenue - spend) / spend


def average_utilization(rates: list[float]) -> float | None:
    """
    Média simples das taxas de utilização por profissional. Retorna None
    quando a lista está vazia (nenhum profissional com grade configurada
    no período) — mesmo motivo de sempre: ausência de dado não é 0%.
    """
    if not rates:
        return None
    return sum(rates) / len(rates)
