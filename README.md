# coletor-concessoes

Coletor de dados públicos de concessões rodoviárias para o app **BR Toll Roads Fin Data** (Price Discovery).

- `cadastro/concessoes.csv` — cadastro mestre (uma linha por contrato). Campos `grupo_fonte = conhecimento_previo_validar` ainda precisam de validação documental.
- `coletor/` — ANTT (tráfego físico e equivalente por praça, receita, custos, investimentos, praças com coordenadas, trechos, SIGICOR) e ARTESP (tráfego diário por praça, agregado por mês).
- Saídas em Parquet na branch `dados` (commit único, sobrescrito). Cache incremental: só reprocessa arquivos cuja data de modificação mudou na fonte.
- `saidas/diagnostico.json` lista nomes de concessionária das fontes que não casaram com o cadastro e o status de cada bloco.

Fontes: dados.antt.gov.br e dadosabertos.artesp.sp.gov.br (dados abertos).

## Bloco financeiro (workflow `financeiro-spes`, branch `dados-financeiro`)

`coletor/financeiro.py` reaproveita o código público do Price Discovery Scanning (`universo-setorial`, clonado no
workflow) para ler DFP/ITR da CVM de cada SPE (anual, trimestral e LTM; receita de construção retirada; IFRS 16).
Específico de concessões:

- CNPJ: coluna `cnpj` do cadastro ou casamento pelo nome no cadastro da CVM (`fonte_cnpj = nome (validar)`).
  SPE sem registro na CVM fica com `registro_cvm = False`.
- Provisão de manutenção: constituição (volta ao EBITDA ajustado), atualização/AVP (financeira), pagamento na DFC e
  saldo no balanço. `capex_manut` = pagamento da DFC; sem ele, saldo inicial + ajuste da DFC − saldo final
  (`capex_manut_aprox = True`).
- Debêntures das SPEs (SND + ANBIMA) com histórico da taxa indicativa desde o início da coleta.
- Acionistas pelo Formulário de Referência (posição acionária) e `cadastro/acionistas.csv` (curadoria).
