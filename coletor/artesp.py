"""Coleta do tráfego diário por praça de pedágio da ARTESP (concessões estaduais de SP).

Fonte: https://dadosabertos.artesp.sp.gov.br/dataset/volume-de-trafego
Os arquivos diários vêm compactados em .rar (2020 em diante). O leiaute interno não é
documentado no portal, então o leitor é tolerante: detecta colunas por nome e grava um
diagnóstico com as colunas encontradas para revisão após a primeira execução.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pandas as pd

from .comum import SAIDA, baixar, decodificar, ler_csv_br, mes_ano, normalizar_nome, numero_br

API = "https://dadosabertos.artesp.sp.gov.br/api/3/action/package_show?id=volume-de-trafego"


def recursos_diarios() -> dict[int, dict]:
    dados = json.loads(baixar(API, timeout=120))
    out = {}
    for r in dados["result"]["resources"]:
        m = re.search(r"pra.as de ped.gio por dia em (20\d\d)", r.get("name", ""), re.I)
        if m:
            out[int(m.group(1))] = r
    return dict(sorted(out.items()))


def extrair(arquivo: Path, destino: Path) -> None:
    """Tenta os extratores disponíveis no runner (unrar, 7z, unar, bsdtar)."""
    comandos = [
        ["unrar", "x", "-o+", str(arquivo), str(destino) + "/"],
        ["7z", "x", "-y", f"-o{destino}", str(arquivo)],
        ["unar", "-f", "-o", str(destino), str(arquivo)],
        ["bsdtar", "-xf", str(arquivo), "-C", str(destino)],
    ]
    erros = []
    for cmd in comandos:
        if shutil.which(cmd[0]) is None:
            continue
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode == 0 and any(destino.rglob("*")):
            return
        erros.append(f"{cmd[0]}: {p.stderr[-300:]}")
    raise RuntimeError("Não foi possível extrair " + arquivo.name + " | " + " | ".join(erros))


def _achar(colunas: list[str], *padroes: str) -> str | None:
    for p in padroes:
        for c in colunas:
            if re.search(p, c):
                return c
    return None


def padronizar(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Detecta colunas e devolve formato longo: conc, praça, sentido, mes, medida, valor."""
    df.columns = [normalizar_nome(c).replace(" ", "_") for c in df.columns]
    cols = list(df.columns)
    c_data = _achar(cols, r"^DATA", r"DIA", r"^DT")
    c_conc = _achar(cols, r"CONCESSIONARIA", r"^LOTE")
    c_praca = _achar(cols, r"PRACA", r"^PP$", r"PEDAGIO")
    c_sent = _achar(cols, r"SENTIDO")
    mapa = {"data": c_data, "concessionaria": c_conc, "praca": c_praca, "sentido": c_sent}
    if not (c_data and c_conc and c_praca):
        return pd.DataFrame(), {"colunas": cols, "mapa": mapa, "status": "colunas_nao_identificadas"}
    ident = {c_data, c_conc, c_praca, c_sent}
    medidas = [c for c in cols if c not in ident and numero_br(df[c].head(200)).notna().mean() > 0.9
               and not re.search(r"KM|LAT|LON|ANO|MES|CODIGO|^ID|RA$|^RA_", c)]
    base = pd.DataFrame({
        "concessionaria_fonte": df[c_conc].str.strip(),
        "praca": df[c_praca].str.strip(),
        "sentido": df[c_sent].str.strip() if c_sent else "",
        "mes": mes_ano(df[c_data]),
    })
    longo = []
    for m in medidas:
        t = base.copy()
        t["medida"] = m
        t["valor"] = numero_br(df[m])
        longo.append(t)
    out = pd.concat(longo, ignore_index=True) if longo else pd.DataFrame()
    return out, {"colunas": cols, "mapa": mapa, "medidas": medidas, "status": "ok"}


def ler_pasta(pasta: Path) -> tuple[pd.DataFrame, list[dict]]:
    partes, diag = [], []
    for arq in sorted(pasta.rglob("*")):
        if arq.suffix.lower() not in (".csv", ".txt", ".xlsx"):
            continue
        if arq.suffix.lower() == ".xlsx":
            df = pd.read_excel(arq, dtype=str).fillna("")
        else:
            bruto = arq.read_bytes()
            amostra = decodificar(bruto[:5000])
            sep = ";" if amostra.count(";") > amostra.count(",") else ","
            df = ler_csv_br(bruto, sep=sep)
        longo, d = padronizar(df)
        d["arquivo"] = arq.name
        d["amostra"] = df.head(3).to_dict("records")
        diag.append(d)
        if not longo.empty:
            partes.append(longo)
    if not partes:
        return pd.DataFrame(), diag
    df = pd.concat(partes, ignore_index=True)
    chaves = ["concessionaria_fonte", "praca", "sentido", "mes", "medida"]
    return df.groupby(chaves, as_index=False)["valor"].sum(), diag


def coletar(manifesto: dict) -> tuple[pd.DataFrame, list[dict]]:
    cache = SAIDA / "cache_artesp"
    cache.mkdir(parents=True, exist_ok=True)
    partes, diagnostico = [], []
    for ano, r in recursos_diarios().items():
        chave = f"artesp_{ano}"
        arq_cache = cache / f"{chave}.parquet"
        versao = r.get("last_modified") or r.get("created") or ""
        if arq_cache.exists() and manifesto.get(chave) == versao:
            partes.append(pd.read_parquet(arq_cache))
            continue
        print(f"[ARTESP] {ano}: baixando {r['url']}", flush=True)
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            rar = tmp / "dados.rar"
            rar.write_bytes(baixar(r["url"]))
            destino = tmp / "x"
            destino.mkdir()
            try:
                extrair(rar, destino)
            except RuntimeError as e:
                diagnostico.append({"ano": ano, "status": "falha_extracao", "erro": str(e)})
                continue
            df, diag = ler_pasta(destino)
        for d in diag:
            d["ano"] = ano
        diagnostico.extend(diag)
        if df.empty:
            continue
        df["ano_arquivo"] = ano
        df.to_parquet(arq_cache, index=False)
        manifesto[chave] = versao
        partes.append(df)
    out = pd.concat(partes, ignore_index=True) if partes else pd.DataFrame()
    return out, diagnostico
