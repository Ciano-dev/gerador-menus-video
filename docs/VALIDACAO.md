# Validação e limites

## Versão portátil anterior

O pacote Windows histórico 1.0.3 (14 MB, com identidades anteriores) foi construído e testado antes da preparação desta apresentação. O relatório preservado registra 460 testes descobertos no Windows: 458 concluídos com sucesso, dois pulados e nenhum erro ou falha. Também registra duas aberturas reais do executável extraído, preparação inicial das ferramentas, reabertura offline e exportações MP4/AVI com conteúdo sintético.

Esses números pertencem à versão portátil anterior. Não são o resultado de uma nova execução de CI nem da seleção de fontes deste repositório.

A identificação do pacote é `e8112ec35699bfb427df5e443bca7a08500eadc9a87dc8f2745d79436231353c` (SHA-256). A preparação anterior da apresentação conferiu o hash e a integridade do ZIP sem reconstruí-lo. A amostra congelada está disponível apenas como proveniência, não como download do produto renomeado; esta rodada não alterou nem revalidou o pacote histórico. O manifesto completo das fontes de origem tem SHA-256 `7392ae193c6ea05c412b8e402c1a0d04a75c8ec6cb24ca9dcfe91996930efe20`.

## Demonstração deste repositório

O MP4 de exemplo foi realmente exportado pelo motor renomeado do aplicativo, com três filmagens de natureza do Pexels aprovadas para esta apresentação: cachoeira, mar e floresta. Tem cinco segundos, 1920×1080, H.264 e 30 fps, sem áudio. O arquivo foi sondado com ffprobe e decodificado por completo com FFmpeg; os 150 frames têm hashes distintos. A imagem e o GIF vieram desse resultado. [Fontes e licença das filmagens](MIDIAS.md).

A captura da interface é uma evidência anterior de teste no Windows. Não representa uma gravação de uso desta preparação nem um teste novo de GUI no Linux.

## Seleção de fontes para apresentação

A seleção anterior retirou ferramentas e testes exclusivos de uma variante antiga de avaliação. A identidade pública é Gerador de menus em vídeo: módulo `menu_video.py`, spec `GeradorMenusVideo.spec` e alvo `Gerador-de-Menus.zip`. Os testes de independência do legado conservam controles negativos e usam caminhos de fixtures opacos. O schema `.menu`, as mídias pessoais, as fontes tipográficas e o manifesto de download permanecem preservados. A demonstração foi atualizada separadamente com filmagens aprovadas.

A execução por fontes não exige pip; a suíte usa unittest. A renomeação foi testada localmente com Linux, Python 3.14.7 e FFmpeg 9.0.1, sem DISPLAY: 459 testes, 451 sucessos, oito pulados e nenhum erro ou falha. Os testes nativos pulados não validam a interface no Windows. Comandos, timestamps, logs e hashes de entrada/saída foram preservados separadamente das fontes. Uma nova verificação dos arquivos combinados deve incluir documentação e assets, não apenas o módulo principal. Um timeout do executor é um limite de teste, não prova de defeito do produto.

## Verificação de cada pacote Windows

Cada pacote novo precisa ser construído no Windows x64 e passar pelos gates nativos: abertura do EXE, consentimento inicial, reabertura offline, exportação e auditorias. Consulte a execução de CI correspondente ao commit da release e o hash do arquivo para identificar o pacote verificado. O workflow é manual; um commit ou uma documentação atualizada não comprovam que esses gates foram executados. A evidência histórica não substitui o teste do novo binário.

## O que não é garantido

- Compatibilidade com qualquer TV, equipamento USB ou codec de entrada.
- Funcionamento nativo validado em Windows 10 nesta preparação.
- Ausência de avisos ou falhas em sessões muito longas. Foram registrados avisos Tk/Tcl em testes anteriores que concluíram; a causa e o efeito em uso prolongado não foram estabelecidos.
- Igualdade pixel a pixel entre a miniatura Tkinter e a exportação.
- Aprovação de um pacote Windows apenas porque os testes Linux passaram ou porque existe um pacote histórico.
- Uso no navegador, menu clicável ou publicação automática de vídeos.

Um projeto público com testes não substitui a avaliação no computador e nas mídias de quem vai usá-lo. Para experimentar, comece com arquivos de teste e uma exportação curta.
