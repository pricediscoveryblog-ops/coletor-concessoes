"""Testes com amostras que reproduzem o leiaute real dos arquivos (copiado das fontes em 18/11/2026)."""
import pandas as pd

from coletor import antt, artesp
from coletor.comum import carregar_cadastro, ler_csv_br, mapa_aliases, mes_ano, numero_br
from coletor.main import mapear

EQ_2023 = (
    'concessionaria;mes_ano;sentido;praca;tipo_de_cobranca;categoria;tipo_de_veiculo;volume_total;multiplicador_de_tarifa;volume_veiculo_equivalente\r\n'
    '"AUTOPISTA FERNÃO DIAS";"01/01/2023";"Decrescente";"1 Norte (Mairiporã)";"N/I";"Categoria 2";"Comercial";55904,00;2,00;111808,00\r\n'
    '"AUTOPISTA FERNÃO DIAS";"01/01/2023";"Decrescente";"1 Norte (Mairiporã)";"N/I";"Categoria 4";"Comercial";38479,00;3,00;115437,00\r\n'
    '"AUTOPISTA FERNÃO DIAS";"01/01/2023";"Decrescente";"1 Norte (Mairiporã)";"N/I";"Categoria 1";"Passeio";912228,00;1,00;912228,00\r\n'
    '"AUTOPISTA FERNÃO DIAS";"01/02/2023";"Decrescente";"1 Norte (Mairiporã)";"N/I";"Categoria 1";"Passeio";1.000,00;1,00;1.000,00\r\n'
)
EQ_2026 = (
    'concessionaria;mes_ano;sentido;praca;tipo_de_cobranca;categoria_eixo;tipo_de_veiculo;volume_total;multiplicador_de_tarifa;volume_veiculo_equivalente\r\n'
    '"AUTOPISTA FLUMINENSE";"01/2026";"Crescente";"Casimiro de Abreu";"Automática";"9";"Comercial";338,00;7,00;2366,00\r\n'
    '"AUTOPISTA FLUMINENSE";"01/2026";"Crescente";"Casimiro de Abreu";"Manual";"9";"Comercial";12,00;0,00;0,00\r\n'
)


def test_numero_br():
    s = numero_br(pd.Series(["1.234,56", "1234.5", "", "105341507,38999999", '"55904,00"']))
    assert s.iloc[0] == 1234.56 and s.iloc[1] == 1234.5 and pd.isna(s.iloc[2])
    assert round(s.iloc[3], 2) == 105341507.39 and s.iloc[4] == 55904


def test_mes_ano_formatos():
    m = mes_ano(pd.Series(["01/01/2023", "01/2026", "2025-01-15"]))
    assert list(m.dt.strftime("%Y-%m")) == ["2023-01", "2026-01", "2025-01"]


def test_agregar_equivalente_formato_antigo():
    df = antt.agregar_trafego(ler_csv_br(EQ_2023), "equivalente")
    jan = df[(df.mes == "2023-01-01") & (df.tipo_veiculo == "Comercial")]
    assert jan.veic_eq.iloc[0] == 111808 + 115437
    assert jan.volume.iloc[0] == 55904 + 38479
    fev = df[df.mes == "2023-02-01"]
    assert fev.veic_eq.iloc[0] == 1000


def test_agregar_equivalente_formato_2026():
    df = antt.agregar_trafego(ler_csv_br(EQ_2026), "equivalente")
    assert len(df) == 1 and df.volume.iloc[0] == 350 and df.veic_eq.iloc[0] == 2366


def test_escolha_arquivos_prefere_mensal_e_ignora_gigante():
    res = [
        {"url": "x/volume-trafego-equivalente-praca-pedagio-2023.csv", "size": 17_000_000},
        {"url": "x/volume-trafego-equivalente-praca-pedagio-2024.csv", "size": 739_000_000},
        {"url": "x/volume-trafego-equivalente-praca-pedagio-2024_mensal_consolidado.csv", "size": 41_000_000},
        {"url": "x/volume-trafego-equivalente-praca-pedagio-2024_diario.csv", "size": 747_000_000},
        {"url": "x/volume-trafego-equivalente-praca-pedagio-2026_diario.csv", "size": 562_000_000},
    ]
    esc = antt.escolher_arquivos_trafego(res)
    assert set(esc) == {2023, 2024}
    assert esc[2024]["url"].endswith("2024_mensal_consolidado.csv")


def test_cadastro_aliases_e_regiao():
    cad = carregar_cadastro()
    assert cad["id"].is_unique
    assert (cad["regiao"] != "").all(), cad.loc[cad["regiao"] == "", "id"].tolist()
    al = mapa_aliases(cad)
    nomes = pd.DataFrame({"concessionaria_fonte": [
        "ECOVIAS RIOMINAS", "VIACOSTEIRA", "VIA DOS CRISTAIS", "ECOVIAS101 ", "L01-AUTOBAN", "L22-ECOVIAS", "NOME NOVO"]})
    m = mapear(nomes, al)
    assert list(m.id_concessao) == ["F-ERM", "F-VCOST", "F-VCRI", "F-ECAP", "S-L01", "S-L22", ""]


def test_artesp_padronizar_eixos_e_pagantes():
    bruto = ("DATA,LOTE,PRACA,SENTIDO,TIPO_CABINE,TIPO_PASSAGEM,TP_PAGAMENTO,LEVE_EIXOS_2,MOTO_EIXOS_2,PESADO_EIXOS_2,LEVE_EIXOS_3,PESADO_EIXOS_5\n"
             "2020-01-01,L01-AUTOBAN,PERUS - NORTE,NORTE,MANUAL,ISENTAS,NÃO SE APLICA/ISENTO/EVASÃO,177,0,29,0,0\n"
             "2020-01-01,L01-AUTOBAN,PERUS - NORTE,NORTE,AUTOMATICA,PAGANTE,TAG,1000,10,100,4,2\n"
             "2020-01-02,L01-AUTOBAN,PERUS - NORTE,NORTE,AUTOMATICA,PAGANTE,TAG,500,0,0,0,0\n")
    out, d = artesp.padronizar(ler_csv_br(bruto, sep=","))
    assert d["status"] == "ok" and d["n_colunas_eixos"] == 5
    pag = out[out.pagante].iloc[0]
    assert pag.veic_leve == 1504 and pag.veic_pesado == 102 and pag.veic_moto == 10
    assert pag.eixos_total == 1500 * 2 + 4 * 3 + 10 * 2 + 100 * 2 + 2 * 5
    assert pag.veic_eq == 1500 * 1 + 4 * 1.5 + 10 * 0.5 + 100 * 2 + 2 * 5
    isento = out[~out.pagante].iloc[0]
    assert isento.veic_leve == 177 and isento.veic_pesado == 29


def test_artesp_colunas_desconhecidas_nao_quebra():
    longo, d = artesp.padronizar(ler_csv_br("A;B\n1;2\n"))
    assert longo.empty and d["status"] == "colunas_nao_identificadas"
