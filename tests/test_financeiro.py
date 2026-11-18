"""Teste do bloco financeiro com um ZIP da CVM sintético (mesmo leiaute dos dados abertos), em modo offline.
Precisa do código do universo-setorial (UNIVERSO_DIR ou ../universo-setorial); sem ele, o teste é pulado."""
import importlib
import io
import os
import sys
import zipfile
from pathlib import Path

import pandas as pd
import pytest

UNIV = Path(os.environ.get("UNIVERSO_DIR", Path(__file__).resolve().parents[2] / "universo-setorial"))
pytestmark = pytest.mark.skipif(not (UNIV / "universo.py").exists(), reason="universo-setorial ausente")

CNPJ = "11222333000181"
CAB = "CNPJ_CIA;DT_REFER;VERSAO;DENOM_CIA;CD_CVM;GRUPO_DFP;MOEDA;ESCALA_MOEDA;ORDEM_EXERC;DT_INI_EXERC;DT_FIM_EXERC;CD_CONTA;DS_CONTA;VL_CONTA;ST_CONTA_FIXA"


def _linhas(ano: int, fator: float, saldo_ini: float, saldo_fim: float, pago_na_dfc: bool) -> dict[str, list[str]]:
    ref, ini, fim = f"{ano}-12-31", f"{ano}-01-01", f"{ano}-12-31"
    cnpj = "11.222.333/0001-81"

    def f(cd, ds, vl, fluxo=True, ordem="ÚLTIMO", dt_ini=ini, dt_fim=fim):
        return f"{cnpj};{ref};1;CONC TESTE S.A.;9999;DF;REAL;MIL;{ordem};{dt_ini if fluxo else ''};{dt_fim};{cd};{ds};{vl:.0f};S"
    dre = [f("3.01", "Receita de Venda de Bens e/ou Serviços", 1500e3 * fator),
           f("3.01.02", "Receita de construção", 300e3),
           f("3.02", "Custo dos Bens e/ou Serviços Vendidos", -700e3 * fator), f("3.02.02", "Custo de construção", -300e3),
           f("3.03", "Resultado Bruto", 800e3 * fator), f("3.05", "Resultado Antes do Resultado Financeiro e dos Tributos", 500e3 * fator),
           f("3.06", "Resultado Financeiro", -200e3), f("3.06.01", "Receitas Financeiras", 50e3), f("3.06.02", "Despesas Financeiras", -250e3),
           f("3.11", "Lucro/Prejuízo Consolidado do Período", 200e3 * fator)]
    bpa = [f("1", "Ativo Total", 5000e3, False), f("1.01", "Ativo Circulante", 800e3, False),
           f("1.01.01", "Caixa e Equivalentes de Caixa", 300e3, False), f("1.01.03", "Contas a Receber", 120e3, False)]
    bpp = [f("2", "Passivo Total", 5000e3, False), f("2.01", "Passivo Circulante", 600e3, False),
           f("2.01.02", "Fornecedores", 80e3, False), f("2.01.04", "Empréstimos e Financiamentos", 250e3, False),
           f("2.02", "Passivo Não Circulante", 3000e3, False), f("2.02.01", "Empréstimos e Financiamentos", 2200e3, False),
           f("2.02.04", "Provisões", saldo_fim * 1e3, False),
           f("2.02.04.02", "Outras Provisões", saldo_fim * 1e3, False),
           f("2.02.04.02.01", "Provisão para manutenção", saldo_fim * 1e3, False),
           f("2.03", "Patrimônio Líquido Consolidado", 1400e3, False)]
    dfc = [f("6.01", "Caixa Líquido Atividades Operacionais", 600e3), f("6.01.01", "Caixa Gerado nas Operações", 900e3),
           f("6.01.01.01", "Lucro líquido do exercício", 200e3 * fator), f("6.01.01.02", "Depreciação e amortização", 150e3),
           f("6.01.01.03", "Constituição de provisão para manutenção", 60e3),
           f("6.01.01.04", "Ajuste a valor presente sobre provisão para manutenção", 10e3),
           f("6.01.02", "Variações nos Ativos e Passivos", -100e3),
           f("6.01.02.03", "Imposto de renda e contribuição social pagos", -50e3),
           f("6.02", "Caixa Líquido Atividades de Investimento", -400e3),
           f("6.02.01", "Adições ao ativo intangível", -400e3),
           f("6.03", "Caixa Líquido Atividades de Financiamento", -300e3),
           f("6.03.01", "Pagamento de juros de debêntures", -220e3),
           f("6.03.02", "Amortização de principal de debêntures", -150e3)]
    if pago_na_dfc:
        dfc.append(f("6.01.02.05", "Pagamento de provisão para manutenção", -45e3))
    # comparativo (penúltimo) do balanço = saldo inicial
    bpp.append(f("2.02.04.02.01", "Provisão para manutenção", saldo_ini * 1e3, False, ordem="PENÚLTIMO",
                 dt_fim=f"{ano - 1}-12-31"))
    dva = [f("7.01", "Receitas", 1700e3), f("7.01.03", "Receitas refs. à Construção de Ativos Próprios", 300e3)]
    return {"DRE": dre, "BPA": bpa, "BPP": bpp, "DFC_MI": dfc, "DVA": dva}


def _zip(pasta: Path, ano: int, **kw):
    with zipfile.ZipFile(pasta / f"dfp_cia_aberta_{ano}.zip", "w") as z:
        for demo, ls in _linhas(ano, **kw).items():
            z.writestr(f"dfp_cia_aberta_{demo}_con_{ano}.csv", ("\n".join([CAB] + ls)).encode("latin-1"))
        z.writestr(f"dfp_cia_aberta_{ano}.csv", f"CNPJ_CIA;DT_REFER;DT_RECEB;LINK_DOC\n11.222.333/0001-81;{ano}-12-31;{ano + 1}-03-20;x\n")


@pytest.fixture()
def fin(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()
    _zip(cache, 2024, fator=1.0, saldo_ini=100, saldo_fim=120, pago_na_dfc=True)
    _zip(cache, 2025, fator=1.1, saldo_ini=120, saldo_fim=130, pago_na_dfc=False)
    monkeypatch.setenv("CVM_OFFLINE", "1")
    monkeypatch.setenv("CVM_CACHE", str(cache))
    monkeypatch.setenv("UNIVERSO_DIR", str(UNIV))
    for m in ("coletor_setorial", "universo", "dividas", "coletor.financeiro"):
        sys.modules.pop(m, None)
    import coletor.financeiro as f
    return importlib.reload(f)


def test_extras_classifica_linhas(fin):
    cs, _uv, _dv = fin._importar()
    sub = pd.DataFrame({"DEMO": ["DFC_MI"] * 5 + ["BPP"], "CD_CONTA": ["6.01.01.03", "6.01.01.04", "6.01.01.05", "6.01.02.05",
                                                                     "6.01.01.06", "2.02.04.02.01"],
                        "DS_CONTA": ["Constituição de provisão para manutenção",
                                     "Atualização monetária sobre provisão para manutenção",
                                     "Provisão e atualização monetária para manutenção",
                                     "Pagamento de provisão para manutenção",
                                     "Receita de operação e manutenção", "Provisão para manutenção"],
                        "VL": [60.0, 10.0, 5.0, -45.0, 99.0, 130.0]})
    sub["DESC"] = sub["DS_CONTA"].map(cs.norm)
    e = fin.extras(sub, cs)
    assert e["prov_manut_constituicao"] == 65 and e["prov_manut_misto"] == 5
    assert e["prov_manut_avp"] == 10 and e["prov_manut_pago_dfc"] == 45 and e["prov_manut_saldo"] == 130


def test_casar_cnpj_por_nome(fin):
    cad = pd.DataFrame({"id": ["X", "Y"], "nome": ["ROTA TESTE", "SEM CVM"], "nomes_fonte": ["", ""], "cnpj": ["", ""]})
    cvm = pd.DataFrame({"CNPJ_NUM": ["1", "2"], "DENOM_SOCIAL": ["CONCESSIONARIA ROTA TESTE S.A.", "PADARIA ROTA"],
                        "SIT": ["ATIVO", "ATIVO"], "CATEG_REG": ["B", "A"]})
    s = fin.casar_cnpj(cad, cvm).set_index("id")
    assert s.loc["X", "cnpj"] == "1" and s.loc["X", "registro_cvm"]
    assert s.loc["Y", "cnpj"] == "" and not s.loc["Y", "registro_cvm"]


def test_coleta_offline_e_capex_manut(fin):
    spe = pd.DataFrame({"id": ["T-1"], "nome": ["CONC TESTE"], "cnpj": [CNPJ], "registro_cvm": [True]})
    diag = {}
    b = fin.coletar_financeiro(spe, diag)
    a = b[b["tipo"] == "anual"].set_index("exercicio")
    assert set(a.index) == {2024, 2025}
    # receita líquida sem construção (1.500 − 300) e construção fora do CPV
    assert round(a.loc[2024, "receita"], 1) == 1200.0
    assert a.loc[2024, "prov_manut_constituicao"] == 60 and a.loc[2024, "prov_manut_avp"] == 10
    # 2024: pagamento identificado na DFC
    assert a.loc[2024, "capex_manut"] == 45 and not a.loc[2024, "capex_manut_aprox"]
    # 2025: sem linha de pagamento -> 120 + (60 + 10) − 130 = 60
    assert round(a.loc[2025, "capex_manut"], 1) == 60.0 and a.loc[2025, "capex_manut_aprox"]
    assert a.loc[2025, "divida_cp"] == 250 and a.loc[2025, "divida_lp"] == 2200
    assert a.loc[2025, "ids"] == "T-1"
