# coletor-concessoes

Coletor de dados públicos de concessões rodoviárias para o app **BR Toll Roads Fin Data** (Price Discovery).

- `cadastro/concessoes.csv` — cadastro mestre (uma linha por contrato). Campos `grupo_fonte = conhecimento_previo_validar` ainda precisam de validação documental.
- `coletor/` — ANTT (tráfego físico e equivalente por praça, receita, custos, investimentos, praças com coordenadas, trechos, SIGICOR) e ARTESP (tráfego diário por praça, agregado por mês).
- Saídas em Parquet na branch `dados` (commit único, sobrescrito). Cache incremental: só reprocessa arquivos cuja data de modificação mudou na fonte.
- `saidas/diagnostico.json` lista nomes de concessionária das fontes que não casaram com o cadastro e o status de cada bloco.

Fontes: dados.antt.gov.br e dadosabertos.artesp.sp.gov.br (dados abertos).
