"""Coleta dos dados abertos da ANTT (concessões rodoviárias federais).

Fonte: https://dados.antt.gov.br (CKAN). Todos os conjuntos são descobertos pela API
package_show, então novos arquivos anuais/mensais entram sem mudar o código.
"""
from __future__ import annotations

import json
import re

import pandas as pd

from .comum import SAIDA, baixar, ler_csv_br, mes_ano, numero_br

API = "https://dados.antt.gov.br/api/3/action/package_show?id="

TRAFEGO = {
    "equivalente": "volume-trafego-equivalente-praca-pedagio",
    "fisico": "volume-trafego-praca-pedagio",
}
# conjuntos pequenos, baixados inteiros a cada execução
ANUAIS = {
    "receita_pedagio": "receita-pedagio",
    "custos_operacionais": "custos-operacionais",
    "investimentos": "investimentos",
}
SIGICOR = [
    "acompanhamento-anual",
    "inexecucao",
    "cadastros-investimentos",
    "autorizacao-investimento",
    "encerramento-investimentos",
    "planejamento-anual",
]


def recursos(pacote: str) -> list[dict]:
    bruto = baixar(API + pacote, timeout=120)
    try:
        dados = json.loads(bruto)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"ANTT devolveu resposta não-JSON para {pacote}: {bruto[:400]!r}") from e
    return [r for r in dados["result"]["resources"] if (r.get("format") or "").upper() == "CSV"]


def escolher_arquivos_trafego(res: list[dict]) -> dict[int, dict]:
    """Um arquivo por ano: 'mensal consolidado' quando existir; senão o anual (pequeno).

    Arquivos diários e os anuais gigantes (>150 MB) são ignorados.
    """
    por_ano: dict[int, dict] = {}
    for r in res:
        nome = r["url"].rsplit("/", 1)[-1].lower()
        m = re.search(r"(20\d\d)", nome)
        if not m or "diario" in nome:
            continue
        ano = int(m.group(1))
        mensal = "mensal_consolidado" in nome
        tamanho = r.get("size") or 0
        if not mensal and tamanho > 150_000_000:
            continue
        atual = por_ano.get(ano)
        if atual is None or (mensal and not atual["_mensal"]):
            por_ano[ano] = {**r, "_mensal": mensal}
    return dict(sorted(por_ano.items()))


def _col(df: pd.DataFrame, *opcoes: str) -> str:
    for o in opcoes:
        if o in df.columns:
            return o
    raise KeyError(f"Nenhuma das colunas {opcoes} em {list(df.columns)}")


def agregar_trafego(df: pd.DataFrame, tipo: str) -> pd.DataFrame:
    """Linha por categoria -> praça x sentido x mês x tipo de veículo."""
    df.columns = [c.strip().lower() for c in df.columns]
    out = pd.DataFrame({
        "concessionaria_fonte": df[_col(df, "concessionaria")].str.strip().str.replace('"', "", regex=False),
        "praca": df[_col(df, "praca")].str.strip().str.replace('"', "", regex=False),
        "sentido": df[_col(df, "sentido")].str.strip().str.replace('"', "", regex=False),
        "mes": mes_ano(df[_col(df, "mes_ano")]),
        "tipo_veiculo": df[_col(df, "tipo_de_veiculo")].str.strip().str.replace('"', "", regex=False),
        "volume": numero_br(df[_col(df, "volume_total")]),
    })
    if tipo == "equivalente":
        out["veic_eq"] = numero_br(df[_col(df, "volume_veiculo_equivalente")])
    chaves = ["concessionaria_fonte", "praca", "sentido", "mes", "tipo_veiculo"]
    agg = out.groupby(chaves, dropna=False, as_index=False).sum(numeric_only=True)
    return agg


def coletar_trafego(manifesto: dict) -> dict[str, pd.DataFrame]:
    """Reprocessa só os anos cujo arquivo mudou (manifesto guarda last_modified)."""
    cache = SAIDA / "cache_antt"
    cache.mkdir(parents=True, exist_ok=True)
    resultado = {}
    for tipo, pacote in TRAFEGO.items():
        anos = escolher_arquivos_trafego(recursos(pacote))
        partes = []
        for ano, r in anos.items():
            chave = f"{tipo}_{ano}"
            arq = cache / f"{chave}.parquet"
            versao = r.get("last_modified") or r.get("created") or ""
            if arq.exists() and manifesto.get(chave) == versao:
                partes.append(pd.read_parquet(arq))
                continue
            print(f"[ANTT] {tipo} {ano}: baixando {r['url']}", flush=True)
            df = agregar_trafego(ler_csv_br(baixar(r["url"])), tipo)
            df["ano_arquivo"] = ano
            df["arquivo"] = r["url"].rsplit("/", 1)[-1]
            df.to_parquet(arq, index=False)
            manifesto[chave] = versao
            partes.append(df)
        resultado[tipo] = pd.concat(partes, ignore_index=True) if partes else pd.DataFrame()
    return resultado


def coletar_anuais() -> dict[str, pd.DataFrame]:
    out = {}
    for nome, pacote in ANUAIS.items():
        r = recursos(pacote)[0]
        df = ler_csv_br(baixar(r["url"]))
        df.columns = [c.strip().lower() for c in df.columns]
        df = df.rename(columns={"concessionaria": "concessionaria_fonte"})
        df["ano"] = pd.to_numeric(df["ano"], errors="coerce").astype("Int64")
        df["valor"] = numero_br(df["valor"])
        out[nome] = df[["concessionaria_fonte", "ano", "valor"]]
    return out


def coletar_pracas_trechos() -> dict[str, pd.DataFrame]:
    pr = recursos("praca-de-pedagio")
    pr = sorted(pr, key=lambda r: r.get("last_modified") or "")[-1]
    pracas = ler_csv_br(baixar(pr["url"]))
    pracas.columns = [c.strip().lower() for c in pracas.columns]
    pracas = pracas.rename(columns={"concessionaria": "concessionaria_fonte"})
    for c in ("latitude", "longitude", "km_m"):
        pracas[c] = numero_br(pracas[c])

    tr = recursos("trecho-concedido")
    tr = sorted(tr, key=lambda r: r.get("last_modified") or "")[-1]
    trechos = ler_csv_br(baixar(tr["url"]))
    trechos.columns = [c.strip().lower() for c in trechos.columns]
    trechos = trechos.rename(columns={"concessionaria": "concessionaria_fonte"})
    for c in ("km_m_inicial", "km_m_final"):
        trechos[c] = numero_br(trechos[c])
    return {"pracas_antt": pracas, "trechos_antt": trechos}


def coletar_receita_praca() -> pd.DataFrame:
    partes = []
    for r in recursos("receita-por-praca"):
        df = ler_csv_br(baixar(r["url"]))
        df.columns = [c.strip().lower() for c in df.columns]
        partes.append(pd.DataFrame({
            "concessionaria_fonte": df["concessionaria"].str.strip(),
            "praca": df["praca_de_pedagio"].str.strip(),
            "mes": mes_ano(df["mes_ano"]),
            "receita": numero_br(df["receita_praca_de_pedagio"]),
        }))
    df = pd.concat(partes, ignore_index=True)
    return df.groupby(["concessionaria_fonte", "praca", "mes"], as_index=False)["receita"].sum()


def coletar_sigicor() -> pd.DataFrame:
    """Conjuntos do SIGICOR (investimentos do PER). Mantidos como texto bruto + origem."""
    partes = []
    for pacote in SIGICOR:
        for r in recursos(pacote):
            try:
                df = ler_csv_br(baixar(r["url"]))
            except Exception as e:  # noqa: BLE001
                print(f"[ANTT] SIGICOR falhou {r['url']}: {e}", flush=True)
                continue
            df.columns = [c.strip().lower() for c in df.columns]
            df = df.loc[:, ~pd.Index(df.columns).duplicated()]
            df["conjunto"] = pacote
            df["arquivo"] = r["url"].rsplit("/", 1)[-1]
            partes.append(df)
    return pd.concat(partes, ignore_index=True) if partes else pd.DataFrame()
