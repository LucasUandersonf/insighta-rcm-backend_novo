"""
app/core/text_utils.py

Extraído de normalization_service.py (Etapa 2) porque agora tem um
segundo consumidor: InsurancePlanService, ao criar um convênio novo pela
tela de Convênios, precisa gerar o mesmo normalized_key que a Etapa 2
usaria para casar um plano importado de arquivo — os dois caminhos
(cadastro manual e importação automática) têm que convergir para a
MESMA forma canônica, senão um plano cadastrado manualmente como "Amil
S450" nunca casaria com "AMIL S-450" vindo de um arquivo importado depois.
"""
import re
import unicodedata


def slugify(value: str) -> str:
    """"UNIMED NAC." -> "unimed_nac". Remove acentos, baixa a caixa, troca
    tudo que não é [a-z0-9] por underscore."""
    ascii_only = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    ascii_only = ascii_only.lower().strip()
    return re.sub(r"[^a-z0-9]+", "_", ascii_only).strip("_")
