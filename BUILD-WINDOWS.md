# Preparação do pacote Windows

## O pacote existente e esta seleção de fontes

A versão portátil histórica 1.0.3 já foi construída e exercitada num runner Windows. A amostra congelada tem 14 MB e SHA-256 `e8112ec35699bfb427df5e443bca7a08500eadc9a87dc8f2745d79436231353c`. Contém identidades anteriores, está preservada apenas como proveniência e não é download do produto renomeado.

O build produz `Gerador-de-Menus.zip`, com pasta `Gerador-de-Menus` e `Gerador-de-Menus.exe`. A identidade das fontes e scripts foi renomeada; catálogo, fontes tipográficas, dependências e manifesto de download permanecem intactos. Um pacote com essa identidade exige build e verificação próprios no Windows x64; não se obtém apenas trocando o nome do ZIP histórico.

## Conteúdo do portátil

O ZIP alvo em formato onedir incluirá executável, runtime Python/Tcl/Tk, recursos tipográficos, manifesto de ferramentas e notices de terceiros. Mantém a pasta `_internal`. As oito fontes livres acompanham seus arquivos OFL; Arial não é redistribuída. O usuário Windows não precisa instalar Python ou Tkinter.

FFmpeg e ffprobe não serão embutidos no ZIP. No primeiro uso, o programa pede consentimento uma vez para o download automático de aproximadamente 195 MB do fornecedor fixado no [manifesto](windows_build/client-download-manifest.json). O cache fica em `tools`, junto da pasta do aplicativo, e é verificado por receipt e hashes antes de ser reutilizado. Um cache íntegro que corresponda ao manifesto desta versão é reutilizável; seu lock legado reconhecido é preservado. A atualização da versão das ferramentas pode exigir nova preparação consentida, sem apagar caches anteriores. Não há alteração do PATH global.

O fornecedor utiliza uma distribuição FFmpeg GPLv3. O manifesto registra URLs de fornecedor, scripts/fontes, tamanho e hashes. Essa estratégia de download não equivale a uma licença nova para o código autoral.

## Pipeline de build

O workflow [.github/workflows/windows-portable.yml](.github/workflows/windows-portable.yml) é somente manual (`workflow_dispatch`), em `windows-2022`, com permissão `contents: read` e checkout sem persistência de credenciais. Não publica release nem faz deploy. Copiar as fontes ou fazer commit não dispara esse workflow.

A configuração preservada usa Python 3.13.17 x64 e PyInstaller 6.16.0. O wheel direto é verificado por hash. Dependências transitivas são registradas, mas não há promessa de reprodutibilidade byte a byte.

A sequência inclui:

1. Validar o manifesto de download e os recursos tipográficos.
2. Preparar dependências de build e notices reais de Python/Tcl/Tk/PyInstaller. Ausência ou corrupção desses termos bloqueia o empacotamento.
3. Preparar o display descartável e as ferramentas para os testes por fontes, fora da distribuição.
4. Executar os testes, incluindo a matriz de render de mídia finita.
5. Gerar o bundle com `windows_build/GeradorMenusVideo.spec` e auditar seus arquivos.
6. Usar `windows_build/smoke_windows.ps1` para extrair uma cópia nova do ZIP e executar duas aberturas reais do executável: preparação inicial com consentimento de download e reabertura offline.
7. Conferir a integridade do ZIP após os testes e conservar o pacote sem reempacotar a extração usada no smoke.

Os gates incluem checksums, limites de extração, exclusão de material privado, notices, recursos das famílias tipográficas, interface real, persistência, render e cancelamento. Logs de CI e mídias grandes de teste não fazem parte desta seleção pública.

## Execução local

O código usa a biblioteca padrão do Python. Para testar por fontes, tenha Python com Tkinter e FFmpeg/ffprobe compatíveis no PATH:

```sh
python -B -m unittest discover -s tests -v
```

Testes sem desktop não substituem os gates nativos. O spec recusa cross-build Linux: o pacote Windows exige Windows x64 e as dependências de build correspondentes. A execução manual de CI e o commit associado identificam qual build realmente passou; não use o pacote histórico como substituto da verificação de uma versão renomeada.

## Limites da evidência

A documentação de [validação](docs/VALIDACAO.md) separa o pacote anterior, a seleção atual de fontes e a demonstração. O histórico tem avisos Tk/Tcl durante testes que concluíram; isso não prova ausência de risco em sessões longas. Reprodução em Windows 10, TVs e USB específicos, uso prolongado e filmagens particulares precisam de validação própria.
