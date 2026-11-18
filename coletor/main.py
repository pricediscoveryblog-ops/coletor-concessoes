"""Orquestra a coleta. Cada bloco roda isolado: a falha de uma fonte não apaga as demais.

Saídas (Parquet) em saidas/, publicadas na branch `dados`:
  cadastro.parquet               cadastro mestre + região
  trafego_equivalente.parquet    ANTT, praça x sentido x mês x tipo de veículo (veículos equivalentes)
  trafego_fisico.parquet         ANTT, idem em veículos físicos
  trafego_artesp.parquet         ARTESP, praça x sentido x mês x medida (formato longo)
  receita_pedagio.parquet / custos_operacionais.parquet / investimentos.parquet  (ANTT, anual)
  receita_praca.parquet          ANTT, receita mensal por praça
  pracas_antt.parquet / trechos_antt.parquet
  sigicor.parquet                ANTT, investimentos do PER (bruto)
  diagnostico.json               nomes sem casamento no cadastro, status de cada bloco
"""
from __future__ import annotations

import json
import re
import traceback
from datetime import datetime, timezone

import pandas as pd

from . import antt, artesp
from .comum import SAIDA, carregar_cadastro, mapa_aliases, normalizar_nome


def mapear(df: pd.DataFrame, aliases: dict[str, str], col: str = "concessionaria_fonte") -> pd.DataFrame:
    def achar(nome: str) -> str:
        n = normalizar_nome(nome)
        if n in aliases:
            return aliases[n]
        m = re.match(r"^L ?(\d{2})\b", n)  # ARTESP: 'L01-AUTOBAN'
        if m and f"L{m.group(1)}" in aliases:
            return aliases[f"L{m.group(1)}"]
        return ""
    unicos = {n: achar(n) for n in df[col].dropna().unique()}
    df = df.copy()
    df.insert(0, "id_concessao", df[col].map(unicos).fillna(""))
    return df


def salvar(nome: str, df: pd.DataFrame) -> None:
    df.to_parquet(SAIDA / f"{nome}.parquet", index=False)
    print(f"[OK] {nome}: {len(df):,} linhas", flush=True)


def main() -> None:
    SAIDA.mkdir(parents=True, exist_ok=True)
    arq_manif = SAIDA / "manifesto.json"
    manifesto = json.loads(arq_manif.read_text()) if arq_manif.exists() else {}
    diag: dict = {"executado_em": datetime.now(timezone.utc).isoformat(), "blocos": {}, "sem_cadastro": {}}

    cad = carregar_cadastro()
    aliases = mapa_aliases(cad)
    salvar("cadastro", cad)

    def bloco(nome, func):
        try:
            for k, df in func().items():
                if "concessionaria_fonte" in df.columns:
                    df = mapear(df, aliases)
                    faltam = sorted(df.loc[df["id_concessao"] == "", "concessionaria_fonte"].unique())
                    if faltam:
                        diag["sem_cadastro"][k] = faltam
                salvar(k, df)
            diag["blocos"][nome] = "ok"
        except Exception:  # noqa: BLE001
            diag["blocos"][nome] = "falhou: " + traceback.format_exc()[-1500:]
            print(f"[ERRO] {nome}\n{traceback.format_exc()}", flush=True)

    bloco("antt_trafego", lambda: {f"trafego_{k}": v for k, v in antt.coletar_trafego(manifesto).items()})
    bloco("antt_anuais", antt.coletar_anuais)
    bloco("antt_pracas_trechos", antt.coletar_pracas_trechos)
    bloco("antt_receita_praca", lambda: {"receita_praca": antt.coletar_receita_praca()})
    bloco("antt_sigicor", lambda: {"sigicor": antt.coletar_sigicor()})

    def _artesp():
        df, d = artesp.coletar(manifesto)
        diag["artesp_arquivos"] = d
        return {"trafego_artesp": df} if not df.empty else {}
    bloco("artesp_trafego", _artesp)

    arq_manif.write_text(json.dumps(manifesto, indent=1))
    (SAIDA / "diagnostico.json").write_text(json.dumps(diag, indent=1, ensure_ascii=False, default=str))
    falhas = [k for k, v in diag["blocos"].items() if v != "ok"]
    print("Blocos com falha:", falhas or "nenhum")


if __name__ == "__main__":
    main()
