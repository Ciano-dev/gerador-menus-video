# Componentes de terceiros e distribuição

## Código do projeto

Esta apresentação não acrescenta uma licença de software livre ao código autoral. Disponibilizar as fontes para consulta não equivale, por si só, a conceder licença MIT, Apache ou outra permissão geral de redistribuição. A escolha de uma licença para o projeto permanece separada da preparação do portfólio.

O pacote escolhido funciona sem proteção de avaliação. Isso descreve o comportamento do aplicativo, não a licença jurídica do código.

## Fontes tipográficas

As famílias Arimo, Montserrat, Oswald, Merriweather, Playfair Display, Cinzel, Dancing Script e Lobster mantêm seus arquivos `OFL.txt` em cada pasta de `font_assets/`. O catálogo e os hashes dos arquivos estão em [font_assets/manifest.json](../font_assets/manifest.json).

Essas licenças e atribuições devem acompanhar as fontes. A opção Atual pode usar Arial já existente no Windows; este repositório não redistribui Arial.

## FFmpeg

FFmpeg e ffprobe são ferramentas externas. Pela execução das fontes, o usuário precisa tê-las disponíveis no PATH. No pacote Windows, o programa oferece download direto de uma versão fixada do fornecedor, após consentimento.

O fornecedor selecionado utiliza uma distribuição GPLv3. Os metadados, hashes, links de fornecedor, scripts e fontes correspondentes estão no [manifesto de download](../windows_build/client-download-manifest.json). O pacote portátil do aplicativo não contém esses executáveis. Não foram acrescentados binários FFmpeg ou arquivos de cliente à seleção pública.

## Runtime do pacote Windows

A distribuição portátil conserva os avisos e termos dos componentes Python, Tcl/Tk e PyInstaller. O pipeline bloqueia o empacotamento quando notices obrigatórios estão ausentes. Não remova esses arquivos ao distribuir o pacote.

## Imagens e demonstração

A captura da interface veio de um teste sintético real da versão 1.0.3 no Windows. O exemplo MP4 e suas imagens derivadas foram exportados pelo motor do aplicativo a partir de cenas geométricas criadas localmente para esta apresentação. Não usam filmagens, nomes de pessoas ou músicas de cliente. O exemplo não contém áudio.
