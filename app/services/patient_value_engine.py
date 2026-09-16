"""
app/services/patient_value_engine.py

Score de paciente de alto valor ("VIP") — "Equilíbrio Insighta" (plano de
Balanced Scorecard, perna Cliente, mecanismo 1). Combina frequência de
visitas (mesmo sinal — visit_count — já usado pelo motor de churn
antecipado, ver _EARLY_CHURN_CTE em analytics_repository.py) com
indicação de outros pacientes (Patient.referred_by_patient_id, "Mapa de
Dados Insighta" Onda 1) — regra determinística e explicável, mesmo
espírito de no_show_risk_engine.py/denial_risk_engine.py: nunca um
modelo de caixa-preta, o gestor/recepção sempre consegue ver O PORQUÊ.

DECISÃO — "ou", não "e": qualquer um dos dois sinais já basta
-------------------------------------------------------------------------
Um paciente que vem toda semana mas nunca indicou ninguém já é valioso
pela frequência sozinha; um paciente novo que já trouxe 2 outros já é
valioso pela indicação sozinha, mesmo com pouco histórico próprio ainda.
Exigir os dois ao mesmo tempo deixaria de fora casos óbvios de alto
valor — a régua aqui é "qualquer um dos dois sinais reais", não uma
média ponderada inventada.

Limiares abaixo são um "chute" razoável de v1 (mesmo padrão documentado
em no_show_risk_engine.py/health_score_engine.py) — nunca uma calibração
validada contra dado real de produção ainda.
"""
from dataclasses import dataclass, field

VIP_MIN_VISITS = 5
VIP_MIN_REFERRALS = 1


@dataclass
class VipStatus:
    is_vip: bool
    reasons: list[str] = field(default_factory=list)


def compute_vip_status(*, visit_count: int, referral_count: int) -> VipStatus:
    reasons: list[str] = []
    if visit_count >= VIP_MIN_VISITS:
        reasons.append("frequente")
    if referral_count >= VIP_MIN_REFERRALS:
        reasons.append("indicou outros pacientes")
    return VipStatus(is_vip=bool(reasons), reasons=reasons)
