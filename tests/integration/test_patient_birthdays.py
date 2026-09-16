"""
tests/integration/test_patient_birthdays.py

Achado do Dossiê Insighta RCM — aniversariantes do mês, ponta a ponta
via GET /patients/birthdays. Prova que PatientService.list_birthdays_in_month
usa Patient.birth_date (nunca inventa data para quem não tem), ordena
por dia do mês, e isola por tenant (RLS).
"""


async def _create_patient(client, auth_headers, *, full_name: str, birth_date: str | None = None, communication_consent: bool | None = None) -> str:
    payload = {"full_name": full_name}
    if birth_date is not None:
        payload["birth_date"] = birth_date
    if communication_consent is not None:
        payload["communication_consent"] = communication_consent
    resp = await client.post("/api/v1/patients", json=payload, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def test_no_patients_with_birthday_in_month_returns_empty_list(client, auth_headers_a):
    response = await client.get("/api/v1/patients/birthdays?month=1", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["month"] == 1
    assert body["items"] == []


async def test_lists_patients_with_birthday_in_the_given_month_ordered_by_day(client, auth_headers_a):
    await _create_patient(client, auth_headers_a, full_name="Nasce dia 20", birth_date="1990-03-20")
    await _create_patient(client, auth_headers_a, full_name="Nasce dia 5", birth_date="1985-03-05")
    await _create_patient(client, auth_headers_a, full_name="Nasce em outro mês", birth_date="1990-04-01")

    response = await client.get("/api/v1/patients/birthdays?month=3", headers=auth_headers_a)
    assert response.status_code == 200
    names = [item["full_name"] for item in response.json()["items"]]
    assert names == ["Nasce dia 5", "Nasce dia 20"]


async def test_patients_without_birth_date_never_appear(client, auth_headers_a):
    await _create_patient(client, auth_headers_a, full_name="Sem data de nascimento")

    response = await client.get("/api/v1/patients/birthdays?month=1", headers=auth_headers_a)
    assert response.status_code == 200
    assert response.json()["items"] == []


async def test_exposes_communication_consent_without_filtering_by_it(client, auth_headers_a):
    await _create_patient(
        client, auth_headers_a, full_name="Consentiu", birth_date="1990-06-10", communication_consent=True
    )
    await _create_patient(
        client, auth_headers_a, full_name="Não consentiu", birth_date="1990-06-15", communication_consent=False
    )
    await _create_patient(client, auth_headers_a, full_name="Nunca perguntado", birth_date="1990-06-20")

    response = await client.get("/api/v1/patients/birthdays?month=6", headers=auth_headers_a)
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 3
    consent_by_name = {item["full_name"]: item["communication_consent"] for item in items}
    assert consent_by_name == {"Consentiu": True, "Não consentiu": False, "Nunca perguntado": None}


async def test_defaults_to_current_month_when_month_is_omitted(client, auth_headers_a):
    response = await client.get("/api/v1/patients/birthdays", headers=auth_headers_a)
    assert response.status_code == 200
    from datetime import date

    assert response.json()["month"] == date.today().month


async def test_birthdays_are_isolated_between_tenants(client, auth_headers_a, auth_headers_b):
    await _create_patient(client, auth_headers_a, full_name="Paciente do tenant A", birth_date="1990-07-15")

    response_b = await client.get("/api/v1/patients/birthdays?month=7", headers=auth_headers_b)
    assert response_b.status_code == 200
    assert response_b.json()["items"] == []
