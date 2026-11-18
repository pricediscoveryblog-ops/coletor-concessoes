"""Funções comuns: download, leitura de CSV brasileiro, cadastro e chaves regionais."""
from __future__ import annotations

import io
import re
import time
import unicodedata
from pathlib import Path

import pandas as pd
import requests

RAIZ = Path(__file__).resolve().parent.parent
CADASTRO = RAIZ / "cadastro" / "concessoes.csv"
SAIDA = RAIZ / "saidas"

REGIAO_POR_UF = {
    "AC": "N", "AM": "N", "AP": "N", "PA": "N", "RO": "N", "RR": "N", "TO": "N",
    "AL": "NE", "BA": "NE", "CE": "NE", "MA": "NE", "PB": "NE", "PE": "NE", "PI": "NE", "RN": "NE", "SE": "NE",
    "DF": "CO", "GO": "CO", "MS": "CO", "MT": "CO",
    "ES": "SE", "MG": "SE", "RJ": "SE", "SP": "SE",
    "PR": "S", "RS": "S", "SC": "S",
}

SESSAO = requests.Session()
SESSAO.headers["User-Agent"] = "coletor-concessoes (pricediscovery)"


def baixar(url: str, tentativas: int = 4, timeout: int = 600) -> bytes:
    """Baixa um arquivo com novas tentativas (os portais públicos oscilam)."""
    ultimo = None
    for i in range(tentativas):
        try:
            r = SESSAO.get(url, timeout=timeout)
            r.raise_for_status()
            return r.content
        except Exception as e:  # noqa: BLE001
            ultimo = e
            time.sleep(5 * (i + 1))
    raise RuntimeError(f"Falha ao baixar {url}: {ultimo}")


def decodificar(conteudo: bytes) -> str:
    """Os arquivos da ANTT/ARTESP alternam entre UTF-8 e Latin-1."""
    try:
        return conteudo.decode("utf-8-sig")
    except UnicodeDecodeError:
        return conteudo.decode("latin-1")


def ler_csv_br(conteudo: bytes | str, sep: str = ";", **kw) -> pd.DataFrame:
    texto = decodificar(conteudo) if isinstance(conteudo, bytes) else conteudo
    return pd.read_csv(io.StringIO(texto), sep=sep, dtype=str, keep_default_na=False, **kw)


def numero_br(serie: pd.Series) -> pd.Series:
    """'1.234,56' -> 1234.56 ; '1234.56' -> 1234.56 ; '' -> NaN."""
    s = serie.astype(str).str.strip().str.replace('"', "", regex=False)
    tem_virgula = s.str.contains(",", regex=False)
    s_br = s.where(~tem_virgula, s.str.replace(".", "", regex=False).str.replace(",", ".", regex=False))
    return pd.to_numeric(s_br.replace("", None), errors="coerce")


def normalizar_nome(nome: str) -> str:
    """Chave de casamento entre fontes: sem acento, maiúsculo, espaços simples."""
    n = unicodedata.normalize("NFKD", str(nome)).encode("ascii", "ignore").decode()
    n = re.sub(r"[^A-Z0-9 ]", " ", n.upper())
    return re.sub(r"\s+", " ", n).strip()


def carregar_cadastro() -> pd.DataFrame:
    cad = pd.read_csv(CADASTRO, sep=";", dtype=str, keep_default_na=False)
    cad["regiao"] = cad["uf_principal"].map(REGIAO_POR_UF).fillna("")
    return cad


def mapa_aliases(cad: pd.DataFrame) -> dict[str, str]:
    """nome normalizado na fonte -> id da concessão."""
    m: dict[str, str] = {}
    for _, r in cad.iterrows():
        for alias in [r["nome"], *r["nomes_fonte"].split("|")]:
            if alias.strip():
                m[normalizar_nome(alias)] = r["id"]
    return m


def mes_ano(serie: pd.Series) -> pd.Series:
    """Aceita '01/01/2023', '01/2026', '2025-01-15' -> Timestamp do 1º dia do mês."""
    s = serie.astype(str).str.strip().str.replace('"', "", regex=False)
    d = pd.to_datetime(s, format="%d/%m/%Y", errors="coerce")
    d = d.fillna(pd.to_datetime(s, format="%m/%Y", errors="coerce"))
    d = d.fillna(pd.to_datetime(s, format="%Y-%m-%d", errors="coerce"))
    return d.dt.to_period("M").dt.to_timestamp()
