# Usar o pacote Windows

## Antes de baixar

A preparação desta apresentação ainda não publicou uma release Windows neste repositório. O pacote portátil identificado é a versão 1.0.3; o ZIP automático de código-fonte do GitHub não substitui essa distribuição.

O arquivo preparado para uma futura release é `Gerador-Menu-Nao-Interativo-1.0.3-Windows-x64.zip`, com SHA-256:

```text
e8112ec35699bfb427df5e443bca7a08500eadc9a87dc8f2745d79436231353c
```

Esse identificador pertence ao pacote existente, não a um novo build desta apresentação. O nome técnico do executável foi preservado para evitar uma reconstrução desnecessária.

## Abrir

1. Extraia **todo o ZIP** numa pasta nova onde você possa gravar arquivos. Não abra apenas o executável de dentro do ZIP.
2. Abra `Sistema-Menu-Anisio.exe`, mantendo a pasta `_internal` e os demais arquivos juntos.
3. Na primeira abertura, leia o pedido de download das ferramentas de vídeo. São aproximadamente 194 MB do fornecedor indicado no manifesto. O download só deve começar depois do seu consentimento; recusar encerra sem baixar.
4. Depois da preparação inicial, o programa reutiliza o cache verificado na pasta `tools`. Não depende de instalar Python nem de mudar o PATH global.
5. Adicione mídias próprias ou de teste e faça primeiro uma exportação curta. O [guia de uso](GUIA-DE-USO.md) descreve os controles.

Use uma pasta nova ao trocar de versão; não misture executáveis e arquivos `_internal` de pacotes diferentes. O arquivo não tem assinatura comercial de código e pode provocar avisos do Windows ou do navegador; não há promessa de download sem alertas.

## Em caso de erro

Anote a ação que estava fazendo e guarde uma captura da mensagem. Os diagnósticos usam `%LOCALAPPDATA%/SistemaMenuAnisio/logs/` quando disponível. Confira os arquivos antes de compartilhar um log: ele pode incluir caminhos do seu computador.

O arquivo gerado não é um menu interativo de DVD. Reprodução em Windows 10, TVs e dispositivos USB específicos exige teste no equipamento; não é garantida pela existência do pacote x64.
