"""
scripts/load_test.py

Item 9 do "Caminho para produção" (Fase 3): "teste automatizado de
interface" já estava concluído (77 testes de frontend) — o que faltava
era exercitar a aplicação sob TRÁFEGO CONCORRENTE de verdade, não só
requisição isolada por vez como os testes de integração fazem.

O QUE ESTE SCRIPT MEDE
-------------------------------------------------------------------------
Simula N "usuários virtuais" batendo de verdade, via HTTP, nos endpoints
mais usados no dia a dia de uma clínica (dashboard executivo, agenda,
lista de pacientes) ao mesmo tempo — contra uma instância REAL da
aplicação (não um mock), com dado real semeado por
`scripts/seed_demo_data.py`. Não substitui os testes de integração
(que provam CORREÇÃO); este mede DESEMPENHO sob concorrência: latência
por endpoint (p50/p90/p99), taxa de erro, e o comportamento em duas
questões específicas que o README já documentava como risco conhecido,
nunca medido:

  1. Connection pool dimensionado para desenvolvimento (`pool_size=10`,
     `DB_MAX_OVERFLOW=5`) — o que acontece quando mais de 15 requisições
     concorrentes precisam do banco ao mesmo tempo?
  2. Rate limiting por IP (`RATE_LIMIT_DEFAULT`, `slowapi`,
     `key_func=get_remote_address`) — como TODA a equipe de uma clínica
     tipicamente sai para a internet pelo MESMO IP público (NAT do
     roteador), elas compartilham o mesmo balde de limite. Este script
     mede se um uso concorrente realista de uma clínica (alguns
     funcionários, ritmo humano) esbarra nisso.

Uso — rode DEPOIS de `python -m scripts.seed_demo_data` (ver README):
    python -m scripts.load_test --base-url http://127.0.0.1:8010 \
        --email marina.demo161111@vitalis-demo.com.br --password 'SenhaDemo123!' \
        --concurrency 10 --duration 60 --pace human

    # cenário de estresse (ignora o ritmo humano, martela sem pausa —
    # serve pra achar o teto de banco/pool, não o de rate limit; combine
    # com RATE_LIMIT_DEFAULT alto na instância sob teste):
    python -m scripts.load_test --base-url http://127.0.0.1:8010 \
        --email ... --password ... --concurrency 30 --duration 30 --pace stress
"""
import argparse
import asyncio
import json
import random
import statistics
import time
from dataclasses import dataclass, field

import httpx


@dataclass
class RequestSample:
    endpoint: str
    status_code: int
    duration_ms: float
    error: str | None = None


@dataclass
class Results:
    samples: list[RequestSample] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def record(self, sample: RequestSample) -> None:
        async with self.lock:
            self.samples.append(sample)


# Mistura ponderada dos endpoints GET mais chamados numa sessão real de
# uso do painel (dashboard executivo é o mais pesado — várias agregações
# em uma query só, ver app/repositories/analytics_repository.py) — pesos
# aproximam o padrão real: dashboard e agenda abertos com frequência,
# telas de detalhe menos.
_ENDPOINT_WEIGHTS: list[tuple[str, int]] = [
    ("/api/v1/analytics/executive-summary?date_from={df}&date_to={dt}", 3),
    ("/api/v1/analytics/agenda-metrics", 3),
    ("/api/v1/analytics/smart-insights?date_from={df}&date_to={dt}", 2),
    ("/api/v1/patients?limit=25&offset=0", 2),
    ("/api/v1/appointments/by-patient/{patient_id}", 1),
    ("/api/v1/professionals", 1),
]

# Pausa entre uma ação e outra do MESMO usuário virtual — "human" imita
# alguém lendo a tela antes de clicar de novo (o padrão real de uso);
# "stress" não pausa, testa o teto de infraestrutura, não o de uso real.
_PACE_DELAY_RANGE_S = {
    "human": (1.5, 4.0),
    "stress": (0.0, 0.05),
}


class _LoginError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


async def _login(client: httpx.AsyncClient, base_url: str, email: str, password: str) -> str:
    resp = await client.post(f"{base_url}/api/v1/auth/login", json={"email": email, "password": password})
    if resp.status_code >= 400:
        raise _LoginError(resp.status_code, resp.text[:200])
    data = resp.json()
    if not data.get("access_token"):
        raise _LoginError(resp.status_code, f"Login não devolveu access_token: {data}")
    return data["access_token"]


async def _fetch_a_patient_id(client: httpx.AsyncClient, base_url: str, headers: dict) -> str:
    resp = await client.get(f"{base_url}/api/v1/patients?limit=1&offset=0", headers=headers)
    resp.raise_for_status()
    items = resp.json().get("items", [])
    if not items:
        raise RuntimeError("Nenhum paciente encontrado — rode scripts/seed_demo_data.py primeiro.")
    return items[0]["id"]


async def _virtual_user(
    user_id: int,
    base_url: str,
    email: str,
    password: str,
    stop_at: float,
    pace: str,
    results: Results,
) -> None:
    delay_lo, delay_hi = _PACE_DELAY_RANGE_S[pace]
    today = time.strftime("%Y-%m-%d")
    date_from = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 30 * 86400))

    # Jitter de chegada — no ritmo "human", ninguém clica em "Entrar" no
    # mesmíssimo milissegundo; imita a equipe chegando pro turno em
    # alguns segundos de diferença (ainda dentro da mesma janela de
    # 1 minuto do rate limit de login — é exatamente esse o cenário real
    # que interessa medir). No ritmo "stress" não tem jitter: o objetivo
    # ali é achar o teto de infraestrutura, não imitar chegada humana.
    if pace == "human":
        await asyncio.sleep(random.uniform(0, 3.0))

    async with httpx.AsyncClient(timeout=30.0) as client:
        t0 = time.perf_counter()
        try:
            token = await _login(client, base_url, email, password)
            await results.record(
                RequestSample("/api/v1/auth/login", 200, (time.perf_counter() - t0) * 1000)
            )
        except _LoginError as exc:
            await results.record(
                RequestSample("/api/v1/auth/login", exc.status_code, (time.perf_counter() - t0) * 1000, error=str(exc))
            )
            return
        except Exception as exc:
            await results.record(RequestSample("/api/v1/auth/login", 0, (time.perf_counter() - t0) * 1000, error=str(exc)))
            return

        headers = {"Authorization": f"Bearer {token}"}

        try:
            patient_id = await _fetch_a_patient_id(client, base_url, headers)
        except Exception as exc:
            await results.record(RequestSample("/api/v1/patients?page=1&page_size=1", 0, 0.0, error=str(exc)))
            return

        endpoints = [e for e, _ in _ENDPOINT_WEIGHTS]
        weights = [w for _, w in _ENDPOINT_WEIGHTS]

        while time.time() < stop_at:
            path = random.choices(endpoints, weights=weights, k=1)[0].format(
                df=date_from, dt=today, patient_id=patient_id
            )
            start = time.perf_counter()
            try:
                resp = await client.get(f"{base_url}{path}", headers=headers)
                elapsed_ms = (time.perf_counter() - start) * 1000
                await results.record(RequestSample(path.split("?")[0], resp.status_code, elapsed_ms))
            except Exception as exc:
                elapsed_ms = (time.perf_counter() - start) * 1000
                await results.record(RequestSample(path.split("?")[0], 0, elapsed_ms, error=str(exc)))

            await asyncio.sleep(random.uniform(delay_lo, delay_hi))


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(int(len(ordered) * pct), len(ordered) - 1)
    return ordered[idx]


def _print_report(results: Results, wall_seconds: float) -> dict:
    by_endpoint: dict[str, list[RequestSample]] = {}
    for s in results.samples:
        by_endpoint.setdefault(s.endpoint, []).append(s)

    report: dict = {"wall_seconds": round(wall_seconds, 1), "endpoints": {}}

    print("\n" + "=" * 78)
    print(f"RELATÓRIO DE TESTE DE CARGA — {wall_seconds:.1f}s de janela, {len(results.samples)} requisições")
    print("=" * 78)
    print(f"{'endpoint':<55} {'n':>5} {'erro%':>7} {'p50ms':>8} {'p90ms':>8} {'p99ms':>8}")
    for endpoint, samples in sorted(by_endpoint.items()):
        ok = [s for s in samples if 200 <= s.status_code < 300]
        durations = [s.duration_ms for s in ok]
        errors = [s for s in samples if not (200 <= s.status_code < 300)]
        rate_limited = [s for s in samples if s.status_code == 429]
        error_pct = 100 * len(errors) / len(samples) if samples else 0
        print(
            f"{endpoint:<55} {len(samples):>5} {error_pct:>6.1f}% "
            f"{_percentile(durations, 0.5):>8.0f} {_percentile(durations, 0.9):>8.0f} {_percentile(durations, 0.99):>8.0f}"
        )
        report["endpoints"][endpoint] = {
            "requests": len(samples),
            "error_pct": round(error_pct, 1),
            "rate_limited_429": len(rate_limited),
            "p50_ms": round(_percentile(durations, 0.5), 1),
            "p90_ms": round(_percentile(durations, 0.9), 1),
            "p99_ms": round(_percentile(durations, 0.99), 1),
            "max_ms": round(max(durations), 1) if durations else 0,
        }

    all_errors = [s for s in results.samples if not (200 <= s.status_code < 300)]
    all_429 = [s for s in results.samples if s.status_code == 429]
    all_5xx = [s for s in results.samples if s.status_code >= 500]
    all_conn_errors = [s for s in results.samples if s.status_code == 0]
    print("-" * 78)
    print(f"Total: {len(results.samples)} req | {len(all_errors)} erro(s) | {len(all_429)} rate-limited (429) | {len(all_5xx)} erro de servidor (5xx) | {len(all_conn_errors)} falha de conexão")
    print("=" * 78)

    report["summary"] = {
        "total_requests": len(results.samples),
        "throughput_rps": round(len(results.samples) / wall_seconds, 2) if wall_seconds else 0,
        "total_429": len(all_429),
        "total_5xx": len(all_5xx),
        "total_connection_errors": len(all_conn_errors),
    }
    return report


async def main_async(args: argparse.Namespace) -> dict:
    stop_at = time.time() + args.duration
    results = Results()

    print(f"Iniciando {args.concurrency} usuário(s) virtual(is) · ritmo '{args.pace}' · {args.duration}s contra {args.base_url}")
    start = time.time()
    await asyncio.gather(
        *[
            _virtual_user(i, args.base_url, args.email, args.password, stop_at, args.pace, results)
            for i in range(args.concurrency)
        ]
    )
    wall_seconds = time.time() - start
    return _print_report(results, wall_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8010")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--concurrency", type=int, default=10, help="usuários virtuais simultâneos")
    parser.add_argument("--duration", type=int, default=60, help="duração da janela de teste, em segundos")
    parser.add_argument("--pace", choices=["human", "stress"], default="human")
    parser.add_argument("--json-out", default=None, help="caminho opcional para salvar o relatório em JSON")
    args = parser.parse_args()

    report = asyncio.run(main_async(args))
    report["config"] = {"concurrency": args.concurrency, "duration": args.duration, "pace": args.pace}

    if args.json_out:
        with open(args.json_out, "w") as fh:
            json.dump(report, fh, indent=2, ensure_ascii=False)
        print(f"Relatório salvo em {args.json_out}")


if __name__ == "__main__":
    main()
