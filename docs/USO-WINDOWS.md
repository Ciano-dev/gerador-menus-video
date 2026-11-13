# Usar o Gerador de menus em vídeo no Windows

## Abrir pela primeira vez

1. Baixe o **pacote Windows** identificado na release. O ZIP do botão **Code** contém o código-fonte, não o programa pronto para abrir.
2. Extraia **a pasta inteira** do ZIP para uma pasta nova do seu computador. Não execute o programa diretamente de dentro do ZIP.
3. Dentro da pasta `Gerador-de-Menus`, abra **`Gerador-de-Menus.exe`**.
4. Na primeira abertura, mantenha a internet conectada. Quando o programa oferecer a preparação das ferramentas de vídeo, clique para aceitar e aguarde terminar. O download é automático, de aproximadamente 194 MB.
5. Quando a janela estiver pronta, adicione seus vídeos e faça uma primeira exportação curta. O [guia de uso](GUIA-DE-USO.md) explica os controles.

**Você não precisa procurar nem instalar Python, FFmpeg ou outras ferramentas manualmente.** Python já está incluído. As ferramentas de vídeo são baixadas pelo próprio programa após sua autorização.

## Nas próximas vezes

Abra o mesmo `Gerador-de-Menus.exe`. As ferramentas ficam salvas em `tools` e podem ser reutilizadas sem internet enquanto o cache estiver íntegro. Se estiver ausente ou inválido, o programa pedirá para prepará-lo novamente.

Mantenha o executável, `_internal`, `tools` e os demais arquivos juntos. Para trocar de versão, extraia o novo pacote numa pasta nova; não misture arquivos `_internal` de versões diferentes. Seus vídeos e projetos `.menu` não precisam ser renomeados. Diretórios antigos de dados não são apagados automaticamente.

O pacote é portátil: não é um instalador tradicional. Como não tem assinatura comercial, o Windows ou navegador pode mostrar um aviso; não há promessa de abertura sem alertas.

## Se aparecer um erro

Guarde uma captura da mensagem e anote o que estava fazendo. Os diagnósticos usam `%LOCALAPPDATA%/GeradorMenusVideo/logs/` quando disponível. Confira um log antes de compartilhá-lo, pois pode conter caminhos do seu computador.

O vídeo exportado não é um menu clicável de DVD. Compatibilidade com uma TV ou equipamento específico precisa ser testada no próprio equipamento.
