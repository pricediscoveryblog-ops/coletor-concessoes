"""Bloco financeiro das SPEs (CVM) — roda no workflow `financeiro` e publica na branch `dados-financeiro`.

Reaproveita o código já validado do Price Discovery Scanning (repo público pricediscoveryblog-ops/universo-setorial,
clonado pelo workflow em UNIVERSO_DIR): leitura de DFP/ITR, diferença de acumulados, LTM, retirada da receita de
construção (ICPC 01), arrendamentos (IFRS 16), serviço da dívida e IR pagos. Aqui entram só o que é específico de
concessão rodoviária:

- CNPJ de cada SPE: coluna `cnpj` do cadastro ou, sem ela, casamento pelo nome no cadastro de companhias da CVM.
  SPE sem registro na CVM fica marcada (o app mostra o aviso, sem dados financeiros).
- Provisão de manutenção (DFC e balanço):
    prov_manut_constituicao  ajuste não caixa da DFC (6.01.01) que volta ao EBITDA (inclui linhas mistas
                             "provisão e atualização monetária", que a DFC não separa: prov_manut_misto)
    prov_manut_avp           atualização monetária / AVP pura da provisão (financeira, não volta ao EBITDA)
    prov_manut_pago_dfc      pagamento/utilização da provisão identificado na DFC (saída de caixa, positivo)
    prov_manut_saldo         saldo da provisão no balanço (circulante + não circulante)
    capex_manut              = prov_manut_pago_dfc; sem linha na DFC, aproximado por
                             saldo inicial + ajuste da DFC (0 se não identificado) − saldo final (capex_manut_aprox=True)
- Dívida de curto e longo prazo (BPP 2.01.04 e 2.02.01) para o perfil de vencimentos.
- Debêntures das SPEs (SND + ANBIMA, mesmas funções do dividas.py) e histórico da taxa indicativa desde o início da coleta.
- Acionistas das SPEs no Formulário de Referência (posição acionária), quando a SPE entrega o FRE.

Saídas em saidas_fin/: spe_cvm, financeiro_spe, debentures_spe, debentures_hist, acionistas_fre (.parquet) e
diagnostico_fin.json.
"""
from __future__ import annotations

import json
import os
import re
import sys
import traceback
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .comum import RAIZ, carregar_cadastro, normalizar_nome

UNIV = Path(os.environ.get("UNIVERSO_DIR", RAIZ.parent / "universo-setorial")).resolve()
SAIDA = RAIZ / "saidas_fin"
N_ANOS = int(os.environ.get("FIN_N_ANOS", "10"))

STOP = {"S", "A", "SA", "S A", "CONCESSIONARIA", "CONCESSAO", "DE", "DA", "DO", "DAS", "DOS", "E", "RODOVIAS",
        "RODOVIA", "SPE", "CIA", "COMPANHIA"}
RX_CONCESSAO = re.compile(r"CONCESS|RODOV|AUTOPISTA|\bVIA|PONTE|ROTA|ECO|TRIANGULO|RODOANEL")

# ---- provisão de manutenção (texto normalizado, minúsculo e sem acento)
RX_MANUT = re.compile(r"manutenc")
RX_MANUT_EXC = re.compile(r"receita|operacao e manutencao|ativos? de (?:contrato|concess)|impairment|oneros")
RX_PAGO = re.compile(r"\bpag|utiliz|realiz|\bbaixa|liquidac|consum")
RX_AVP = re.compile(r"atualiz|valor presente|\bavp\b|juros|encargo|monetari")
RX_ACRESC = re.compile(r"^provis|constitu|complement|revers|provisao e |provisoes e ")
NOVOS_FLUXOS = ["prov_manut_constituicao", "prov_manut_avp", "prov_manut_misto", "prov_manut_pago_dfc"]
NOVOS_ESTOQUES = ["prov_manut_saldo", "divida_cp", "divida_lp"]


def _importar():
    if str(UNIV) not in sys.path:
        sys.path.insert(0, str(UNIV))
    import coletor_setorial as cs  # noqa: E402
    import dividas as dv  # noqa: E402
    import universo as uv  # noqa: E402
    return cs, uv, dv


# ------------------------------------------------------------------ CNPJ das SPEs
def tokens(nome: str) -> set[str]:
    return set(normalizar_nome(nome).split()) - STOP


def casar_cnpj(cad: pd.DataFrame, cvm: pd.DataFrame) -> pd.DataFrame:
    """Uma linha por SPE do cadastro: CNPJ, nome e situação na CVM. Casamento pelo nome só quando todas as palavras
    distintivas do nome (ou de um alias) aparecem na denominação social e ela é de concessionária."""
    cvm = cvm.copy()
    cvm["_n"] = (cvm["DENOM_SOCIAL"].fillna("") + " " + cvm.get("DENOM_COMERC", pd.Series("", index=cvm.index)).fillna("")).map(normalizar_nome)
    cvm["_tok"] = cvm["_n"].str.split().map(set)
    cvm["_ativo"] = cvm.get("SIT", pd.Series("", index=cvm.index)).fillna("").str.upper().str.contains("ATIVO")
    cvm = cvm[cvm["_n"].str.contains(RX_CONCESSAO)]
    por_cnpj = cvm.drop_duplicates("CNPJ_NUM").set_index("CNPJ_NUM")
    linhas = []
    for _, r in cad.iterrows():
        cnpj = re.sub(r"\D", "", r.get("cnpj", "") or "").zfill(14) if (r.get("cnpj") or "").strip() else ""
        fonte, cands = ("cadastro" if cnpj else ""), []
        if not cnpj:
            for alias in [r["nome"], *str(r["nomes_fonte"]).split("|")]:
                t = tokens(alias)
                if not t or re.fullmatch(r"L ?\d{2}", normalizar_nome(alias)):
                    continue
                ok = cvm[cvm["_tok"].map(lambda s, t=t: t <= s)]
                if len(ok):
                    ok = ok.assign(_len=ok["_n"].str.len()).sort_values(["_ativo", "_len"], ascending=[False, True])
                    cands = list(dict.fromkeys(ok["CNPJ_NUM"]))
                    break
            if cands:
                cnpj, fonte = cands[0], "nome (validar)"
        info = por_cnpj.loc[cnpj] if cnpj in por_cnpj.index else None
        linhas.append({"id": r["id"], "nome": r["nome"], "cnpj": cnpj, "fonte_cnpj": fonte,
                       "registro_cvm": info is not None,
                       "denominacao_cvm": "" if info is None else info["DENOM_SOCIAL"],
                       "situacao_cvm": "" if info is None else str(info.get("SIT", "")),
                       "categoria_cvm": "" if info is None else str(info.get("CATEG_REG", "")),
                       "candidatos": "|".join(cands[:5]), "ambiguo": len(cands) > 1})
    return pd.DataFrame(linhas)


# ------------------------------------------------------------------ extras por demonstração
def extras(sub: pd.DataFrame, cs) -> dict:
    out = {k: np.nan for k in NOVOS_FLUXOS + NOVOS_ESTOQUES}
    out["prov_manut_linhas"] = ""
    dfc_demo = "DFC_MI" if (sub["DEMO"] == "DFC_MI").any() else "DFC_MD"
    d = sub[(sub["DEMO"] == dfc_demo) & sub["CD_CONTA"].str.startswith("6.01.") & sub["DESC"].str.contains(RX_MANUT)
            & ~sub["DESC"].str.contains(RX_MANUT_EXC)]
    d = cs.topo(d)
    soma = {"c": [], "a": [], "m": [], "p": []}
    for r in d.itertuples():
        desc, ajuste = r.DESC, r.CD_CONTA.startswith("6.01.01")
        if not ajuste or RX_PAGO.search(desc):
            k = "p"
        elif RX_AVP.search(desc):
            k = "m" if RX_ACRESC.search(desc) else "a"
        else:
            k = "c"
        soma[k].append(r.VL)
    if soma["c"] or soma["m"]:
        out["prov_manut_constituicao"] = float(np.sum(soma["c"] + soma["m"]))
    if soma["m"]:
        out["prov_manut_misto"] = float(np.sum(soma["m"]))
    if soma["a"]:
        out["prov_manut_avp"] = float(np.sum(soma["a"]))
    if soma["p"]:
        out["prov_manut_pago_dfc"] = -float(np.sum(soma["p"]))
    if len(d):
        out["prov_manut_linhas"] = " | ".join(f"{r.CD_CONTA} {r.DS_CONTA}: {r.VL:.1f}" for r in d.itertuples())[:600]
    b = sub[(sub["DEMO"] == "BPP") & sub["CD_CONTA"].str.match(r"^2\.0[12]\.") & sub["DESC"].str.contains(RX_MANUT)
            & sub["DESC"].str.contains(r"provis|obrigac")]
    b = cs.topo(b)
    if len(b):
        out["prov_manut_saldo"] = float(b["VL"].sum())
    out["divida_cp"] = cs.conta(sub, "BPP", "2.01.04")
    out["divida_lp"] = cs.conta(sub, "BPP", "2.02.01")
    return out


def capex_manutencao(base: pd.DataFrame) -> pd.DataFrame:
    """capex_manut = pagamento da provisão na DFC; sem ele, saldo inicial + ajuste da DFC − saldo final."""
    if base.empty:
        return base
    b = base.copy()
    b["dt_fim"] = pd.to_datetime(b["dt_fim"])
    saldos = b.dropna(subset=["prov_manut_saldo"]).drop_duplicates(["cnpj", "dt_fim"]).set_index(["cnpj", "dt_fim"])["prov_manut_saldo"]

    def saldo_em(cnpj, alvo):
        if cnpj not in saldos.index.get_level_values(0):
            return np.nan
        s = saldos.loc[cnpj]
        perto = s[(s.index >= alvo - pd.Timedelta(days=20)) & (s.index <= alvo + pd.Timedelta(days=20))]
        return float(perto.iloc[-1]) if len(perto) else np.nan
    ini = []
    for r in b.itertuples():
        meses = 3 if r.tipo == "trimestral" else 12
        alvo = (r.dt_fim + pd.Timedelta(days=1) - pd.DateOffset(months=meses)) - pd.Timedelta(days=1)
        ini.append(saldo_em(r.cnpj, alvo))
    b["prov_manut_saldo_ini"] = ini
    ajuste = b["prov_manut_constituicao"].fillna(0) + b["prov_manut_avp"].fillna(0)
    aprox = (b["prov_manut_saldo_ini"] + ajuste - b["prov_manut_saldo"]).clip(lower=0)
    tem = b["prov_manut_pago_dfc"].notna()
    b["capex_manut"] = b["prov_manut_pago_dfc"].where(tem, aprox)
    b["capex_manut_aprox"] = ~tem & b["capex_manut"].notna()
    b["dt_fim"] = b["dt_fim"].dt.date
    return b


# ------------------------------------------------------------------ demonstrações
def coletar_financeiro(spe: pd.DataFrame, diag: dict) -> pd.DataFrame:
    cs, uv, _ = _importar()
    cnpjs = set(spe.loc[spe["registro_cvm"], "cnpj"])
    if not cnpjs:
        return pd.DataFrame()
    original = cs.calcular

    def calcular(sub, aud, ctx):
        m = original(sub, aud, ctx)
        m.update(extras(sub, cs))
        return m
    cs.calcular = calcular
    for k in NOVOS_FLUXOS:
        if k not in uv.FLUXOS:
            uv.FLUXOS.append(k)
    for k in NOVOS_ESTOQUES:
        if k not in uv.ESTOQUES:
            uv.ESTOQUES.append(k)
    linhas_txt: dict[tuple, str] = {}
    snaps: dict[str, list] = {c: [] for c in cnpjs}
    for doc in ("DFP", "ITR"):
        anos = uv.anos_disponiveis(doc)
        if not anos:
            diag[f"{doc}_anos"] = "nenhum"
            continue
        for ano in [a for a in anos if a >= max(anos) - N_ANOS]:
            print(f"  {doc} {ano}...", flush=True)
            bruto = uv.ler_doc(doc, ano, cnpjs, forcar=(ano >= max(anos) - 1 and not cs.OFFLINE))
            if bruto.empty:
                continue
            for s in uv.snapshots(cs.preparar(bruto), doc, []):
                snaps[s["cnpj"]].append(s)
                linhas_txt[(s["cnpj"], s["doc"], s["exercicio"], s["trimestre"])] = s["m"].get("prov_manut_linhas", "")
    linhas, sem = [], []
    for cnpj in sorted(cnpjs):
        if not snaps[cnpj]:
            sem.append(cnpj)
            continue
        l, _a, _info = uv.montar_empresa(snaps[cnpj], N_ANOS, construcao=True)
        linhas += l
    diag["sem_dfp_itr_estruturada"] = sem
    base = pd.DataFrame(linhas)
    if base.empty:
        return base
    base = capex_manutencao(base)
    ids = spe[spe["cnpj"] != ""].groupby("cnpj")["id"].apply(lambda s: "|".join(s))
    base.insert(0, "ids", base["cnpj"].map(ids))
    return base


# ------------------------------------------------------------------ debêntures
def coletar_debentures(spe: pd.DataFrame, hist_ant: pd.DataFrame, diag: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    _cs, _uv, dv = _importar()
    cnpjs = set(spe.loc[spe["cnpj"] != "", "cnpj"])
    snd = dv.ler_snd()
    snd = snd[snd["cnpj_emissor"].isin(cnpjs)].copy()
    anb = dv.anbima_ate(date.today())
    if anb is not None:
        snd = snd.merge(anb, on="codigo", how="left")
        snd["saldo_pu_par_rs_mm"] = np.where(snd["pct_pu_par"].notna() & snd["pu"].notna(),
                                             snd["quantidade_mercado"] * snd["pu"] / (snd["pct_pu_par"] / 100) / 1e6, np.nan)
    else:
        snd["saldo_pu_par_rs_mm"] = np.nan
        for c in ("indice_correcao", "taxa_indicativa", "pu", "pct_pu_par", "duration_du", "ref_ntnb", "data_anbima"):
            snd[c] = np.nan
    snd["saldo_vna_rs_mm"] = snd["quantidade_mercado"] * snd["vna"] / 1e6
    snd["saldo_rs_mm"] = snd["saldo_pu_par_rs_mm"].fillna(snd["saldo_vna_rs_mm"])
    snd["duration_anos"] = snd["duration_du"] / 252
    ids = spe[spe["cnpj"] != ""].groupby("cnpj")["id"].apply(lambda s: "|".join(s))
    snd.insert(0, "ids", snd["cnpj_emissor"].map(ids))
    diag["debentures"] = {"quantidade": int(len(snd)), "emissores": int(snd["cnpj_emissor"].nunique()),
                          "data_anbima": None if anb is None else str(anb["data_anbima"].iloc[0])}
    novo = snd.dropna(subset=["taxa_indicativa"])[["ids", "cnpj_emissor", "codigo", "data_anbima", "indice", "taxa_emissao",
                                                   "taxa_indicativa", "duration_anos", "pu", "pct_pu_par", "saldo_rs_mm"]]
    hist = pd.concat([hist_ant, novo], ignore_index=True) if len(hist_ant) else novo
    if len(hist):
        hist["data_anbima"] = pd.to_datetime(hist["data_anbima"]).dt.date
        hist = hist.drop_duplicates(["codigo", "data_anbima"], keep="last").sort_values(["codigo", "data_anbima"])
    return snd, hist


# ------------------------------------------------------------------ acionistas (FRE)
def coletar_acionistas(spe: pd.DataFrame, diag: dict) -> pd.DataFrame:
    cs, _uv, dv = _importar()
    cnpjs = set(spe.loc[spe["registro_cvm"], "cnpj"])
    norm = cs.norm
    partes = []
    for ano in (date.today().year, date.today().year - 1):
        try:
            p = cs.baixar(dv.FRE.format(ano=ano), cs.CACHE / f"fre_cia_aberta_{ano}.zip", forcar=ano == date.today().year)
            with zipfile.ZipFile(p) as z:
                arq = [n for n in z.namelist() if "posicao_acionaria" in n and "classe" not in n]
                if not arq:
                    diag[f"fre_{ano}"] = "sem arquivo de posição acionária"
                    continue
                df = pd.read_csv(z.open(arq[0]), sep=";", encoding="latin-1", dtype=str)
        except Exception as e:  # noqa: BLE001
            diag[f"fre_{ano}"] = f"falhou: {e}"
            continue
        col = lambda *ps, ex=(): next((c for c in df.columns if all(p in norm(c) for p in ps)  # noqa: E731
                                       and not any(x in norm(c) for x in ex)), None)
        c_cia, c_ac = col("cnpj", "companhia"), col("acionista", ex=("cpf", "cnpj", "tipo", "id_", "controlador", "residente"))
        c_doc, c_pct = col("cpf", "acionista"), col("percentual", "total")
        c_ctrl, c_dt, c_ver = col("controlador"), col("data", "referencia"), col("versao")
        if not (c_cia and c_ac):
            diag[f"fre_{ano}"] = f"colunas inesperadas: {list(df.columns)[:12]}"
            continue
        df["_cnpj"] = df[c_cia].map(cs.so_digitos)
        df = df[df["_cnpj"].isin(cnpjs)]
        if df.empty:
            continue
        if c_dt and c_ver:
            df["_v"] = pd.to_numeric(df[c_ver], errors="coerce")
            df = df[(df[c_dt] == df.groupby("_cnpj")[c_dt].transform("max"))]
            df = df[df["_v"] == df.groupby("_cnpj")["_v"].transform("max")]
        partes.append(pd.DataFrame({
            "cnpj": df["_cnpj"], "ano_fre": ano, "data_referencia": df[c_dt] if c_dt else "",
            "acionista": df[c_ac].str.strip(), "cnpj_cpf_acionista": df[c_doc].map(cs.so_digitos) if c_doc else "",
            "participacao_pct": pd.to_numeric(df[c_pct].str.replace(",", "."), errors="coerce") if c_pct else np.nan,
            "controlador": df[c_ctrl].fillna("") if c_ctrl else ""}))
    if not partes:
        return pd.DataFrame()
    a = pd.concat(partes, ignore_index=True)
    a = a[a["ano_fre"] == a.groupby("cnpj")["ano_fre"].transform("max")]
    ids = spe[spe["cnpj"] != ""].groupby("cnpj")["id"].apply(lambda s: "|".join(s))
    a.insert(0, "ids", a["cnpj"].map(ids))
    return a


# ------------------------------------------------------------------ main
def main() -> None:
    SAIDA.mkdir(parents=True, exist_ok=True)
    diag: dict = {"executado_em": datetime.now(timezone.utc).isoformat(), "universo_dir": str(UNIV), "blocos": {}}
    cad = carregar_cadastro()

    def bloco(nome, func):
        try:
            func()
            diag["blocos"][nome] = "ok"
        except Exception:  # noqa: BLE001
            diag["blocos"][nome] = "falhou: " + traceback.format_exc()[-1500:]
            print(f"[ERRO] {nome}\n{traceback.format_exc()}", flush=True)

    ctx: dict = {}

    def _spe():
        cs, _uv, _dv = _importar()
        cs.CACHE.mkdir(parents=True, exist_ok=True)
        spe = casar_cnpj(cad, cs.carregar_cadastro())
        spe.to_parquet(SAIDA / "spe_cvm.parquet", index=False)
        ctx["spe"] = spe
        print(f"[OK] spe_cvm: {int(spe['registro_cvm'].sum())} de {len(spe)} SPEs com registro na CVM", flush=True)
    bloco("spe_cvm", _spe)
    if "spe" not in ctx:          # sem o casamento de CNPJ não há o que coletar; mantém a saída anterior
        arq = SAIDA / "spe_cvm.parquet"
        if arq.exists():
            ctx["spe"] = pd.read_parquet(arq)
    if "spe" in ctx:
        spe = ctx["spe"]

        def _fin():
            f = coletar_financeiro(spe, diag)
            if len(f):
                f.to_parquet(SAIDA / "financeiro_spe.parquet", index=False)
            print(f"[OK] financeiro_spe: {len(f):,} linhas, {f['cnpj'].nunique() if len(f) else 0} SPEs", flush=True)
        bloco("financeiro", _fin)

        def _deb():
            arq_h = SAIDA / "debentures_hist.parquet"
            ant = pd.read_parquet(arq_h) if arq_h.exists() else pd.DataFrame()
            d, h = coletar_debentures(spe, ant, diag)
            d.to_parquet(SAIDA / "debentures_spe.parquet", index=False)
            if len(h):
                h.to_parquet(arq_h, index=False)
            print(f"[OK] debentures_spe: {len(d)} debêntures; histórico {len(h)} linhas", flush=True)
        bloco("debentures", _deb)

        def _acs():
            a = coletar_acionistas(spe, diag)
            if len(a):
                a.to_parquet(SAIDA / "acionistas_fre.parquet", index=False)
            print(f"[OK] acionistas_fre: {len(a)} linhas", flush=True)
        bloco("acionistas", _acs)
    (SAIDA / "diagnostico_fin.json").write_text(json.dumps(diag, indent=1, ensure_ascii=False, default=str))
    falhas = [k for k, v in diag["blocos"].items() if v != "ok"]
    print("Blocos com falha:", falhas or "nenhum")


if __name__ == "__main__":
    main()
