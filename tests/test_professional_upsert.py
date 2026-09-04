"""
tests/test_professional_upsert.py

CORREÇÃO (Auditoria Go-Live, achado F-02): antes desta mudança, o único
jeito de um Profissional existir no sistema era uma tela de CRUD manual
— incompatível com a decisão de que o SaaS opera exclusivamente sobre
dados consolidados do ERP/enviados pelo cliente (mesmo motivo pelo qual
/patients e /professionals foram removidas do frontend). Agora
NormalizationService._get_or_create_professional resolve o profissional
a partir da própria linha de faturamento, no mesmo espírito de
_get_or_create_patient.

Estes testes exercitam a função pura de resolução de identidade
(casar/criar) com um repositório fake em memória, sem precisar de
Postgres — não requerem rede/pip, então continuam executáveis mesmo
neste sandbox (ver limitação de rede documentada no README de auditoria).
Complementam, não substituem, um teste de integração real contra Postgres
quando o CI do time rodar a suíte completa.
"""
import uuid
from dataclasses import dataclass

import pytest

from app.models.professional import Professional
from app.services.normalization_service import NormalizationService
from app.worker.schemas import RawBillingRow


class _FakeProfessionalRepo:
    def __init__(self):
        self.store: list[Professional] = []

    async def get_by_registry(self, professional_registry: str) -> Professional | None:
        return next((p for p in self.store if p.professional_registry == professional_registry), None)

    async def get_by_name(self, full_name: str) -> Professional | None:
        return next((p for p in self.store if p.full_name == full_name), None)

    async def add(self, professional: Professional) -> Professional:
        self.store.append(professional)
        return professional


def _row(*, professional_name: str | None, professional_registry: str | None = None) -> RawBillingRow:
    return RawBillingRow(
        patient_cpf=None,
        patient_name="Paciente Teste",
        professional_name=professional_name,
        professional_registry=professional_registry,
        insurance_plan_raw_name="Unimed",
        procedure_code="10101012",
        cid_code=None,
        charged_value=150.0,
        service_date="2026-01-15",
    )


def _service(professional_repo: _FakeProfessionalRepo) -> NormalizationService:
    # Só professional_repo importa para estes testes — os outros
    # colaboradores nunca são chamados por _get_or_create_professional.
    return NormalizationService(
        patient_repo=None,  # type: ignore[arg-type]
        professional_repo=professional_repo,  # type: ignore[arg-type]
        appointment_repo=None,  # type: ignore[arg-type]
        contract_item_repo=None,  # type: ignore[arg-type]
        insurance_plan_repo=None,  # type: ignore[arg-type]
        billing_repo=None,  # type: ignore[arg-type]
        local_repo=None,  # type: ignore[arg-type]
        guia_repo=None,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_row_without_professional_name_returns_none_and_creates_nothing():
    repo = _FakeProfessionalRepo()
    service = _service(repo)
    tenant_id = uuid.uuid4()

    result = await service._get_or_create_professional(tenant_id, _row(professional_name=None))

    assert result is None
    assert repo.store == []


@pytest.mark.asyncio
async def test_new_professional_with_registry_is_created():
    repo = _FakeProfessionalRepo()
    service = _service(repo)
    tenant_id = uuid.uuid4()

    result = await service._get_or_create_professional(
        tenant_id, _row(professional_name="Dra. Ana Souza", professional_registry="CRM12345")
    )

    assert result is not None
    assert result.full_name == "Dra. Ana Souza"
    assert result.professional_registry == "CRM12345"
    assert result.tenant_id == tenant_id
    assert len(repo.store) == 1


@pytest.mark.asyncio
async def test_same_registry_matches_existing_even_with_different_name_spelling():
    """O registro profissional (CRM/CRO) é a chave de identidade, não o
    nome — protege contra "Dra. Ana Souza" vs "Ana Souza" vs "ANA SOUZA"
    virarem 3 profissionais diferentes em 3 importações."""
    repo = _FakeProfessionalRepo()
    service = _service(repo)
    tenant_id = uuid.uuid4()

    first = await service._get_or_create_professional(
        tenant_id, _row(professional_name="Dra. Ana Souza", professional_registry="CRM12345")
    )
    second = await service._get_or_create_professional(
        tenant_id, _row(professional_name="ANA SOUZA", professional_registry="CRM12345")
    )

    assert first.id == second.id
    assert len(repo.store) == 1


@pytest.mark.asyncio
async def test_different_registry_creates_a_second_professional():
    repo = _FakeProfessionalRepo()
    service = _service(repo)
    tenant_id = uuid.uuid4()

    first = await service._get_or_create_professional(
        tenant_id, _row(professional_name="Dra. Ana Souza", professional_registry="CRM12345")
    )
    second = await service._get_or_create_professional(
        tenant_id, _row(professional_name="Dr. Bruno Lima", professional_registry="CRM99999")
    )

    assert first.id != second.id
    assert len(repo.store) == 2


@pytest.mark.asyncio
async def test_no_registry_falls_back_to_exact_name_match():
    """Limitação conhecida e documentada (mesma classe de limitação que
    PatientRepository.get_by_cpf ausente já tem hoje): sem registro
    profissional na linha, o match só funciona por nome EXATO — grafias
    diferentes do mesmo profissional podem gerar duplicatas. Aceitável
    por ora porque a maioria dos exports de ERP relevantes traz o
    registro profissional (é dado obrigatório em faturamento TISS/ANS)."""
    repo = _FakeProfessionalRepo()
    service = _service(repo)
    tenant_id = uuid.uuid4()

    first = await service._get_or_create_professional(tenant_id, _row(professional_name="Dra. Carla Melo"))
    second = await service._get_or_create_professional(tenant_id, _row(professional_name="Dra. Carla Melo"))
    third = await service._get_or_create_professional(tenant_id, _row(professional_name="Carla Melo"))

    assert first.id == second.id  # nome idêntico -> mesmo profissional
    assert third.id != first.id  # grafia diferente -> duplicata (limitação documentada)
    assert len(repo.store) == 2
