# Validação e limites

## Versão portátil anterior

O pacote Windows 1.0.3 foi construído e testado antes da preparação desta apresentação. O relatório preservado registra 460 testes descobertos no Windows: 458 concluídos com sucesso, dois pulados e nenhum erro ou falha. Também registra duas aberturas reais do executável extraído, preparação inicial das ferramentas, reabertura offline e exportações MP4/AVI com conteúdo sintético.

Esses números pertencem à versão portátil anterior. Não são o resultado de uma nova execução de CI nem da seleção de fontes deste repositório.

A identificação do pacote é `e8112ec35699bfb427df5e443bca7a08500eadc9a87dc8f2745d79436231353c` (SHA-256). A preparação da apresentação conferiu o hash e a integridade do ZIP sem reconstruí-lo. O manifesto completo das fontes de origem tem SHA-256 `7392ae193c6ea05c412b8e402c1a0d04a75c8ec6cb24ca9dcfe91996930efe20`.

## Demonstração deste repositório

O MP4 de exemplo foi realmente exportado pelo motor do aplicativo, com três cenas geométricas originais e nomes fictícios. Tem oito segundos, 1920×1080, H.264 e 30 fps, sem áudio. O arquivo foi sondado com ffprobe e decodificado por completo com FFmpeg. A imagem e o GIF vieram desse resultado; não são telas inventadas de um programa que não foi executado.

A captura da interface é uma evidência anterior de teste no Windows. Não representa uma gravação de uso desta preparação nem um teste novo de GUI no Linux.

## Seleção de fontes para apresentação

O aplicativo não foi reescrito. A seleção preserva os arquivos de runtime e build da versão congelada, mas retira ferramentas e testes exclusivos de uma variante antiga de avaliação. Os testes que comprovam independência desse legado conservam seu objetivo, com adaptação dos respectivos dublês de teste. A documentação mudou para explicar o produto atual.

A execução por fontes não exige pip; a suíte usa unittest. As condições e o resultado da nova verificação local ficam registrados separadamente do resultado histórico do pacote Windows.

## O que não é garantido

- Compatibilidade com qualquer TV, equipamento USB ou codec de entrada.
- Funcionamento nativo validado em Windows 10 nesta preparação.
- Ausência de avisos ou falhas em sessões muito longas. Foram registrados avisos Tk/Tcl em testes anteriores que concluíram; a causa e o efeito em uso prolongado não foram estabelecidos.
- Igualdade pixel a pixel entre a miniatura Tkinter e a exportação.
- Um novo executável Windows obtido a partir desta apresentação: nenhum rebuild foi feito.
- Uso no navegador, menu clicável ou publicação automática de vídeos.

Um projeto público com testes não substitui a avaliação no computador e nas mídias de quem vai usá-lo. Para experimentar, comece com arquivos de teste e uma exportação curta.
